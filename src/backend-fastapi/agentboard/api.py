from . import api_helpers  # Phase 5: shared helpers used by routers
from .api_helpers import (
    _cors_origins, _PERMISSION_RE, _invalidate_stats_cache, _notify_webhooks,
    _need, _current_user, _apply_cors, _optional_user_id, _auth_is_required,
    _require_project_owner, _caller_uid_admin, _enforce_owner_or_admin,
    _enforce_member_or_admin, _resolve_project_id_from_request, _user_response,
    _api_key_response, _probe_cli_sync, _mention_notify, _ext_for_mime,
    _require_admin, _dispatch_proposal, _write_audit_log, request_session,
)

"""AgentBoard REST API（纯 JSON，前后端分离的后端）。

独立运行：uvicorn agentboard.api:app --port 8000
供 Web 前端（fetch）与 MCP（httpx）调用；不含任何 HTML 渲染。
"""
import os
import re
import json
import queue as _queue
import shlex
import subprocess
import asyncio
import threading
import uuid
from datetime import datetime
from contextlib import asynccontextmanager
from sqlalchemy import text
from fastapi import FastAPI, Depends, HTTPException, Query, Header, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from .database import get_session, init_db, SessionLocal
from . import service, auth, mq
from .mq import (
    EVENT_STORY_CREATED, EVENT_STORY_CONFIRMED, EVENT_STORY_READY,
    # Step 4 P1-1（2026-08-10 review）：event 命名空间统一为 entity.action
    EVENT_STORY_REVIEW_REQUESTED, EVENT_STORY_REVIEW_REJECTED,
    EVENT_STORY_REVIEW_VOTE_CAST, EVENT_STORY_COMMENT_REPLIED,
    EVENT_TASK_AVAILABLE, EVENT_TASK_ASSIGNED,
    EVENT_TASK_READY_FOR_REVIEW, EVENT_TASK_REVIEWED, EVENT_TASK_REJECTED,
    EVENT_TASK_REVIEW_REQUESTED, EVENT_TASK_REVIEW_VOTE_CAST,
    publish_workflow_event,
)
from .cos_client import client as _cos_client, CosError
from .models import ALL_TYPES, ALL_STATUSES, ALL_PRIORITIES, ALL_SPRINT_STATUSES, ALL_SCHEDULE_TYPES, ALL_RUN_STATUSES, Status
from .cache import get_cache, API_CACHE_TTL
from .schemas import *  # Phase 5: extracted BaseModel classes (used by router type hints)

@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.validate_runtime_security()
    init_db()
    yield

app = FastAPI(title="AgentBoard API", version="0.2", lifespan=lifespan)

# ---------- Cache Invalidation Helper ----------

# ---------- Webhook 派发 Helper（Epic 122 切片 3 M1） ----------

# 前后端分离：允许 Web 前端跨域调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def require_business_auth(request: Request, call_next):
    """可选统一保护 REST 业务端点，避免新增路由遗漏鉴权依赖。"""
    protected = (
        os.getenv("AGENTBOARD_REQUIRE_AUTH", "0").lower() in {"1", "true", "yes"}
        and request.method != "OPTIONS"
        and request.url.path.startswith("/api/")
        and request.url.path not in {"/api/meta", "/api/health", "/api/auth/register", "/api/auth/login"}
    )
    if protected:
        authorization = request.headers.get("Authorization")
        raw_token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
        uid = auth.parse_token(raw_token)
        api_key_permissions: list[str] | None = None
        # Also support API Key auth: token prefixed with abk_
        if not uid and raw_token and raw_token.startswith(auth.API_KEY_PREFIX):
            digest = auth.hash_api_key(raw_token)
            with request_session(request) as s:
                ak = service.lookup_api_key_by_hash(s, digest)
                uid = ak.user_id if ak and ak.enabled else None
                api_key_permissions = auth.decode_permissions(ak.permissions) if ak and ak.enabled else None
                # Update last_used_at if key is valid
                if uid and ak:
                    service.touch_api_key(s, ak)
        if uid and api_key_permissions is not None:
            required_permission = "api:read" if request.method in {"GET", "HEAD", "OPTIONS"} else "api:write"
            if not auth.permission_allows(api_key_permissions, required_permission):
                return JSONResponse(
                    status_code=403,
                    content={"detail": f"API key requires '{required_permission}' permission"},
                )
        with request_session(request) as s:
            if not uid or service.get_user(s, uid) is None:
                return _apply_cors(request, JSONResponse(status_code=401, content={"detail": "unauthorized"}))
    return await call_next(request)

# ---------- Schemas ----------

# Epic 122 S1：Agent 注册表 + Story 评审闭环

# Epic 122 S3 M2：评审统计与超时护栏

# ---------- New schemas ----------

# ---------- Bulk Operations ----------

# ---------- Agent 状态 WebSocket 广播（2026-08-09） ----------
class AgentStateHub:
    """Agent 状态变更广播中心（订阅者队列模式）。

    - ``subscribe()`` 返回 asyncio.Queue（WS 端点持有，循环读取推送）；
    - ``broadcast()`` 线程安全（同步 REST 端点 / worker probe 上报可随时调用，
      put_nowait 不依赖事件循环，规避跨线程 send 的限制）；
    - 连接数 O(1)：广播复制 payload 投递到各订阅队列。
    """

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def broadcast(self, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(data)
            except Exception:
                pass

    def broadcast_agent(self, agent: dict) -> None:
        self.broadcast({"type": "agent_state", "agent": agent})

    def broadcast_deleted(self, agent_id: str) -> None:
        self.broadcast({"type": "agent_deleted", "agent_id": agent_id})

agent_state_hub = AgentStateHub()

# ---------- Project-scoped access control ----------

# Phase 5: catch the canonical exception classes from core.exceptions.
# features/*/service.py raises these (rather than the old service.NotFound etc.),
# so handlers must match the new classes — otherwise InvalidValue 500s slip out.
from .core.exceptions import (
    NotFound as CoreNotFound,
    Duplicate as CoreDuplicate,
    InvalidValue as CoreInvalidValue,
    IllegalTransition as CoreIllegalTransition,
    DomainError,
)

@app.exception_handler(CoreNotFound)
async def handle_not_found(_request: Request, exc: CoreNotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})

@app.exception_handler(CoreDuplicate)
async def handle_duplicate(_request: Request, exc: CoreDuplicate):
    return JSONResponse(status_code=409, content={"detail": str(exc)})

@app.exception_handler(CoreInvalidValue)
async def handle_invalid_value(_request: Request, exc: CoreInvalidValue):
    return JSONResponse(status_code=422, content={"detail": str(exc)})

@app.exception_handler(CoreIllegalTransition)
async def handle_illegal_transition(_request: Request, exc: CoreIllegalTransition):
    return JSONResponse(status_code=400, content={"detail": str(exc)})

# ---------- Meta ----------

# ---------- Agent 状态 WebSocket（2026-08-09） ----------
@app.websocket("/ws/agents")
async def ws_agents(websocket: WebSocket, token: str | None = Query(None)):
    """Agent 状态实时推送：连上先发全量快照，之后接收 agent_state / agent_deleted。"""
    if _auth_is_required():
        uid = auth.parse_token(token or "")
        if not uid:
            await websocket.close(code=4401)
            return
    await websocket.accept()
    q = agent_state_hub.subscribe()
    try:
        with request_session(websocket) as s:
            snapshot = [service._ser(x) for x in service.list_agents(s)]
        await websocket.send_text(json.dumps(
            {"type": "snapshot", "agents": snapshot}, ensure_ascii=False))
        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout=30)
                await websocket.send_text(data)
            except asyncio.TimeoutError:
                # 保活 ping（默认 30s 空转，nginx/IIS 代理需心跳防断连）
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        pass
    finally:
        agent_state_hub.unsubscribe(q)

# ---------- Tasks ----------

# 兼容旧 import（pre-existing 客户端/MCP 工具可能仍引用）
# 1 release 后下架——届时新代码全部用 TicketRequestSpec。
ProposalTicketIn = TicketRequestSpec
TicketRequestExecuteIn = TicketRequestSpec

def execute_ticket_request_by_id_inner(rid: int, s: Session,
                                       authorization: str | None) -> dict:
    """按显式 request id 执行转换（供测试/前端精确控制）。语义同 execute RPC。"""
    req = service.get_ticket_request(s, rid)
    if not req:
        raise HTTPException(status_code=404, detail=f"ticket request {rid} not found")
    pid = req.proposal_id
    uid, is_admin = _caller_uid_admin(authorization)
    p = service.get_proposal(s, pid)
    if not p:
        raise HTTPException(status_code=404, detail=f"proposal {pid} not found")
    if not is_admin and not service.user_is_project_member(s, p.project_id, uid):
        raise HTTPException(status_code=403, detail="project membership required")
    try:
        result = service.execute_ticket_request(s, pid, request_id=rid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=409 if "正在生成中" in str(e) else 422,
                            detail=str(e))
    mq.publish_workflow_event(mq.EVENT_TICKET_CREATED, "proposal", pid,
                              ref_id=result["request"]["id"])
    return result

def fail_ticket_request_inner(rid: int, error: str, s: Session) -> dict:
    """worker 标记转换失败：request → failed，proposal ticket_preparing → converged。"""
    try:
        req = service.fail_ticket_request(s, rid, error=error)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return service._ser(req)

def claim_ticket_request_inner(rid: int, s: Session) -> dict:
    """**原子**认领转换请求：pending → processing（worker 竞争消费）。"""
    try:
        req = service.claim_ticket_request(s, rid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    if req is None or req.status != "processing":
        raise HTTPException(
            status_code=409,
            detail=f"ticket request {rid} 无法认领：当前状态 "
                   f"{req.status if req else 'unknown'}，仅 pending 可认领",
        )
    return service._ser(req)

# ---------- 兼容层（deprecated，1 release 后下架）----------

@app.middleware("http")
async def audit_log_middleware(request: Request, call_next):
    """记录所有非 health/meta/auth 的 API 请求到审计日志。"""
    import re
    import time
    skip_paths = {"/api/meta", "/api/health", "/api/audit-logs"}
    if request.url.path in skip_paths or not request.url.path.startswith("/api/"):
        return await call_next(request)

    start = time.time()
    # 读取请求体（仅对非 GET 请求）
    body_text = None
    if request.method in {"POST", "PUT", "PATCH"}:
        body_bytes = await request.body()
        body_text = body_bytes.decode("utf-8", errors="replace")
        # 脱敏：移除敏感字段
        body_text = re.sub(r'"password"\s*:\s*"[^"]*"', '"password":"***"', body_text)
        body_text = re.sub(r'"token"\s*:\s*"[^"]*"', '"token":"***"', body_text)
        # 限制长度
        body_text = body_text[:2000] if body_text else None

    response = await call_next(request)

    duration_ms = int((time.time() - start) * 1000)
    # 从响应状态码
    status_code = response.status_code if hasattr(response, "status_code") else None

    # 提取用户 ID
    uid = None
    authorization = request.headers.get("authorization")
    if authorization:
        try:
            with SessionLocal() as audit_session:
                uid = api_helpers.resolve_actor_context(
                    authorization, audit_session,
                ).user_id
        except Exception:
            uid = None

    # 提取实体信息
    path = request.url.path
    entity_type = None
    entity_id = None
    action = request.method
    # 从路径提取实体类型和 ID
    for pattern, etype in [
        (r"^/api/projects/(\d+)", "project"),
        (r"^/api/epics/(\d+)", "epic"),
        (r"^/api/stories/(\d+)", "story"),
        (r"^/api/tasks/(\d+)", "task"),
        (r"^/api/(?:runs|agent-runs)/(\d+)", "run"),
        (r"^/api/comments/(\d+)", "comment"),
        (r"^/api/attachments/(\d+)", "attachment"),
        (r"^/api/dependencies/(\d+)", "dependency"),
        (r"^/api/schedules/(\d+)", "schedule"),
        (r"^/api/documents/(\d+)", "document"),
        (r"^/api/document-comments/(\d+)", "document_comment"),
        (r"^/api/document-folders/(\d+)", "document_folder"),
        (r"^/api/proposals/(\d+)", "proposal"),
        (r"^/api/proposal-questions/(\d+)", "proposal_question"),
    ]:
        m = re.match(pattern, path)
        if m:
            entity_type = etype
            entity_id = int(m.group(1))
            break

    # 异步记录日志：把同步 DB 写入移到线程池，避免阻塞 asyncio 事件循环
    # （此前在 async 中间件里直接 with SessionLocal() 写审计，会阻塞事件循环，
    #  在串行请求场景下（如逐条作答）累积成秒级延迟）。
    try:
        await asyncio.to_thread(
            _write_audit_log, uid, action, entity_type, entity_id, path,
            request, body_text, status_code, duration_ms,
        )
    except Exception:
        pass  # 不阻塞主流程

    return response

@app.middleware("http")
async def project_access_middleware(request: Request, call_next):
    """Enforce project-scoped access control on all /api routes.

    Active only when ``AGENTBOARD_REQUIRE_AUTH=1`` (the Docker / production posture).
    Local open-CRUD mode (``REQUIRE_AUTH=0``) is intentionally left untouched.

    Rules (2026-07-21 — 邀请制):
    - Resolve the target project from the route (direct ``/api/projects/{pid}`` or via a
      child resource such as epic/story/task/sprint/schedule/document, by id or query param).
    - Routes that are not project-scoped pass through.
    - All projects are member-only for non-admin users.
    - System admins (``is_admin``) always pass.
    - Reads (GET/HEAD): requires membership or admin.
    - Writes (POST/PUT/PATCH/DELETE): the project root (settings / deletion) requires the
      owner or an admin; sub-resources require membership or admin.
    """
    if not _auth_is_required():
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    if path in {"/api/meta", "/api/health", "/api/auth/register", "/api/auth/login"}:
        return await call_next(request)

    try:
        pid = _resolve_project_id_from_request(request)
        if pid is None:
            return await call_next(request)

        is_project_root = bool(re.match(r"^/api/projects/\d+/?$", path))
        is_write = request.method not in {"GET", "HEAD"}

        with request_session(request) as s:
            p = service.get_project(s, pid)
            if p is None:
                # Unknown project: let the endpoint return 404.
                return await call_next(request)
            uid, is_admin = _caller_uid_admin(request.headers.get("authorization"), s)
            if _auth_is_required() and uid is None:
                return _apply_cors(request, JSONResponse(status_code=401, content={"detail": "unauthorized"}))

            # All projects are member-only for non-admin users
            if is_admin:
                return await call_next(request)
            if uid is None:
                return _apply_cors(request, JSONResponse(status_code=403, content={"detail": "access denied: project membership required"}))
            if not service.user_is_project_member(s, pid, uid):
                return _apply_cors(request, JSONResponse(status_code=403, content={"detail": "access denied: project membership required"}))

            # Write operations: project root requires owner/admin; sub-resources require membership
            if is_write and is_project_root:
                try:
                    _enforce_owner_or_admin(s, pid, uid, is_admin)
                except HTTPException as e:
                    return _apply_cors(request, JSONResponse(status_code=e.status_code, content={"detail": e.detail}))
            return await call_next(request)
    except HTTPException as e:
        return _apply_cors(request, JSONResponse(status_code=e.status_code, content={"detail": e.detail}))

# ---------------------------------------------------------------------------
# Phase 5: 路由按 feature 拆分后,在 app 上 include_router
# ---------------------------------------------------------------------------
from .features.auth.router import router as auth_router
from .features.projects.router import router as projects_router
from .features.work_items.router import router as work_items_router
from .features.proposals.router import router as proposals_router
from .features.documents.router import router as documents_router
from .features.notifications.router import router as notifications_router
from .features.webhooks.router import router as webhooks_router
from .features.scheduling.router import router as scheduling_router
from .features.search.router import router as search_router
from .features.admin.router import router as admin_router
from .features.learning.router import router as learning_router
from .features.scheduling.behavior_router import router as behavior_router

app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(work_items_router)
app.include_router(proposals_router)
app.include_router(documents_router)
app.include_router(notifications_router)
app.include_router(webhooks_router)
app.include_router(scheduling_router)
app.include_router(search_router)
app.include_router(admin_router)
app.include_router(learning_router)
app.include_router(behavior_router)

