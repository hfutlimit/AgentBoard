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
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from .database import get_session, init_db, SessionLocal
from . import service, auth, mq
from .mq import (
    EVENT_STORY_CREATED, EVENT_STORY_CONFIRMED, EVENT_REVIEW_REQUESTED,
    EVENT_REVIEW_REJECTED, EVENT_REVIEW_VOTE_CAST, EVENT_STORY_READY,
    EVENT_COMMENT_REPLIED, EVENT_TASK_AVAILABLE, EVENT_TASK_ASSIGNED,
    EVENT_TASK_READY_FOR_REVIEW, EVENT_TASK_REVIEWED, EVENT_TASK_REJECTED,
    publish_workflow_event,
)
from .cos_client import client as _cos_client, CosError
from .models import ALL_TYPES, ALL_STATUSES, ALL_PRIORITIES, ALL_SPRINT_STATUSES, ALL_SCHEDULE_TYPES, ALL_RUN_STATUSES, Status
from .cache import get_cache, API_CACHE_TTL


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.validate_runtime_security()
    init_db()
    yield


app = FastAPI(title="AgentBoard API", version="0.2", lifespan=lifespan)

# ---------- Cache Invalidation Helper ----------
def _invalidate_stats_cache(project_id: int) -> None:
    """Invalidate project stats cache when data changes"""
    try:
        cache = get_cache()
        cache.delete(f"stats:{project_id}")
    except Exception:
        pass  # Non-critical, don't fail the request


# ---------- Webhook 派发 Helper（Epic 122 切片 3 M1） ----------
def _notify_webhooks(s: Session, project_id: int, event: str, payload: dict) -> dict:
    """按事件向项目 Webhook 派发（best-effort，任何异常不阻断主业务）。

    与 ``publish_workflow_event``（RabbitMQ 通道）平行：MQ 是 Agent 间的
    事件总线，Webhook 是面向外部系统/常驻 Runner 的 HTTP 通道。事件名
    复用 mq.EVENT_* 常量（语义同构）；payload 只带定位信息（实体 id/status/ref）。
    """
    try:
        return service.fire_webhooks_for_event(
            s, project_id=project_id, event=event, payload=payload)
    except Exception:
        # webhook 派发失败绝不影响主业务成功返回
        return {"matched": 0, "succeeded": 0}

# 前后端分离：允许 Web 前端跨域调用
_cors_origins = [
    x.strip() for x in os.getenv("AGENTBOARD_CORS_ORIGINS", "*").split(",") if x.strip()
]
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
            with SessionLocal() as s:
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
        with SessionLocal() as s:
            if not uid or service.get_user(s, uid) is None:
                return _apply_cors(request, JSONResponse(status_code=401, content={"detail": "unauthorized"}))
    return await call_next(request)


# ---------- Schemas ----------
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    key: str | None = Field(None, max_length=20)
    description: str = ""


class ProjectPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    key: str | None = Field(None, max_length=20)
    description: str | None = None


class EpicIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""


class EpicPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = None
    status: str | None = None


class StoryIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    # Epic 123：是否需要设计评审段（默认 true 走设计评审流）
    needs_design: bool = True


class StoryPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = None
    status: str | None = None
    needs_design: bool | None = None


# Epic 122 S1：Agent 注册表 + Story 评审闭环
class AgentRegisterIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    roles: str = "[]"
    capabilities: str = "[]"
    cli_command: str = ""
    model: str = ""
    auth_key: str = ""


class AgentUpdateIn(BaseModel):
    """前端 Agent 配置中心（PUT /api/agents/{agent_id}，全字段可选）。"""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    roles: str | None = None
    capabilities: str | None = None
    cli_command: str | None = None
    model: str | None = Field(default=None, max_length=100)
    enabled: bool | None = None
    user_id: int | None = None


class AgentHeartbeatIn(BaseModel):
    """Worker probe 上报（可选 body）：probe_ok=False 表示探测失败（置 offline）。"""
    probe_ok: bool | None = None
    probe_message: str = ""


class AgentProbeIn(BaseModel):
    """手动 probe 覆盖（POST /api/agents/{agent_id}/probe，可选）。"""
    timeout: int = Field(default=8, ge=1, le=30)


class AgentReviewIn(BaseModel):
    verdict: str = Field(pattern="^(approve|reject)$")
    comment: str = Field(min_length=1, max_length=2000)


# Epic 122 S3 M2：评审统计与超时护栏
class ReassignTimeoutIn(BaseModel):
    timeout_minutes: int = Field(default=service.DEFAULT_REVIEW_TIMEOUT_MINUTES, ge=1, le=1440)
    max_per_run: int = Field(default=service.DEFAULT_TIMEOUT_SCAN_BATCH, ge=1, le=200)


class TaskIn(BaseModel):
    project_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=300)
    type: str = "task"
    description: str = ""
    spec: str = ""
    priority: str = "medium"
    # Epic 17: 任务管理增强
    assignee_id: int | None = None
    due_date: str | None = None  # ISO date string YYYY-MM-DD
    labels: str = "[]"  # JSON array string
    # Epic 32 Story 49.3: 预估工时（小时）
    estimate: float | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    type: str | None = None
    status: str | None = None
    description: str | None = None
    spec: str | None = None
    priority: str | None = None
    sprint_id: int | None = None
    # Epic 17: 任务管理增强
    assignee_id: int | None = None
    due_date: str | None = None  # ISO date string YYYY-MM-DD
    labels: str | None = None  # JSON array string
    # Epic 32 Story 49.3: 预估工时（小时）
    estimate: float | None = None


class CommentIn(BaseModel):
    author: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)


class StatusIn(BaseModel):
    status: str
    # Epic 123：状态变更原因/备注（写入 task_status_history.reason）
    reason: str = ""


class SpecAppendIn(BaseModel):
    text: str = Field(min_length=1)


class AuthRegister(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("username is required")
        return value


class AuthLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class SprintIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    goal: str = ""
    start_date: str | None = None
    end_date: str | None = None


class SprintPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    goal: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class ScheduleIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    schedule_type: str = "cron"
    cron_expr: str | None = None
    # Story 106：绑定松绑（agent / 固定 task / 可选筛选，全部可选）
    agent: str | None = None
    task_id: int | None = None
    task_priority: str | None = None
    task_type: str | None = None
    epic_id: int | None = None


class SchedulePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    schedule_type: str | None = None
    cron_expr: str | None = None
    enabled: bool | None = None
    next_run_at: str | None = None
    # Story 106：显式 null = 解除绑定 / 清除筛选
    agent: str | None = None
    task_id: int | None = None
    task_priority: str | None = None
    task_type: str | None = None
    epic_id: int | None = None


class RunIn(BaseModel):
    task_id: int | None = None
    idempotency_key: str | None = Field(None, max_length=128)


class RunPatch(BaseModel):
    status: str | None = None
    output: str | None = None
    error_message: str | None = None
    summary: str | None = None
    log_ref: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    task_id: int | None = None


class RunReportIn(BaseModel):
    """Agent 主动报告 run 最终结果（Story 104）"""
    status: str
    summary: str | None = None
    log_ref: str | None = None


# ---------- New schemas ----------
class ProjectPatchExtended(BaseModel):
    """Project PATCH 支持 is_private"""
    name: str | None = Field(None, min_length=1, max_length=200)
    key: str | None = Field(None, max_length=20)
    description: str | None = None
    is_private: bool | None = None


class MemberRoleIn(BaseModel):
    role: str = Field(..., pattern=r"^(owner|member)$")


class NotificationIn(BaseModel):
    user_id: int = Field(gt=0)
    notif_type: str = Field(..., pattern=r"^(project_invite|join_request|task_assigned|status_changed|mentioned)$")
    title: str = Field(min_length=1, max_length=300)
    content: str = ""
    link: str | None = Field(None, max_length=500)


class UserAdminPatch(BaseModel):
    is_admin: bool


class UserProfilePatch(BaseModel):
    display_name: str | None = Field(None, max_length=100)
    email: str | None = Field(None, max_length=254)
    avatar_url: str | None = Field(None, max_length=500)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("invalid email address")
        return normalized

    @field_validator("avatar_url")
    @classmethod
    def validate_avatar_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        if not re.fullmatch(r"https?://[^\s]+", value.strip()):
            raise ValueError("avatar_url must be an http(s) URL")
        return value.strip()


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1000)
    new_password: str = Field(min_length=8, max_length=1000)


_PERMISSION_RE = re.compile(r"^[a-z][a-z0-9_-]*(?::(?:[a-z0-9_*.-]+))+$")


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    permissions: list[str] = Field(default_factory=lambda: ["api:read"], max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name is required")
        return value.strip()

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str]) -> list[str]:
        normalized = sorted(set(value))
        if any(len(p) > 120 or not _PERMISSION_RE.fullmatch(p) for p in normalized):
            raise ValueError("permissions must be namespaced strings such as 'mcp:tools:read'")
        return normalized


class ApiKeyPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    enabled: bool | None = None
    permissions: list[str] | None = Field(None, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("name is required")
        return value.strip() if value is not None else None

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str] | None) -> list[str] | None:
        return ApiKeyCreate.validate_permissions(value) if value is not None else None


# ---------- Bulk Operations ----------
class BulkTaskUpdate(BaseModel):
    task_ids: list[int] = Field(..., min_length=1, max_length=100)
    status: str | None = None
    priority: str | None = None
    sprint_id: int | None = None
    # v3.0 批量指派：新增 assignee_id / clear_assignee（增量字段，向后兼容）
    assignee_id: int | None = None
    clear_assignee: bool = False
    # v3.2 批量改截止日期：新增 due_date / clear_due_date（增量字段，向后兼容）
    due_date: str | None = None
    clear_due_date: bool = False

    @field_validator("task_ids")
    @classmethod
    def validate_ids(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("task_ids cannot be empty")
        if len(set(value)) != len(value):
            raise ValueError("task_ids must be unique")
        return value


class BulkTaskDelete(BaseModel):
    task_ids: list[int] = Field(..., min_length=1, max_length=100)

    @field_validator("task_ids")
    @classmethod
    def validate_ids(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("task_ids cannot be empty")
        if len(set(value)) != len(value):
            raise ValueError("task_ids must be unique")
        return value


def _need(obj, what: str):
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return obj


def _current_user(
    authorization: str | None, s: Session, *, required_permission: str | None = None,
):
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token)
    if not uid and token and token.startswith(auth.API_KEY_PREFIX):
        item = service.lookup_api_key_by_hash(s, auth.hash_api_key(token))
        if item and item.enabled:
            permissions = auth.decode_permissions(item.permissions)
            if required_permission and not auth.permission_allows(permissions, required_permission):
                raise HTTPException(status_code=403, detail=f"API key requires '{required_permission}' permission")
            uid = item.user_id
    u = service.get_user(s, uid) if uid else None
    if u is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return u


def _apply_cors(request: Request, resp: JSONResponse) -> JSONResponse:
    """为 middleware 早返回的 JSONResponse 注入 CORS 头（防 CORS 拦截致 0 status）。

    FastAPI 的 CORSMiddleware 只对经过路由的响应补 CORS 头；从 middleware 直接
    return 的 JSONResponse 会绕过该步骤，浏览器看到 4xx 没 CORS 头会判定为
    "0 Unknown Error" 而非 403，导致前端无法正确处理。
    """
    origin = request.headers.get("origin")
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Vary"] = "Origin"
    return resp


def _optional_user_id(authorization: str | None, s: Session) -> int | None:
    if not authorization:
        return None
    return _current_user(authorization, s, required_permission="api:read").id


def _auth_is_required() -> bool:
    return os.getenv("AGENTBOARD_REQUIRE_AUTH", "0").lower() in {"1", "true", "yes"}


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


def _require_project_owner(
    s: Session, project_id: int, authorization: str | None,
) -> None:
    # Explicitly preserve the documented local open-CRUD mode when no identity is supplied.
    if not authorization and not _auth_is_required():
        return
    user = _current_user(authorization, s, required_permission="api:write")
    if not user.is_admin and not service.user_is_project_owner(s, project_id, user.id):
        raise HTTPException(status_code=403, detail="project owner or admin required")


# ---------- Project-scoped access control ----------
def _caller_uid_admin(authorization: str | None) -> tuple[int | None, bool]:
    """Resolve ``(user_id, is_admin)`` from the Authorization header.

    Handles both Bearer user tokens and ``abk_`` API keys. Returns ``(None, False)``
    when no valid credential is present.
    """
    if not authorization:
        return None, False
    token = authorization.split(" ", 1)[1] if authorization.startswith("Bearer ") else None
    if not token:
        return None, False
    uid = auth.parse_token(token)
    if not uid and token.startswith(auth.API_KEY_PREFIX):
        with SessionLocal() as s:
            ak = service.lookup_api_key_by_hash(s, auth.hash_api_key(token))
            if ak and ak.enabled:
                uid = ak.user_id
    if uid is None:
        return None, False
    with SessionLocal() as s:
        u = service.get_user(s, uid)
        return uid, bool(u and u.is_admin)


def _enforce_owner_or_admin(s: Session, project_id: int, uid: int | None, is_admin: bool) -> None:
    if is_admin:
        return
    if not uid or not service.user_is_project_owner(s, project_id, uid):
        raise HTTPException(status_code=403, detail="project owner or admin required")


def _enforce_member_or_admin(s: Session, project_id: int, uid: int | None, is_admin: bool) -> None:
    if is_admin:
        return
    if not uid or not service.user_is_project_member(s, project_id, uid):
        raise HTTPException(status_code=403, detail="project membership required")


def _resolve_project_id_from_request(request: Request) -> int | None:
    """Map a request to the project it targets, or ``None`` if not project-scoped."""
    path = request.url.path
    m = re.match(r"^/api/projects/(\d+)", path)
    if m:
        return int(m.group(1))
    qp = request.query_params
    with SessionLocal() as s:
        m = re.match(r"^/api/epics/(\d+)", path)
        if m:
            return service.get_epic_project_id(s, int(m.group(1)))
        m = re.match(r"^/api/stories/(\d+)", path)
        if m:
            return service.get_story_project_id(s, int(m.group(1)))
        m = re.match(r"^/api/tasks/(\d+)", path)
        if m:
            return service.get_task_project_id(s, int(m.group(1)))
        m = re.match(r"^/api/sprints/(\d+)", path)
        if m:
            return service.get_sprint_project_id(s, int(m.group(1)))
        m = re.match(r"^/api/schedules/(\d+)", path)
        if m:
            return service.get_schedule_project_id(s, int(m.group(1)))
        m = re.match(r"^/api/comments/(\d+)", path)
        if m:
            return service.get_comment_project_id(s, int(m.group(1)))
        m = re.match(r"^/api/attachments/(\d+)", path)
        if m:
            return service.get_attachment_project_id(s, int(m.group(1)))
        m = re.match(r"^/api/dependencies/(\d+)", path)
        if m:
            return service.get_dependency_project_id(s, int(m.group(1)))
        if "project_id" in qp:
            return int(qp["project_id"])
        if "epic_id" in qp:
            return service.get_epic_project_id(s, int(qp["epic_id"]))
        if "story_id" in qp:
            return service.get_story_project_id(s, int(qp["story_id"]))
        if "sprint_id" in qp:
            sp = s.get(Sprint, int(qp["sprint_id"]))
            return sp.project_id if sp else None
        if path == "/api/webhooks" or path.startswith("/api/webhooks/"):
            if "project_id" in qp:
                return int(qp["project_id"])
            m = re.match(r"^/api/webhooks/(\d+)", path)
            if m:
                return service.get_webhook_project_id(s, int(m.group(1)))
        # Documents（Epic 15）
        m = re.match(r"^/api/documents/(\d+)", path)
        if m:
            return service.get_document_project_id(s, int(m.group(1)))
        m = re.match(r"^/api/document-comments/(\d+)", path)
        if m:
            return service.get_document_comment_project_id(s, int(m.group(1)))
        # Document folders（Epic 15 增强：文件夹/子文件夹）
        if path == "/api/document-folders":
            if "project_id" in qp:
                return int(qp["project_id"])
            # 未指定 project_id 时仅返回有权限项目的文件夹 → 不绑定项目，放行
            return None
        m = re.match(r"^/api/document-folders/(\d+)", path)
        if m:
            return service.get_document_folder_project_id(s, int(m.group(1)))
        # Proposals（Epic 96 P0）— /api/proposals/pending 为 Worker 轮询端点，不绑项目
        m = re.match(r"^/api/proposals/(\d+)", path)
        if m:
            return service.get_proposal_project_id(s, int(m.group(1)))
    return None


def _user_response(user) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "avatar_url": user.avatar_url,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
    }


@app.exception_handler(service.NotFound)
async def handle_not_found(_request: Request, exc: service.NotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(service.Duplicate)
async def handle_duplicate(_request: Request, exc: service.Duplicate):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(service.InvalidValue)
async def handle_invalid_value(_request: Request, exc: service.InvalidValue):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(service.IllegalTransition)
async def handle_illegal_transition(_request: Request, exc: service.IllegalTransition):
    return JSONResponse(status_code=400, content={"detail": str(exc)})



# ---------- Meta ----------
@app.get("/api/meta")
def meta():
    return {"types": ALL_TYPES, "statuses": ALL_STATUSES, "priorities": ALL_PRIORITIES,
            "sprint_statuses": ALL_SPRINT_STATUSES,
            "schedule_types": ALL_SCHEDULE_TYPES, "run_statuses": ALL_RUN_STATUSES}


# ---------- Health ----------
@app.get("/api/health")
def health(s: Session = Depends(get_session)):
    """健康检查端点：探测 DB 连接、API 版本。不需要鉴权。"""
    db_status = "ok"
    try:
        s.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"
    return {
        "status": "ok",
        "database": db_status,
        "version": "0.4",
        "timestamp": datetime.now().isoformat(),
    }


# ---------- Auth ----------
@app.post("/api/auth/register", status_code=201)
def register(body: AuthRegister, s: Session = Depends(get_session)):
    registration_open = os.getenv("AGENTBOARD_ALLOW_REGISTRATION", "1").lower() in {"1", "true", "yes"}
    if not registration_open and service.has_users(s):
        raise HTTPException(status_code=403, detail="registration is disabled")
    try:
        u = service.register_user(s, username=body.username, password=body.password)
    except service.Duplicate:
        raise HTTPException(status_code=409, detail=f"username '{body.username}' already exists")
    return {"id": u.id, "username": u.username, "is_admin": u.is_admin, "token": auth.make_token(u.id)}


@app.post("/api/auth/login")
def login(body: AuthLogin, s: Session = Depends(get_session)):
    u = service.authenticate_user(s, username=body.username, password=body.password)
    if u is None:
        raise HTTPException(status_code=401, detail="invalid username or password")
    return {"id": u.id, "username": u.username, "is_admin": u.is_admin, "token": auth.make_token(u.id)}


@app.get("/api/auth/me")
def me(authorization: str | None = Header(None), s: Session = Depends(get_session)):
    return _user_response(_current_user(authorization, s, required_permission="api:read"))


@app.patch("/api/auth/me")
def update_me(
    body: UserProfilePatch,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    user = _current_user(authorization, s, required_permission="api:write")
    updated = service.update_user_profile(user=user, s=s, **body.model_dump(exclude_unset=True))
    return _user_response(updated)


@app.post("/api/auth/change-password", status_code=204)
def change_password(
    body: PasswordChange,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    user = _current_user(authorization, s, required_permission="api:write")
    try:
        service.change_user_password(
            s, user, current_password=body.current_password, new_password=body.new_password,
        )
    except service.InvalidValue as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _api_key_response(item) -> dict:
    return {
        "id": item.id, "name": item.name, "prefix": item.key_prefix,
        "permissions": auth.decode_permissions(item.permissions), "enabled": item.enabled,
        "created_at": item.created_at, "updated_at": item.updated_at,
        "last_used_at": item.last_used_at,
    }


@app.post("/api/api-keys", status_code=201)
def create_api_key(body: ApiKeyCreate, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    user = _current_user(authorization, s)
    item, plaintext = service.create_api_key(
        s, user_id=user.id, name=body.name, permissions=body.permissions,
    )
    return {**_api_key_response(item), "key": plaintext}


@app.get("/api/api-keys")
def list_api_keys(authorization: str | None = Header(None), s: Session = Depends(get_session)):
    user = _current_user(authorization, s)
    return {"items": [_api_key_response(x) for x in service.list_api_keys(s, user_id=user.id)]}


@app.get("/api/api-keys/{api_key_id}")
def get_api_key(api_key_id: int, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    user = _current_user(authorization, s)
    return _api_key_response(_need(service.get_api_key(s, user_id=user.id, api_key_id=api_key_id), "api key"))


@app.patch("/api/api-keys/{api_key_id}")
def update_api_key(body: ApiKeyPatch, api_key_id: int, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    user = _current_user(authorization, s)
    item = _need(service.get_api_key(s, user_id=user.id, api_key_id=api_key_id), "api key")
    updated = service.update_api_key(
        s, item, name=body.name, enabled=body.enabled, permissions=body.permissions,
    )
    return _api_key_response(updated)


@app.delete("/api/api-keys/{api_key_id}", status_code=204)
def revoke_api_key(api_key_id: int, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    user = _current_user(authorization, s)
    if not service.revoke_api_key(s, user_id=user.id, api_key_id=api_key_id):
        raise HTTPException(status_code=404, detail="api key not found")


# ---------- Projects ----------
@app.get("/api/projects")
def list_projects_ext(
    s: Session = Depends(get_session),
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
    authorization: str | None = Header(None),
):
    """列表 API：admin 可见全部项目；普通用户仅见受邀（成员）项目。

    访问权限由 ``list_accessible_projects`` 中 ``user.is_admin`` 控制。
    API Key（``abk_``）的身份解析完全等同于其关联用户 —— 若 key 属于非管理
    员，则仅返回该用户的成员项目；若属于管理员，则可见全部。
    """
    uid = _optional_user_id(authorization, s)
    projects, total = service.list_accessible_projects(s, uid, limit=limit, offset=offset)
    return {"items": [service._ser(p) for p in projects], "total": total}


@app.get("/api/users/me/projects")
def list_my_projects(
    role: str | None = Query(None, pattern=r"^(owner|member)$"),
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
    authorization: str | None = Header(None), s: Session = Depends(get_session),
):
    user = _current_user(authorization, s, required_permission="api:read")
    rows, total = service.list_user_projects(s, user.id, role=role, limit=limit, offset=offset)
    return {
        "items": [{**service._ser(project), "membership_role": membership_role} for project, membership_role in rows],
        "total": total,
    }


@app.post("/api/projects", status_code=201)
def create_project(
    body: ProjectIn, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    user = _current_user(authorization, s, required_permission="api:write") if authorization or _auth_is_required() else None
    p = service.create_project(s, name=body.name, key=body.key, description=body.description)
    # 创建者自动成为项目 owner；本地显式开放模式仍兼容匿名项目。
    uid = user.id if user else None
    if uid:
        service.add_project_member(s, project_id=p.id, user_id=uid, role="owner")
    return service._ser(p)


@app.get("/api/projects/{pid}")
def get_project_ext(
    pid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """获取项目：admin 可见全部，普通用户仅可见其成员项目（邀请制）"""
    p = _need(service.get_project(s, pid), "project")
    uid = _optional_user_id(authorization, s)
    user = service.get_user(s, uid) if uid else None
    if not (user and user.is_admin) and not service.user_is_project_member(s, pid, uid):
        raise HTTPException(status_code=403, detail="access denied: project membership required")
    return service._ser(p)


@app.patch("/api/projects/{pid}")
def update_project(
    pid: int, body: ProjectPatchExtended, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    _need(service.get_project(s, pid), "project")
    _require_project_owner(s, pid, authorization)
    r = service.update_project(s, pid, **body.model_dump(exclude_none=True))
    return service._ser(_need(r, "project"))


@app.delete("/api/projects/{pid}")
def delete_project(
    pid: int, authorization: str | None = Header(None), s: Session = Depends(get_session),
):
    _need(service.get_project(s, pid), "project")
    _require_project_owner(s, pid, authorization)
    if not service.delete_project(s, pid):
        raise HTTPException(status_code=404, detail="project not found")
    return {"ok": True}


# ---------- Epics ----------
@app.get("/api/projects/{pid}/epics")
def list_epics(pid: int, s: Session = Depends(get_session), limit: int = Query(100, ge=1, le=200),
               offset: int = Query(0, ge=0)):
    return [service._ser(e) for e in service.list_epics(s, pid, limit=limit, offset=offset)]


@app.post("/api/projects/{pid}/epics", status_code=201)
def create_epic(pid: int, body: EpicIn, s: Session = Depends(get_session)):
    _need(service.get_project(s, pid), "project")
    return service._ser(service.create_epic(s, project_id=pid, title=body.title, description=body.description))


@app.get("/api/epics/{eid}")
def get_epic(eid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_epic(s, eid), "epic"))


@app.patch("/api/epics/{eid}")
def update_epic(eid: int, body: EpicPatch, s: Session = Depends(get_session)):
    r = service.update_epic(s, eid, **body.model_dump(exclude_none=True))
    return service._ser(_need(r, "epic"))


@app.delete("/api/epics/{eid}")
def delete_epic(eid: int, s: Session = Depends(get_session)):
    if not service.delete_epic(s, eid):
        raise HTTPException(status_code=404, detail="epic not found")
    return {"ok": True}


# ---------- Stories ----------
@app.get("/api/epics/{eid}/stories")
def list_stories(eid: int, s: Session = Depends(get_session), limit: int = Query(100, ge=1, le=200),
                 offset: int = Query(0, ge=0)):
    return [service._ser(x) for x in service.list_stories(s, eid, limit=limit, offset=offset)]


@app.post("/api/epics/{eid}/stories", status_code=201)
def create_story(eid: int, body: StoryIn, s: Session = Depends(get_session)):
    epic = _need(service.get_epic(s, eid), "epic")
    st = service.create_story(s, epic_id=eid, title=body.title,
                              description=body.description, needs_design=body.needs_design)
    # 事件源：Story 创建广播（分配器 worker 消费后自动指派 reviewer）
    publish_workflow_event(EVENT_STORY_CREATED, "story", st.id, ref_id=eid)
    # Webhook 通道（Epic 122 切片 3）：面向外部系统/常驻 Runner
    _notify_webhooks(s, epic.project_id, EVENT_STORY_CREATED,
                     {"id": st.id, "epic_id": eid, "title": st.title, "status": st.status})
    return service._ser(st)


@app.get("/api/stories/{sid}")
def get_story(sid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_story(s, sid), "story"))


@app.patch("/api/stories/{sid}")
def update_story(sid: int, body: StoryPatch, s: Session = Depends(get_session)):
    r = service.update_story(s, sid, **body.model_dump(exclude_none=True))
    return service._ser(_need(r, "story"))


@app.post("/api/stories/{sid}/confirm")
def confirm_story(sid: int, authorization: str | None = Header(None),
                  s: Session = Depends(get_session)):
    """用户确认 Story 开始（Ticket 全流程人工闸门）：backlog → confirmed。

    确认后发 MQ ``story.confirmed`` 触发 agent 自动处理编排（切片 2 由
    Proposal Worker 轮询拉起 agent）。CAS 幂等：已 confirmed 直接返回。
    """
    uid, _is_admin = _caller_uid_admin(authorization)
    st = service.confirm_story(s, sid, changed_by=uid)
    publish_workflow_event(EVENT_STORY_CONFIRMED, "story", st.id, ref_id=st.epic_id)
    # Agent MQ 编排（2026-08-09）：广播其下 backlog/todo 任务 → 各 agent worker
    # 竞争认领（task.available + 服务端 claim CAS）。MQ 未配置时 no-op，
    # 由 worker 的 story 扫描轮询兜底（handle_story 竞争认领）。
    try:
        for t in service.list_tasks(s, story_id=sid, limit=200):
            if t.status in ("backlog", "todo"):
                publish_workflow_event(EVENT_TASK_AVAILABLE, "task", t.id,
                                       ref_id=sid)
    except Exception:
        log.exception("confirm 广播 task.available 失败（不影响主流程）")
    _epic = s.get(service.Epic, st.epic_id)
    if _epic is not None:
        _notify_webhooks(s, _epic.project_id, EVENT_STORY_CONFIRMED,
                         {"id": st.id, "epic_id": st.epic_id, "status": st.status})
    return service._ser(st)


@app.get("/api/stories/{sid}/status-history")
def story_status_history(sid: int, limit: int = Query(100, ge=1, le=500),
                         s: Session = Depends(get_session)):
    """Story 状态变更历史（Ticket 全流程），按时间倒序。"""
    _need(service.get_story(s, sid), "story")
    rows = service.list_story_status_history(s, sid, limit=limit)
    return {"items": [service._ser(x) for x in rows], "total": len(rows)}


@app.post("/api/stories/{sid}/complete")
def complete_story(sid: int, authorization: str | None = Header(None),
                   s: Session = Depends(get_session)):
    """Story 自动收尾（Ticket 全流程）：任意非 done/blocked → done。

    Worker 在 Story 下全部 task 完成后调用（agent 自动处理收尾）；blocked
    拒绝（人工仲裁态）。幂等：已 done 直接返回。
    """
    uid, _is_admin = _caller_uid_admin(authorization)
    st = service.complete_story(s, sid, changed_by=uid, reason="worker 自动收尾")
    _epic = s.get(service.Epic, st.epic_id)
    if _epic is not None:
        _notify_webhooks(s, _epic.project_id, "story.completed",
                         {"id": st.id, "status": st.status})
    return service._ser(st)


@app.post("/api/stories/{sid}/claim")
def claim_story(sid: int, authorization: str | None = Header(None),
                s: Session = Depends(get_session)):
    """Worker 竞争认领 Story（Ticket 全流程多实例编排）：CAS confirmed → todo。

    多 Worker 实例（不同 agent CLI）竞争同一 confirmed Story 时恰一赢家；
    竞争失败返回 409（已被其它实例认领 / 状态不可认领）。
    """
    uid, _is_admin = _caller_uid_admin(authorization)
    try:
        st = service.claim_story(s, sid, changed_by=uid)
    except service.IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return service._ser(st)


@app.post("/api/stories/{sid}/unclaim")
def unclaim_story(sid: int, authorization: str | None = Header(None),
                  s: Session = Depends(get_session)):
    """Worker 认领交接/失败回退（Ticket 全流程）：CAS todo → confirmed。

    agent 本轮未完成全部任务或失败时回退 confirmed 重新入池；blocked 拒绝。
    """
    uid, _is_admin = _caller_uid_admin(authorization)
    try:
        st = service.unclaim_story(s, sid, changed_by=uid, reason="worker 交接/失败回退")
    except service.IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return service._ser(st)


@app.delete("/api/stories/{sid}")
def delete_story(sid: int, s: Session = Depends(get_session)):
    if not service.delete_story(s, sid):
        raise HTTPException(status_code=404, detail="story not found")
    return {"ok": True}


# ---------- Story 评审闭环（Epic 122 S1） ----------
@app.get("/api/stories")
def list_stories_global(status: str | None = Query(None), reviewer_id: str | None = Query(None),
                        project_id: int | None = Query(None), limit: int = Query(100, ge=1, le=200),
                        offset: int = Query(0, ge=0),
                        authorization: str | None = Header(None),
                        s: Session = Depends(get_session)):
    """全局 Story 列表（供评审任务拉取等场景）。

    - ``?reviewer_id=me`` 解析为当前登录用户，返回指派给我的评审任务；
    - ``?status=pending_review`` 按评审态过滤；
    - ``?project_id=N`` 限定项目（配合 project_access_middleware 权限）。
    """
    uid, _is_admin = _caller_uid_admin(authorization)
    q = s.query(service.Story)
    if status:
        if status not in service.STORY_STATUSES:
            raise HTTPException(status_code=422, detail=f"invalid status '{status}'")
        q = q.filter(service.Story.status == status)
    if reviewer_id:
        if reviewer_id == "me":
            if _auth_is_required() and uid is None:
                raise HTTPException(status_code=401, detail="unauthorized")
            if uid is None:
                raise HTTPException(status_code=422, detail="reviewer_id=me requires login")
            q = q.filter(service.Story.reviewer_id == uid)
        else:
            try:
                q = q.filter(service.Story.reviewer_id == int(reviewer_id))
            except ValueError:
                raise HTTPException(status_code=422, detail="invalid reviewer_id")
    if project_id is not None:
        q = q.join(service.Epic, service.Story.epic_id == service.Epic.id).filter(
            service.Epic.project_id == project_id
        )
    total = q.count()
    items = [service._ser(x) for x in q.order_by(service.Story.id.desc()).limit(limit).offset(offset).all()]
    return {"items": items, "total": total}


@app.post("/api/stories/{sid}/assign-reviewer")
def assign_story_reviewer(sid: int, authorization: str | None = Header(None),
                          s: Session = Depends(get_session)):
    """随机指派评审人（幂等；CAS 并发安全）。项目成员写权限由中间件覆盖。"""
    uid, _is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    st = service.assign_reviewer(s, sid, user_id=uid)
    # 事件源：指派成功 → review.requested 定向投递给 reviewer 的 Agent 队列
    # （reviewer 是 users.id；找到其绑定的 Agent 才能定向，否则退化为广播）
    reviewer_agent_id = None
    if st.reviewer_id is not None:
        agent = s.query(service.Agent).filter(service.Agent.user_id == st.reviewer_id).first()
        if agent is not None:
            reviewer_agent_id = agent.agent_id
    publish_workflow_event(EVENT_REVIEW_REQUESTED, "story", st.id,
                           ref_id=st.reviewer_id, agent_id=reviewer_agent_id)
    _epic = s.get(service.Epic, st.epic_id)
    if _epic is not None:
        _notify_webhooks(s, _epic.project_id, EVENT_REVIEW_REQUESTED,
                         {"id": st.id, "reviewer_id": st.reviewer_id, "status": st.status})
    return service._ser(st)


@app.post("/api/stories/{sid}/review")
def review_story(sid: int, body: AgentReviewIn, authorization: str | None = Header(None),
                 s: Session = Depends(get_session)):
    """评审投票（approve/reject + 评论，CAS）：仅被指派 reviewer 可操作。"""
    uid, _is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if uid is None:
        raise HTTPException(status_code=422, detail="review requires login")
    before = s.get(service.Story, sid)
    before_round = (before.review_round or 0) if before is not None else 0
    st = service.review_story(s, story_id=sid, reviewer_user_id=uid,
                              verdict=body.verdict, comment=body.comment)
    # 事件源：结算判定 ——
    # ready → story.ready（评审通过，开发分配入口）；
    # blocked / round 增加 → review.rejected（护栏终态 / single reject /
    #   majority 多数驳回待收敛）；
    # 其余（majority 投票未达法定票数，状态与轮次均未变）→ review.vote_cast。
    if st.status == "ready":
        event = EVENT_STORY_READY
    elif st.status == "blocked" or (st.review_round or 0) > before_round:
        event = EVENT_REVIEW_REJECTED
    else:
        event = EVENT_REVIEW_VOTE_CAST
    if event == EVENT_STORY_READY:
        ref_id = st.reviewer_id
    elif event == EVENT_REVIEW_VOTE_CAST:
        ref_id = uid
    else:
        ref_id = st.review_round
    publish_workflow_event(event, "story", st.id, ref_id=ref_id)
    # Webhook 通道（Epic 122 切片 3）
    _epic = s.get(service.Epic, st.epic_id)
    if _epic is not None:
        _notify_webhooks(s, _epic.project_id, event,
                         {"id": st.id, "status": st.status, "reviewer_id": st.reviewer_id,
                          "review_round": st.review_round})
    return service._ser(st)


# ---------- Agents（Epic 122 S1 + 2026-08-09 配置中心化） ----------
@app.post("/api/agents/register", status_code=201)
def register_agent(body: AgentRegisterIn, authorization: str | None = Header(None),
                   s: Session = Depends(get_session)):
    """注册/更新 Agent 身份（幂等，MCP/agent 自报入口）。绑定当前认证用户。"""
    uid, _is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    agent = service.register_agent(s, agent_id=body.agent_id, name=body.name,
                                   roles=body.roles, capabilities=body.capabilities,
                                   cli_command=body.cli_command, model=body.model,
                                   auth_key=body.auth_key, user_id=uid)
    agent_state_hub.broadcast_agent(service._ser(agent))
    return service._ser(agent)


@app.put("/api/agents/{agent_id}")
def update_agent(agent_id: str, body: AgentUpdateIn,
                 authorization: str | None = Header(None),
                 s: Session = Depends(get_session)):
    """前端 Agent 配置中心：更新名称/角色/CLI 模板/模型/启用状态（全字段可选）。"""
    uid, is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    agent = service.get_agent_by_agent_id(s, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    if not is_admin and agent.user_id not in (None, uid):
        raise HTTPException(status_code=403, detail="agent belongs to another user")
    agent = service.update_agent(s, agent_id, **body.model_dump(exclude_none=True))
    agent_state_hub.broadcast_agent(service._ser(agent))
    return service._ser(agent)


@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: str, authorization: str | None = Header(None),
                 s: Session = Depends(get_session)):
    """删除 Agent 注册记录（前端配置中心）。"""
    uid, is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    agent = service.get_agent_by_agent_id(s, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    if not is_admin and agent.user_id not in (None, uid):
        raise HTTPException(status_code=403, detail="agent belongs to another user")
    service.delete_agent(s, agent_id)
    agent_state_hub.broadcast_deleted(agent_id)
    return {"ok": True}


@app.post("/api/agents/{agent_id}/heartbeat")
def agent_heartbeat(agent_id: str, body: AgentHeartbeatIn | None = None,
                    authorization: str | None = Header(None),
                    s: Session = Depends(get_session)):
    """Agent 心跳保活（置在线）。Worker probe 带 probe_ok/probe_message 上报详情。"""
    uid, _is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    probe_ok = body.probe_ok if body else None
    probe_message = body.probe_message if body else ""
    agent = service.agent_heartbeat(s, agent_id, user_id=uid,
                                    probe_ok=probe_ok, probe_message=probe_message)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    agent_state_hub.broadcast_agent(service._ser(agent))
    return service._ser(agent)


@app.post("/api/agents/{agent_id}/deregister")
def agent_deregister(agent_id: str, body: AgentHeartbeatIn | None = None,
                     authorization: str | None = Header(None),
                     s: Session = Depends(get_session)):
    """Agent 注销下线（自身或 admin）。Worker probe 失败带 probe_message 原因。"""
    uid, is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    probe_message = body.probe_message if body else ""
    agent = service.agent_deregister(s, agent_id, user_id=uid, is_admin=is_admin,
                                     probe_message=probe_message)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    agent_state_hub.broadcast_agent(service._ser(agent))
    return service._ser(agent)


@app.post("/api/agents/{agent_id}/probe")
def probe_agent(agent_id: str, body: AgentProbeIn | None = None,
                authorization: str | None = Header(None),
                s: Session = Depends(get_session)):
    """手动探测 Agent CLI（前端「立即探测」）：同步跑 ``<cmd> --version`` 判活。

    与 Worker 定期 probe 语义一致（{model} 占位符替换 + 结果落 probe_message）。
    """
    uid, _is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    agent = service.get_agent_by_agent_id(s, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    timeout = body.timeout if body else 8
    ok, msg = _probe_cli_sync(agent.cli_command, model=agent.model, timeout=timeout)
    agent = service.agent_heartbeat(s, agent_id, user_id=uid,
                                    probe_ok=ok, probe_message=msg)
    agent_state_hub.broadcast_agent(service._ser(agent))
    return service._ser(agent)


@app.get("/api/agents")
def list_agents(online: bool | None = Query(None), role: str | None = Query(None),
                s: Session = Depends(get_session)):
    """列出已注册 Agent（?online=true&role=reviewer 过滤）。"""
    return [service._ser(x) for x in service.list_agents(s, online=online, role=role)]


def _probe_cli_sync(cmd: str, *, model: str = "", timeout: int = 8) -> tuple[bool, str]:
    """同步 CLI 探测（手动 probe / API 侧）：``<cmd> --version`` 判活。

    与 worker._probe_cli 同语义：{model} 占位符替换（空则移除）；Windows .cmd
    包装 OSError 时退化 ``cmd /c`` 执行（WinError 193 同款坑）。
    """
    from .worker import split_command
    full = (cmd or "").strip().replace("{model}", (model or "").strip())
    if not full.strip():
        return False, "未配置 cli_command"
    if "{model}" in full:
        full = full.replace("{model}", "").strip()
    try:
        argv = split_command(full) + ["--version"]
    except ValueError as e:
        return False, f"命令解析失败：{e}"
    for use_cmd in (False, True):
        run_argv = (["cmd", "/c"] + argv) if use_cmd else argv
        try:
            proc = subprocess.run(run_argv, capture_output=True, text=True,
                                  timeout=timeout, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return False, f"探测超时 {timeout}s"
        except (OSError, ValueError) as e:
            if use_cmd:
                return False, f"无法启动 CLI：{e}"
            continue  # 退化 cmd /c 再试一次
        ok = proc.returncode == 0
        detail = ""
        if (proc.stdout or "").strip():
            detail = proc.stdout.strip().splitlines()[0][:80]
        elif (proc.stderr or "").strip():
            detail = proc.stderr.strip().splitlines()[-1][:80]
        msg = (f"OK {detail}" if ok else f"exit={proc.returncode} {detail}").strip()
        return ok, msg or ("OK" if ok else f"exit={proc.returncode}")
    return False, "无法启动 CLI"


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
        with SessionLocal() as s:
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
@app.get("/api/stories/{sid}/tasks")
def list_tasks(sid: int, s: Session = Depends(get_session), limit: int = Query(100, ge=1, le=200),
               offset: int = Query(0, ge=0), sprint_id: int | None = Query(None)):
    q_base = service.query_task_count(s, sid, sprint_id=sprint_id)
    total = q_base
    items = [service._ser(t) for t in service.list_tasks(s, sid, sprint_id=sprint_id, limit=limit, offset=offset)]
    return {"items": items, "total": total}


@app.post("/api/stories/{sid}/tasks", status_code=201)
def create_task(
    sid: int, body: TaskIn, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    story = _need(service.get_story(s, sid), "story")
    try:
        t = service.create_task(s, project_id=body.project_id, story_id=story.id,
                                title=body.title, type=body.type,
                                description=body.description, spec=body.spec,
                                priority=body.priority,
                                assignee_id=body.assignee_id,
                                due_date=body.due_date,
                                labels=body.labels,
                                estimate=body.estimate)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    _invalidate_stats_cache(body.project_id)
    if t.assignee_id is not None:
        service.create_notification(
            s, user_id=t.assignee_id, notif_type="task_assigned",
            title=f"任务 #{t.id} 已分配给你", content=t.title, link=f"/task/{t.id}",
        )
    return service._ser(t)


# ---------- Enhanced Search (must be before /api/tasks/{tid}) ----------
@app.get("/api/tasks/search")
def search_tasks_enhanced_api(
    project_id: int | None = None,
    epic_id: int | None = None,
    story_id: int | None = None,
    sprint_id: int | None = None,
    type: str | None = None,
    status: str | list[str] | None = None,
    priority: str | list[str] | None = None,
    q: str | None = Query(None),
    sort_by: str = Query("id", description="Sort field: id, created_at, updated_at, priority, status, title"),
    sort_order: str = Query("desc", pattern=r"^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
):
    """增强搜索：支持多值过滤（status[]=xx&status[]=yy）和排序。"""
    try:
        rows = service.search_tasks_enhanced(
            s, project_id=project_id, epic_id=epic_id, story_id=story_id,
            sprint_id=sprint_id, type=type, status=status, priority=priority,
            q=q, sort_by=sort_by, sort_order=sort_order, limit=limit, offset=offset,
        )
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return [service._ser(t) for t in rows]


@app.get("/api/tasks/{tid}")
def get_task(tid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_task(s, tid), "task"))


@app.patch("/api/tasks/{tid}")
def update_task(
    tid: int, body: TaskPatch, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    task = service.get_task(s, tid)
    pid = task.project_id if task else None
    old_assignee_id = task.assignee_id if task else None
    old_status = task.status if task else None
    try:
        r = service.update_task(s, tid, **body.model_dump(exclude_unset=True))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    if pid:
        _invalidate_stats_cache(pid)
    updated = _need(r, "task")
    if updated.assignee_id is not None and updated.assignee_id != old_assignee_id:
        service.create_notification(
            s, user_id=updated.assignee_id, notif_type="task_assigned",
            title=f"任务 #{updated.id} 已分配给你", content=updated.title,
            link=f"/task/{updated.id}",
        )
        # Agent MQ 定向投递（2026-08-09）：任务显式指派给某 agent 用户 →
        # 发到该 agent 的 direct queue（task.assigned），对应 worker 独享消费。
        _agent = s.query(service.Agent).filter(
            service.Agent.user_id == updated.assignee_id).first()
        if _agent is not None and _agent.agent_id:
            publish_workflow_event(EVENT_TASK_ASSIGNED, "task", updated.id,
                                   ref_id=updated.story_id,
                                   agent_id=_agent.agent_id)
    if updated.assignee_id is not None and updated.status != old_status:
        service.create_notification(
            s, user_id=updated.assignee_id, notif_type="status_changed",
            title=f"任务 #{updated.id} 状态已变更", content=f"{updated.title}：{old_status} → {updated.status}",
            link=f"/task/{updated.id}",
        )
    return service._ser(updated)


@app.put("/api/tasks/{tid}/status")
def set_status(
    tid: int, body: StatusIn, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    task = service.get_task(s, tid)
    pid = task.project_id if task else None
    old_status = task.status if task else None
    uid, _is_admin = _caller_uid_admin(authorization)
    try:
        result = service.set_status(s, tid, body.status, changed_by=uid, reason=body.reason)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.IllegalTransition as e:
        raise HTTPException(status_code=400, detail=str(e))
    if pid:
        _invalidate_stats_cache(pid)
    if result.assignee_id is not None and result.status != old_status:
        service.create_notification(
            s, user_id=result.assignee_id, notif_type="status_changed",
            title=f"任务 #{result.id} 状态已变更", content=f"{result.title}：{old_status} → {result.status}",
            link=f"/task/{result.id}",
        )
    return service._ser(result)


@app.get("/api/tasks/{tid}/status-history")
def get_task_status_history(tid: int, authorization: str | None = Header(None),
                            s: Session = Depends(get_session)):
    """任务状态变更历史（Epic 123）：from_status → to_status、操作人、原因、时间，倒序。"""
    _need(service.get_task(s, tid), "task")
    return [service._ser(h) for h in service.list_task_status_history(s, tid)]


@app.post("/api/tasks/{tid}/claim")
def claim_task_for_development(tid: int, authorization: str | None = Header(None),
                               s: Session = Depends(get_session)):
    """开发任务竞争认领（Epic 122 切片 2，CAS 并发安全）。

    条件 UPDATE ``status IN (backlog, todo)`` → ``in_progress + assignee_id=当前用户``，
    rowcount=1 才成功；已认领/已结束返回 409 明确错误（复用 Epic 118 护栏语义）。
    项目写权限由 project_access_middleware 自动覆盖。
    """
    uid, _is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if uid is None:
        raise HTTPException(status_code=422, detail="claim requires login")
    try:
        t = service.claim_development_task(s, tid, user_id=uid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=409, detail=str(e))
    _invalidate_stats_cache(t.project_id)
    return service._ser(t)


@app.post("/api/tasks/{tid}/submit-review")
def submit_task_review(tid: int, authorization: str | None = Header(None),
                       s: Session = Depends(get_session)):
    """开发完成提交评审（Epic 122 切片 2）：assignee 或 admin 操作。

    - 校验 status=in_progress + assignee 匹配（admin 豁免）→ in_review；
    - 成功 → 广播 ``task.ready_for_review``（分配器 worker 消费，切片 2 M2 指派 reviewer）。
    """
    uid, is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if uid is None:
        raise HTTPException(status_code=422, detail="submit-review requires login")
    try:
        t = service.submit_task_for_review(s, tid, user_id=uid, is_admin=is_admin)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    _invalidate_stats_cache(t.project_id)
    # 事件源：任务进入评审态 → 广播（消息只带定位信息，状态回查 DB）
    publish_workflow_event(EVENT_TASK_READY_FOR_REVIEW, "task", t.id,
                           ref_id=t.assignee_id)
    _notify_webhooks(s, t.project_id, EVENT_TASK_READY_FOR_REVIEW,
                     {"id": t.id, "assignee_id": t.assignee_id, "status": t.status})
    return service._ser(t)


@app.post("/api/tasks/{tid}/assign-reviewer")
def assign_task_reviewer(tid: int, authorization: str | None = Header(None),
                         s: Session = Depends(get_session)):
    """随机指派 Task 评审人（幂等，CAS 并发安全，Epic 122 切片 2 M2）。

    - 候选 = 在线 reviewer ∩ 项目成员 ∩ ≠ assignee；无候选 → 422；
    - 成功 → 定向投递 review.requested（entity_type=task）给 reviewer 绑定的
      Agent 队列（无 Agent 绑定退化为广播，开发者轮询 list_review_tasks 兜底）。
    项目写权限由 project_access_middleware 自动覆盖。
    """
    uid, _is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        t = service.assign_task_reviewer(s, tid, user_id=uid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    _invalidate_stats_cache(t.project_id)
    # 事件源：指派成功 → review.requested（定向 reviewer agent；无绑定退广播）
    reviewer_agent_id = None
    if t.reviewer_id is not None:
        agent = s.query(service.Agent).filter(service.Agent.user_id == t.reviewer_id).first()
        if agent is not None:
            reviewer_agent_id = agent.agent_id
    publish_workflow_event(EVENT_REVIEW_REQUESTED, "task", t.id,
                           ref_id=t.reviewer_id, agent_id=reviewer_agent_id)
    _notify_webhooks(s, t.project_id, EVENT_REVIEW_REQUESTED,
                     {"id": t.id, "reviewer_id": t.reviewer_id, "status": t.status})
    return service._ser(t)


@app.post("/api/tasks/{tid}/review")
def review_task(tid: int, body: AgentReviewIn, authorization: str | None = Header(None),
                s: Session = Depends(get_session)):
    """Task 评审投票（approve/reject + 评论，CAS）：仅被指派 reviewer 可操作。

    - approve → in_review→done，广播 ``task.reviewed``；
    - reject → review_round+1，退回 in_progress（开发者修复后重新 submit-review），
      达 5 轮上限 → blocked 护栏；广播 ``task.rejected``（ref_id=轮次）。
    项目写权限由 project_access_middleware 自动覆盖。
    """
    uid, _is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if uid is None:
        raise HTTPException(status_code=422, detail="review requires login")
    try:
        before = s.get(service.Task, tid)
        before_round = (before.review_round or 0) if before is not None else 0
        t = service.review_task(s, task_id=tid, reviewer_user_id=uid,
                                verdict=body.verdict, comment=body.comment)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    _invalidate_stats_cache(t.project_id)
    # 事件源：结算判定（语义同 Story 版）——
    # done → task.reviewed；blocked / round 增加 → task.rejected；
    # 其余（majority 投票未达法定票数）→ review.vote_cast。
    if t.status == Status.DONE:
        event = EVENT_TASK_REVIEWED
    elif t.status == Status.BLOCKED or (t.review_round or 0) > before_round:
        event = EVENT_TASK_REJECTED
    else:
        event = EVENT_REVIEW_VOTE_CAST
    # ref_id 语义：task.reviewed / vote_cast → 投票人 uid（既有契约）；
    # task.rejected → 评审轮次
    if event in (EVENT_TASK_REVIEWED, EVENT_REVIEW_VOTE_CAST):
        ref_id = uid
    else:
        ref_id = t.review_round
    publish_workflow_event(event, "task", t.id, ref_id=ref_id)
    # Webhook 通道（Epic 122 切片 3）
    _notify_webhooks(s, t.project_id, event,
                     {"id": t.id, "status": t.status, "reviewer_id": t.reviewer_id,
                      "review_round": t.review_round})
    return service._ser(t)


@app.delete("/api/tasks/{tid}")
def delete_task(tid: int, s: Session = Depends(get_session)):
    task = service.get_task(s, tid)
    pid = task.project_id if task else None
    if not service.delete_task(s, tid):
        raise HTTPException(status_code=404, detail="task not found")
    if pid:
        _invalidate_stats_cache(pid)
    return {"ok": True}


# ---------- 评审统计与超时护栏（Epic 122 S3 M2） ----------
@app.get("/api/review-stats")
def review_stats(project_id: int, days: int = 7, user_id: int | None = None,
                 authorization: str | None = Header(None),
                 s: Session = Depends(get_session)):
    """项目级评审统计运营视图（S3 M2）。

    权限：project_access_middleware 经 ?project_id= 解析项目 → 项目成员可读
    （公开项目读开放 / admin 全局绕过）。
    """
    uid, _is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        return service.get_review_stats(s, project_id=project_id, days=days,
                                        user_id=user_id)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/review-stats/reassign-timeout")
def reassign_timeout(project_id: int | None = None,
                     body: ReassignTimeoutIn | None = None,
                     authorization: str | None = Header(None),
                     s: Session = Depends(get_session)):
    """评审超时自愈扫描（S3 M2 护栏）：超时 pending_review Story / in_review Task →
    轮次上限 blocked，否则 CAS 解绑重派。

    权限：带 ?project_id= 时由 project_access_middleware 解析 → 项目成员写；
    不带时（Worker 全局自愈扫描）放行 —— 幂等 + 有界 + 不读敏感数据，仅做评审指派
    自愈，任意已认证用户可触发（Worker 以服务账号调用）。
    重派成功的实体发布 review.requested（定向新 reviewer agent 队列退广播）+
    Webhook 通道，与既有 assign-reviewer 端点事件语义一致。
    """
    if body is None:
        body = ReassignTimeoutIn()
    uid, _is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    result = service.scan_review_timeouts(
        s, project_id=project_id,
        timeout_minutes=body.timeout_minutes,
        max_per_run=body.max_per_run)
    # 事件源：重派成功的 Story/Task 逐个发布 review.requested（定向退广播）+ Webhook
    for entity_type, rows in (("story", result.get("_stories_reassigned") or []),
                              ("task", result.get("_tasks_reassigned") or [])):
        for eid, new_reviewer_id in rows:
            reviewer_agent_id = None
            if new_reviewer_id is not None:
                agent = s.query(service.Agent).filter(
                    service.Agent.user_id == new_reviewer_id).first()
                if agent is not None:
                    reviewer_agent_id = agent.agent_id
            publish_workflow_event(EVENT_REVIEW_REQUESTED, entity_type, eid,
                                   ref_id=new_reviewer_id, agent_id=reviewer_agent_id)
            if project_id is not None:
                _notify_webhooks(s, project_id, EVENT_REVIEW_REQUESTED,
                                 {"id": eid, "reviewer_id": new_reviewer_id,
                                  "status": "pending_review" if entity_type == "story" else "in_review"})
    return {k: v for k, v in result.items() if not k.startswith("_")}


# ---------- Bulk Task Operations ----------
@app.post("/api/tasks/bulk-update")
def bulk_update_tasks(body: BulkTaskUpdate, authorization: str | None = Header(None),
                      s: Session = Depends(get_session)):
    """批量更新任务：支持 status / priority / sprint_id / assignee_id / due_date"""
    results = []
    errors = []
    affected_pids = set()
    uid, _is_admin = _caller_uid_admin(authorization)
    for tid in body.task_ids:
        task = service.get_task(s, tid)
        if not task:
            errors.append({"task_id": tid, "error": "task not found"})
            continue
        try:
            updates = {}
            if body.status is not None:
                service.set_status(s, tid, body.status, changed_by=uid, reason="bulk")
            if body.priority is not None:
                updates["priority"] = body.priority
            if body.sprint_id is not None:
                updates["sprint_id"] = body.sprint_id
            if body.assignee_id is not None:
                updates["assignee_id"] = body.assignee_id
            elif body.clear_assignee:
                updates["assignee_id"] = None
            # v3.2 批量改截止日期：clear_due_date 优先清空；否则按传入 due_date 设置
            if body.clear_due_date:
                updates["due_date"] = None
            elif body.due_date is not None:
                updates["due_date"] = body.due_date
            if updates:
                service.update_task(s, tid, **updates)
            results.append({"task_id": tid, "ok": True})
            affected_pids.add(task.project_id)
        except Exception as e:
            errors.append({"task_id": tid, "error": str(e)})
    for pid in affected_pids:
        _invalidate_stats_cache(pid)
    return {"updated": results, "errors": errors}


@app.post("/api/tasks/bulk-delete")
def bulk_delete_tasks(body: BulkTaskDelete, s: Session = Depends(get_session)):
    """批量删除任务"""
    results = []
    errors = []
    affected_pids = set()
    for tid in body.task_ids:
        task = service.get_task(s, tid)
        pid = task.project_id if task else None
        try:
            if service.delete_task(s, tid):
                results.append({"task_id": tid, "ok": True})
                if pid:
                    affected_pids.add(pid)
            else:
                errors.append({"task_id": tid, "error": "task not found"})
        except Exception as e:
            errors.append({"task_id": tid, "error": str(e)})
    for pid in affected_pids:
        _invalidate_stats_cache(pid)
    return {"deleted": results, "errors": errors}


@app.post("/api/tasks/{tid}/spec/append")
def append_task_spec(tid: int, body: SpecAppendIn, s: Session = Depends(get_session)):
    return service._ser(_need(service.append_task_spec(s, tid, body.text), "task"))


# ---------- Comments ----------
@app.get("/api/tasks/{tid}/comments")
def list_comments(tid: int, s: Session = Depends(get_session)):
    try:
        return [service._ser(x) for x in service.list_comments(s, task_id=tid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


def _mention_notify(s: Session, *, author: str, content: str, link: str) -> None:
    """扫描评论内容中的 @username 提及并创建 mentioned 通知（Task/Story/Epic 评论共用）。"""
    mentioned_user_ids: set[int] = set()
    for username in re.findall(r"@([A-Za-z0-9_.-]{1,64})", content):
        mentioned = service.get_user_by_username(s, username)
        if mentioned and mentioned.id not in mentioned_user_ids:
            mentioned_user_ids.add(mentioned.id)
            service.create_notification(
                s, user_id=mentioned.id, notif_type="mentioned",
                title=f"{author} 在评论中提到了你", content=content[:500],
                link=link,
            )


@app.post("/api/tasks/{tid}/comments", status_code=201)
def create_comment(
    tid: int, body: CommentIn, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    try:
        comment = service.create_comment(s, task_id=tid, author=body.author, content=body.content)
        _mention_notify(s, author=body.author, content=body.content, link=f"/task/{tid}")
        return service._ser(comment)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))


# ---------- Story / Epic Comments ----------
@app.get("/api/stories/{sid}/comments")
def list_story_comments(sid: int, s: Session = Depends(get_session)):
    try:
        return [service._ser(x) for x in service.list_comments(s, story_id=sid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/stories/{sid}/comments", status_code=201)
def create_story_comment(
    sid: int, body: CommentIn, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    try:
        comment = service.create_comment(s, story_id=sid, author=body.author, content=body.content)
        _mention_notify(s, author=body.author, content=body.content, link=f"/story/{sid}")
        # 事件源：评审往返收敛 —— 评论者非 reviewer 时定向通知 reviewer；
        # 评论者即 reviewer（评审意见）时退化为广播，作者侧消费者感知。
        st = service.get_story(s, sid)
        reviewer_agent_id = None
        if st is not None and st.reviewer_id is not None:
            agent = s.query(service.Agent).filter(service.Agent.user_id == st.reviewer_id).first()
            reviewer_agent_id = agent.agent_id if agent is not None else None
        publish_workflow_event(EVENT_COMMENT_REPLIED, "story", sid,
                               ref_id=comment.id, agent_id=reviewer_agent_id)
        # Webhook 通道（Epic 122 切片 3）
        if st is not None:
            _epic = s.get(service.Epic, st.epic_id)
            if _epic is not None:
                _notify_webhooks(s, _epic.project_id, EVENT_COMMENT_REPLIED,
                                 {"id": sid, "comment_id": comment.id, "by": body.author})
        return service._ser(comment)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/api/epics/{eid}/comments")
def list_epic_comments(eid: int, s: Session = Depends(get_session)):
    try:
        return [service._ser(x) for x in service.list_comments(s, epic_id=eid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/epics/{eid}/comments", status_code=201)
def create_epic_comment(
    eid: int, body: CommentIn, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    try:
        comment = service.create_comment(s, epic_id=eid, author=body.author, content=body.content)
        _mention_notify(s, author=body.author, content=body.content, link=f"/epic/{eid}")
        return service._ser(comment)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.delete("/api/comments/{cid}")
def delete_comment(cid: int, s: Session = Depends(get_session)):
    if not service.delete_comment(s, cid):
        raise HTTPException(status_code=404, detail="comment not found")
    return {"ok": True}


@app.post("/api/tasks/{tid}/generate-subtasks")
def generate_subtasks(tid: int, s: Session = Depends(get_session)):
    try:
        created = service.generate_tasks_from_spec(s, tid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return [service._ser(t) for t in created]


# ---------- Search ----------
@app.get("/api/tasks")
def search_tasks(project_id: int | None = None, epic_id: int | None = None,
                 story_id: int | None = None, sprint_id: int | None = None,
                 type: str | None = None, status: str | None = None,
                 priority: str | None = None, q: str | None = Query(None),
                 reviewer_id: str | None = Query(None),
                 limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
                 s: Session = Depends(get_session),
                 authorization: str | None = Header(None)):
    rid: int | None = None
    if reviewer_id:
        if reviewer_id == "me":
            uid, _ = _caller_uid_admin(authorization)
            if uid is None:
                raise HTTPException(status_code=422, detail="reviewer_id=me requires login")
            rid = uid
        else:
            try:
                rid = int(reviewer_id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="invalid reviewer_id")
    try:
        rows = service.search_tasks(s, project_id=project_id, epic_id=epic_id,
                                    story_id=story_id, sprint_id=sprint_id,
                                    type=type, status=status,
                                    priority=priority, q=q, reviewer_id=rid,
                                    limit=limit, offset=offset)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return [service._ser(t) for t in rows]


# 全局 Story 关键词搜索（命令面板等场景）；路径用 /api/search/stories 避免与 /api/stories/{sid} 冲突
@app.get("/api/search/stories")
def search_stories_api(
    q: str = Query(..., min_length=1, description="关键词"),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
):
    rows = service.search_stories(s, q=q, limit=limit)
    return [service._ser(x) for x in rows]


# 全局 Epic 关键词搜索（命令面板等场景，Epic v6.13）；路径用 /api/search/epics 避免与 /api/epics/{eid} 冲突
@app.get("/api/search/epics")
def search_epics_api(
    q: str = Query(..., min_length=1, description="关键词"),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
):
    rows = service.search_epics(s, q=q, limit=limit)
    return [service._ser(x) for x in rows]


# 全局 Sprint 关键词搜索（命令面板等场景，v6.14）；路径用 /api/search/sprints 避免与 /api/projects/{pid}/sprints 冲突
@app.get("/api/search/sprints")
def search_sprints_api(
    q: str = Query(..., min_length=1, description="关键词"),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
):
    rows = service.search_sprints(s, q=q, limit=limit)
    return [service._ser(x) for x in rows]


# 当前用户通知关键词搜索（命令面板等场景，v6.15）；通知属隐私数据，必须带鉴权且仅返回本人通知
@app.get("/api/search/notifications")
def search_notifications_api(
    q: str = Query(..., min_length=1, description="关键词"),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = _current_user(authorization, s, required_permission="api:read").id
    rows = service.search_notifications(s, user_id=uid, q=q, limit=limit)
    return [service._ser(n) for n in rows]


# ---------- Sprint ----------
@app.get("/api/projects/{pid}/sprints")
def list_sprints(pid: int, s: Session = Depends(get_session),
                limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    _need(service.get_project(s, pid), "project")
    return [service._ser(sp) for sp in service.list_sprints(s, pid, limit=limit, offset=offset)]


@app.post("/api/projects/{pid}/sprints", status_code=201)
def create_sprint(pid: int, body: SprintIn, s: Session = Depends(get_session)):
    _need(service.get_project(s, pid), "project")
    return service._ser(service.create_sprint(
        s, project_id=pid, title=body.title, goal=body.goal,
        start_date=body.start_date, end_date=body.end_date))


@app.get("/api/sprints/{sid}")
def get_sprint(sid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_sprint(s, sid), "sprint"))


@app.patch("/api/sprints/{sid}")
def update_sprint(sid: int, body: SprintPatch, s: Session = Depends(get_session)):
    try:
        r = service.update_sprint(s, sid, **body.model_dump(exclude_none=True))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(_need(r, "sprint"))


@app.post("/api/sprints/{sid}/activate", status_code=200)
def activate_sprint(sid: int, s: Session = Depends(get_session)):
    try:
        result = service.activate_sprint(s, sid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    _invalidate_stats_cache(result.project_id)
    return service._ser(result)


@app.post("/api/sprints/{sid}/complete", status_code=200)
def complete_sprint(sid: int, s: Session = Depends(get_session)):
    try:
        result = service.complete_sprint(s, sid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    _invalidate_stats_cache(result.project_id)
    return service._ser(result)


@app.delete("/api/sprints/{sid}")
def delete_sprint(sid: int, s: Session = Depends(get_session)):
    try:
        if not service.delete_sprint(s, sid):
            raise HTTPException(status_code=404, detail="sprint not found")
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


@app.get("/api/sprints/{sid}/tasks")
def list_sprint_tasks(sid: int, s: Session = Depends(get_session),
                      limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    _need(service.get_sprint(s, sid), "sprint")
    return [service._ser(t) for t in service.list_tasks(s, sprint_id=sid, limit=limit, offset=offset)]


@app.get("/api/sprints/{sid}/burndown")
def sprint_burndown(sid: int, s: Session = Depends(get_session)):
    """Sprint 燃尽图数据"""
    return service.get_sprint_burndown(s, sid)


# ---------- Attachment ----------
@app.get("/api/tasks/{tid}/attachments")
def list_attachments(tid: int, s: Session = Depends(get_session)):
    try:
        return [service._ser(a) for a in service.list_attachments(s, tid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/tasks/{tid}/attachments", status_code=201)
async def upload_attachment(tid: int, file: UploadFile = File(...), s: Session = Depends(get_session)):
    try:
        content = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="failed to read file")
    try:
        att = service.create_attachment(s, task_id=tid, content=content,
                                         original_name=file.filename or "unnamed",
                                         mime_type=file.content_type or "application/octet-stream")
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(att)


@app.get("/api/attachments/{aid}")
def download_attachment(aid: int, s: Session = Depends(get_session)):
    att = service.get_attachment(s, aid)
    if not att:
        raise HTTPException(status_code=404, detail="attachment not found")
    path = service.get_attachment_path(att)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file not found on disk")
    return FileResponse(path, media_type=att.mime_type, filename=att.original_name)


@app.get("/api/attachments/{aid}/info")
def attachment_info(aid: int, s: Session = Depends(get_session)):
    att = service.get_attachment(s, aid)
    if not att:
        raise HTTPException(status_code=404, detail="attachment not found")
    return service._ser(att)


@app.delete("/api/attachments/{aid}")
def delete_attachment(aid: int, s: Session = Depends(get_session)):
    if not service.delete_attachment(s, aid):
        raise HTTPException(status_code=404, detail="attachment not found")
    return {"ok": True}


# ---------- COS 图片上传（Epic 64 S1） ----------
_COS_MAX_SIZE = 10 * 1024 * 1024  # 10MB
_COS_ALLOWED_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_COS_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _ext_for_mime(mime: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
            "image/webp": ".webp"}.get(mime, ".png")


@app.get("/api/projects/{pid}/cos/config")
def cos_config(pid: int, s: Session = Depends(get_session)):
    """COS 配置状态（前端据此显示上传入口/降级提示）。未配置不报错，返回 configured:false。"""
    if not service.get_project(s, pid):
        raise HTTPException(status_code=404, detail="project not found")
    cfg = _cos_client.config_dict()
    cfg["upload_endpoint"] = f"/api/projects/{pid}/cos/upload"
    return cfg


@app.post("/api/projects/{pid}/cos/upload", status_code=201)
async def cos_upload(pid: int, file: UploadFile = File(...), s: Session = Depends(get_session)):
    """服务端直传图片至腾讯云 COS，返回预签名 GET URL（24h）供 markdown 引用。

    优雅降级：COS 环境变量未配置时返回 503 明确错误，不阻断其他功能。
    权限：路由 /api/projects/{pid}/... 由 project_access_middleware 自动覆盖（成员写/管理员绕过）。
    """
    if not service.get_project(s, pid):
        raise HTTPException(status_code=404, detail="project not found")
    if not _cos_client.is_configured():
        raise HTTPException(status_code=503,
                            detail=f"COS not configured: {_cos_client.config_error} (set COS_SECRET_ID/COS_SECRET_KEY/COS_BUCKET/COS_REGION)")
    try:
        content = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="failed to read file")
    if len(content) > _COS_MAX_SIZE:
        raise HTTPException(status_code=422, detail="file too large (max 10MB)")
    mime = file.content_type or "application/octet-stream"
    if mime not in _COS_ALLOWED_TYPES:
        raise HTTPException(status_code=422,
                            detail=f"unsupported content type: {mime} (allowed: image/png, image/jpeg, image/gif, image/webp)")
    original_name = file.filename or "unnamed"
    ext = os.path.splitext(original_name)[1].lower() or _ext_for_mime(mime)
    if ext not in _COS_ALLOWED_EXTS:
        ext = _ext_for_mime(mime)
    key = f"uploads/{pid}/{uuid.uuid4().hex}{ext}"
    try:
        _cos_client.put_object(key, content, content_type=mime)
    except CosError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "key": key,
        "url": _cos_client.presigned_get_url(key),
        "size": len(content),
        "content_type": mime,
        "original_name": original_name,
        "cos_configured": True,
    }


# ---------- AgentSchedule ----------
@app.get("/api/projects/{pid}/schedules")
def list_schedules(pid: int, s: Session = Depends(get_session),
                   limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    _need(service.get_project(s, pid), "project")
    return [service._ser(sch) for sch in service.list_schedules(s, pid, limit=limit, offset=offset)]


@app.post("/api/projects/{pid}/schedules", status_code=201)
def create_schedule(pid: int, body: ScheduleIn, s: Session = Depends(get_session)):
    _need(service.get_project(s, pid), "project")
    try:
        sch = service.create_schedule(
            s, project_id=pid, title=body.title,
            schedule_type=body.schedule_type, cron_expr=body.cron_expr,
            agent=body.agent, task_id=body.task_id,
            task_priority=body.task_priority, task_type=body.task_type,
            epic_id=body.epic_id,
        )
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return service._ser(sch)


@app.get("/api/schedules/{sid}")
def get_schedule(sid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_schedule(s, sid), "schedule"))


@app.patch("/api/schedules/{sid}")
def update_schedule(sid: int, body: SchedulePatch, s: Session = Depends(get_session)):
    fields = body.model_dump(exclude_none=True)
    for k in ("agent", "task_id", "task_priority", "task_type", "epic_id"):
        if k in body.model_fields_set:
            fields[k] = getattr(body, k)  # 显式 null = 解除绑定 / 清除筛选
    try:
        r = service.update_schedule(s, sid, **fields)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return service._ser(_need(r, "schedule"))


@app.delete("/api/schedules/{sid}")
def delete_schedule(sid: int, s: Session = Depends(get_session)):
    if not service.delete_schedule(s, sid):
        raise HTTPException(status_code=404, detail="schedule not found")
    return {"ok": True}


# ---------- AgentRun ----------
@app.post("/api/schedules/{sid}/runs", status_code=201)
def create_run(sid: int, body: RunIn, s: Session = Depends(get_session)):
    try:
        run = service.create_run(s, schedule_id=sid, task_id=body.task_id,
                                 idempotency_key=body.idempotency_key)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.Duplicate as e:
        raise HTTPException(status_code=409, detail=str(e))
    return service._ser(run)


@app.get("/api/schedules/{sid}/runs")
def list_runs(sid: int, s: Session = Depends(get_session),
              limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    _need(service.get_schedule(s, sid), "schedule")
    return [service._ser(r) for r in service.list_runs(s, sid, limit=limit, offset=offset)]


@app.get("/api/runs/{rid}")
def get_run(rid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_run(s, rid), "run"))


@app.patch("/api/runs/{rid}")
def update_run(rid: int, body: RunPatch, s: Session = Depends(get_session)):
    try:
        r = service.update_run(s, rid, **body.model_dump(exclude_none=True))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(_need(r, "run"))


@app.post("/api/runs/{rid}/report")
def report_run_result(rid: int, body: RunReportIn, s: Session = Depends(get_session)):
    """Agent 主动报告 run 结果（Epic 78 Story 104）：
    仅 pending/running → success/failed/cancelled 合法；终态不可再变（幂等除外）。
    """
    try:
        r = service.report_run_result(
            s, rid, status=body.status, summary=body.summary, log_ref=body.log_ref,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return service._ser(r)


@app.delete("/api/runs/{rid}")
def delete_run(rid: int, s: Session = Depends(get_session)):
    if not service.delete_run(s, rid):
        raise HTTPException(status_code=404, detail="run not found")
    return {"ok": True}


# ---------- Project visibility & members ----------


# ---------- Project Members ----------
@app.get("/api/projects/{pid}/members")
def list_members(
    pid: int, s: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):
    _need(service.get_project(s, pid), "project")
    members, total = service.list_project_members(s, pid, limit=limit, offset=offset)
    return {
        "items": [
            {
                **service._ser(m),
                "username": (
                    service.get_user(s, m.user_id).username
                    if service.get_user(s, m.user_id) else None
                ),
            }
            for m in members
        ],
        "total": total,
    }


@app.post("/api/projects/{pid}/members", status_code=201)
def add_member(
    pid: int, body: dict,
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """邀请用户加入项目（仅 owner 或管理员可操作）"""
    uid = _current_user(authorization, s, required_permission="api:write").id
    if not service.user_is_project_owner(s, pid, uid):
        u = service.get_user(s, uid) if uid else None
        if not (u and u.is_admin):
            raise HTTPException(status_code=403, detail="only owner or admin can add members")
    try:
        found_user = service.get_user_by_username(s, body.get("username")) if body.get("username") else None
        user_id = body.get("user_id") or (found_user.id if found_user else None)
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id or username required")
        pm = service.add_project_member(s, project_id=pid, user_id=user_id, role=body.get("role", "member"))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.Duplicate as e:
        raise HTTPException(status_code=409, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 发送邀请通知
    project = service.get_project(s, pid)
    service.create_notification(
        s, user_id=user_id, notif_type="project_invite",
        title=f"项目邀请：{project.name}",
        content=f"你已被邀请加入项目「{project.name}」（{project.key or ''}），角色：{body.get('role', 'member')}。",
        link=f"/project/{pid}",
    )
    return service._ser(pm)


@app.delete("/api/projects/{pid}/members/{uid}")
def remove_member(
    pid: int, uid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """移除项目成员（仅 owner 或管理员可操作，owner 不能移除自己）"""
    current_uid = _current_user(authorization, s, required_permission="api:write").id
    if not service.user_is_project_owner(s, pid, current_uid):
        u = service.get_user(s, current_uid) if current_uid else None
        if not (u and u.is_admin):
            raise HTTPException(status_code=403, detail="only owner or admin can remove members")
    try:
        if not service.remove_project_member(s, pid, uid):
            raise HTTPException(status_code=404, detail="member not found")
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


@app.patch("/api/projects/{pid}/members/{uid}")
def update_member_role(
    pid: int, uid: int, body: MemberRoleIn, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """更新成员角色（仅 owner 或管理员可操作）"""
    current_uid = _current_user(authorization, s, required_permission="api:write").id
    if not service.user_is_project_owner(s, pid, current_uid):
        u = service.get_user(s, current_uid) if current_uid else None
        if not (u and u.is_admin):
            raise HTTPException(status_code=403, detail="only owner or admin can update member role")
    try:
        pm = service.update_project_member_role(s, pid, uid, body.role)
        if not pm:
            raise HTTPException(status_code=404, detail="member not found")
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(pm)


# ---------- Notifications ----------
@app.get("/api/notifications")
def list_notifications(
    s: Session = Depends(get_session),
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    authorization: str | None = Header(None),
):
    uid = _current_user(authorization, s, required_permission="api:read").id
    items, total = service.list_notifications(s, uid, limit=limit, offset=offset, unread_only=unread_only)
    return {"items": [service._ser(n) for n in items], "total": total}


@app.get("/api/notifications/unread-count")
def unread_count(
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = _current_user(authorization, s, required_permission="api:read").id
    _, total = service.list_notifications(s, uid, limit=1, unread_only=True)
    return {"count": total}


@app.post("/api/notifications/{nid}/read")
def mark_read(
    nid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = _current_user(authorization, s, required_permission="api:write").id
    n = service.mark_notification_read(s, nid, uid)
    if not n:
        raise HTTPException(status_code=404, detail="notification not found")
    return service._ser(n)


@app.post("/api/notifications/read-all")
def mark_all_read(
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = _current_user(authorization, s, required_permission="api:write").id
    count = service.mark_all_notifications_read(s, uid)
    return {"ok": True, "count": count}


@app.delete("/api/notifications/{nid}")
def delete_notification(
    nid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = _current_user(authorization, s, required_permission="api:write").id
    if not service.delete_notification(s, nid, uid):
        raise HTTPException(status_code=404, detail="notification not found")
    return {"ok": True}


# ---------- Project Statistics ----------
# 配置化 TTL：全局默认 AGENTBOARD_CACHE_TTL，各端点可单独覆盖
# 统计端点默认回退到全局默认 TTL
_CACHE_TTL_STATS = int(os.getenv("AGENTBOARD_CACHE_TTL_STATS", str(API_CACHE_TTL)))
# 列表端点缓存 TTL（预留；如需为列表端点启用缓存，可设置此变量）
_CACHE_TTL_LIST  = int(os.getenv("AGENTBOARD_CACHE_TTL_LIST", str(API_CACHE_TTL)))
@app.get("/api/projects/{pid}/stats")
def project_stats(pid: int, s: Session = Depends(get_session)):
    cache = get_cache()
    cache_key = f"stats:{pid}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    _need(service.get_project(s, pid), "project")
    result = service.get_project_stats(s, pid)
    cache.set(cache_key, result, _CACHE_TTL_STATS)
    return result


# ---------- Dashboard overview（跨项目聚合统计，首页性能优化） ----------
@app.get("/api/overview")
def dashboard_overview(
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """首页 Dashboard 单请求聚合统计（替代四级整树预加载）。

    可见性：admin → 全部项目；普通用户 → 成员项目；未登录（REQUIRE_AUTH=0
    本地开放模式）→ 空统计。权限由 require_business_auth + project_access_middleware
    整体把关：本端点非项目级路由，鉴权仅要求有效身份（若开启）。
    """
    uid = _optional_user_id(authorization, s)
    return service.get_overview(s, uid)


# ---------- Cache Statistics (Epic 30 / Story 30.1 Task 802) ----------
@app.get("/api/cache/stats")
def cache_stats(s: Session = Depends(get_session)):
    """缓存命中率与容量统计。

    鉴权由 require_business_auth 中间件统一处理：
    - AGENTBOARD_REQUIRE_AUTH=1 时，需携带具备 api:read 权限的 Bearer/API Key；
    - 本地开放模式（默认）下公开可读。
    """
    return get_cache().stats()


# ---------- Admin: Users ----------
def _require_admin(authorization: str | None, s: Session, *, permission: str = "api:read"):
    """校验调用方为管理员，同时支持 Bearer 登录 token 与 ``abk_`` API key。

    权限模型（2026-07-29）：API key 的身份完全等同其关联用户 —— 管理员用户的
    key 可走 admin 通道；普通用户的 key 一律 403。无凭证/无效凭证与历史行为
    保持一致，统一返回 403 "admin only"。
    """
    try:
        user = _current_user(authorization, s, required_permission=permission)
    except HTTPException:
        raise HTTPException(status_code=403, detail="admin only")
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return user


@app.get("/api/admin/users")
def admin_list_users(
    s: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    authorization: str | None = Header(None),
):
    _require_admin(authorization, s)
    users, total = service.list_users(s, limit=limit, offset=offset)
    return {"items": [service._ser(x) for x in users], "total": total}


@app.patch("/api/admin/users/{uid}")
def admin_update_user(
    uid: int, body: UserAdminPatch, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    _require_admin(authorization, s, permission="api:write")
    u = service.set_user_admin(s, uid, body.is_admin)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    return service._ser(u)


# ---------- Admin: Projects ----------
@app.get("/api/admin/projects")
def admin_list_projects(
    s: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    authorization: str | None = Header(None),
):
    _require_admin(authorization, s)
    projects, total = service.list_all_projects_admin(s, limit=limit, offset=offset)
    return {"items": projects, "total": total}


@app.delete("/api/admin/projects/{pid}")
def admin_delete_project(
    pid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    _require_admin(authorization, s, permission="api:write")
    if not service.delete_project(s, pid):
        raise HTTPException(status_code=404, detail="project not found")
    return {"ok": True}


# ---------- Epic 20: Data Export ----------
@app.get("/api/projects/{pid}/export")
def export_project(
    pid: int, format: str = Query("json", pattern=r"^(json)$"),
    s: Session = Depends(get_session),
):
    """导出项目完整数据为 JSON。"""
    try:
        return service.export_project_data(s, pid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/stories/{sid}/export")
def export_story(
    sid: int, format: str = Query("json", pattern=r"^(json)$"),
    s: Session = Depends(get_session),
):
    """导出 Story 及所有子任务为 JSON。"""
    try:
        return service.export_story_data(s, sid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------- Epic 22 Story 22.1: 审计日志 ----------
@app.get("/api/audit-logs")
def list_audit_logs(
    project_id: int | None = Query(None),
    entity_type: str | None = Query(None),
    entity_id: int | None = Query(None),
    user_id: int | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
):
    """获取审计日志列表。"""
    items, total = service.list_audit_logs(
        s, project_id=project_id, entity_type=entity_type,
        entity_id=entity_id, user_id=user_id, action=action,
        limit=limit, offset=offset,
    )
    return {"items": [service._ser(x) for x in items], "total": total}


# ---------- Epic 22 Story 22.2: 任务依赖关系 ----------
@app.post("/api/tasks/{tid}/dependencies", status_code=201)
def add_dependency(
    tid: int,
    depends_on_id: int = Query(..., description="被依赖的任务 ID"),
    dependency_type: str = Query("blocks", pattern=r"^(blocks|blocked_by|relates_to)$"),
    s: Session = Depends(get_session),
):
    """添加任务依赖关系。"""
    try:
        dep = service.add_task_dependency(
            s, task_id=tid, depends_on_id=depends_on_id, dependency_type=dependency_type,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.Duplicate as e:
        raise HTTPException(status_code=409, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(dep)


@app.get("/api/tasks/{tid}/dependencies")
def get_dependencies(tid: int, s: Session = Depends(get_session)):
    """获取任务的依赖关系（blockers 和 blocked_by）。"""
    try:
        return service.get_task_dependencies(s, tid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/dependencies/{did}")
def delete_dependency(did: int, s: Session = Depends(get_session)):
    """删除依赖关系。"""
    try:
        service.remove_task_dependency(s, did)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


# ---------- Epic 22 Story 22.3: 数据导入 ----------
@app.post("/api/projects/{pid}/import")
def import_tasks(
    pid: int,
    body: dict,
    s: Session = Depends(get_session),
):
    """从 JSON 数据批量导入任务。"""
    try:
        return service.import_tasks_from_json(s, pid, body)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))


# ---------- Epic 22 Story 22.4: Webhook 配置 ----------
class WebhookIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2000)
    secret: str | None = Field(None, max_length=256)
    events: list[str] = Field(default_factory=list)


@app.post("/api/webhooks", status_code=201)
def create_webhook(
    body: WebhookIn,
    project_id: int | None = Query(None),
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """创建 Webhook 配置。"""
    user = _current_user(authorization, s) if authorization else None
    try:
        wh = service.create_webhook(
            s, project_id=project_id, name=body.name, url=body.url,
            secret=body.secret, events=body.events,
            created_by=user.id if user else None,
        )
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    import json
    return {
        "id": wh.id, "name": wh.name, "url": wh.url, "enabled": wh.enabled,
        "events": json.loads(wh.events), "created_at": wh.created_at,
    }


@app.get("/api/webhooks")
def list_webhooks(
    project_id: int | None = Query(None),
    s: Session = Depends(get_session),
):
    """列出 Webhook 配置。"""
    import json
    webhooks = service.list_webhooks(s, project_id=project_id)
    return {
        "items": [
            {"id": w.id, "name": w.name, "url": w.url, "enabled": w.enabled,
             "events": json.loads(w.events), "created_at": w.created_at}
            for w in webhooks
        ]
    }


@app.delete("/api/webhooks/{wid}")
def delete_webhook(wid: int, s: Session = Depends(get_session)):
    """删除 Webhook 配置。"""
    try:
        service.delete_webhook(s, wid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


@app.patch("/api/webhooks/{wid}")
def toggle_webhook(
    wid: int,
    enabled: bool,
    s: Session = Depends(get_session),
):
    """启用/停用 Webhook。"""
    try:
        wh = service.toggle_webhook(s, wid, enabled)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    import json
    return {
        "id": wh.id, "name": wh.name, "url": wh.url, "enabled": wh.enabled,
        "events": json.loads(wh.events), "created_at": wh.created_at,
    }


# ---------- Documents (Epic 15：项目文档维护 / 多成员·多 Agent 协作) ----------
class DocumentIn(BaseModel):
    project_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=300)
    content: str = ""
    type: str = "plan"  # memory / plan / knowledge / design
    status: str = "draft"  # draft / in_review / approved / cancelled
    epic_id: int | None = None
    story_id: int | None = None
    folder_id: int | None = None
    author_id: int | None = None


class DocumentPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    content: str | None = None
    type: str | None = None
    status: str | None = None
    folder_id: int | None = None  # null = 移出文件夹到根目录
    epic_id: int | None = None   # null = 清空 epic 关联（须属于文档项目）
    story_id: int | None = None  # null = 清空 story 关联（须属于文档项目/所属 epic）


class DocumentFolderIn(BaseModel):
    project_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=300)
    parent_id: int | None = None


class DocumentFolderPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=300)
    parent_id: int | None = None  # null = 移动到根目录


class DocumentCommentIn(BaseModel):
    author: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)
    author_id: int | None = None


class DocumentCommentPatch(BaseModel):
    content: str = Field(min_length=1)
    author: str = Field(min_length=1, max_length=100)


@app.post("/api/documents", status_code=201)
def create_document(body: DocumentIn, s: Session = Depends(get_session),
                    authorization: str | None = Header(None)):
    """新建文档（title/content/type/project_id 必填，status 默认 draft）。

    权限控制（2026-07-21）：需为目标项目成员或管理员。
    """
    # 权限检查：必须在目标项目中是成员或管理员
    uid, is_admin = _caller_uid_admin(authorization)
    if not is_admin and not service.user_is_project_member(s, body.project_id, uid):
        raise HTTPException(status_code=403, detail="project membership required")
    try:
        d = service.create_document(
            s, project_id=body.project_id, title=body.title, content=body.content,
            type=body.type, status=body.status, epic_id=body.epic_id,
            story_id=body.story_id, folder_id=body.folder_id, author_id=body.author_id,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(d)


@app.get("/api/document-folders", response_model=None)
def list_document_folders(
    project_id: int | None = Query(None),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """列出项目文档文件夹（含全部层级，前端组装树）。

    权限：与文档列表一致——指定 project_id 时由中间件校验成员身份；
    未指定时仅返回当前用户有权限项目的文件夹。
    """
    uid = _optional_user_id(authorization, s)
    return [service._ser(f) for f in service.list_document_folders(
        s, project_id=project_id, user_id=uid,
    )]


@app.post("/api/document-folders", status_code=201)
def create_document_folder(body: DocumentFolderIn, s: Session = Depends(get_session),
                           authorization: str | None = Header(None)):
    """新建文档文件夹（name 必填，parent_id 可选 = 创建子文件夹）。

    权限：需为目标项目成员或管理员。
    """
    uid, is_admin = _caller_uid_admin(authorization)
    if not is_admin and not service.user_is_project_member(s, body.project_id, uid):
        raise HTTPException(status_code=403, detail="project membership required")
    try:
        f = service.create_document_folder(
            s, project_id=body.project_id, name=body.name, parent_id=body.parent_id,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(f)


@app.patch("/api/document-folders/{fid}")
def update_document_folder(fid: int, body: DocumentFolderPatch, s: Session = Depends(get_session)):
    """重命名 / 移动文件夹。parent_id=null 移动到根目录；防环校验由 service 完成。"""
    try:
        fields = body.model_dump(exclude_none=True)
        if "parent_id" in body.model_fields_set:
            fields["parent_id"] = body.parent_id  # 显式 null = 移动到根
        r = service.update_document_folder(s, fid, **fields)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(_need(r, "document folder"))


@app.delete("/api/document-folders/{fid}")
def delete_document_folder(fid: int, s: Session = Depends(get_session)):
    """删除文件夹：直接子文档与子文件夹上提至父级，不级联删除子项。"""
    if not service.delete_document_folder(s, fid):
        raise HTTPException(status_code=404, detail="document folder not found")
    return {"ok": True}


@app.get("/api/documents")
def list_documents(
    project_id: int | None = Query(None),
    type: str | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """列出文档，支持按 project_id / type / status 过滤与关键词搜索。默认按 updated_at 倒序。

    权限控制（2026-07-21）：
    - 指定 project_id 时：通过中间件校验项目成员身份
    - 未指定 project_id 时：仅返回用户有权限的项目文档
    """
    uid = _optional_user_id(authorization, s)
    try:
        rows = service.list_documents(
            s, project_id=project_id, type=type, status=status, q=q,
            limit=limit, offset=offset, user_id=uid,
        )
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return [service._ser(d) for d in rows]


@app.get("/api/documents/{did}")
def get_document(did: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_document(s, did), "document"))


@app.patch("/api/documents/{did}")
def update_document(did: int, body: DocumentPatch, s: Session = Depends(get_session)):
    """编辑文档 title/content/type（状态流转请用 PUT /status）。

    folder_id 显式传 null 表示移出文件夹到根目录；未传该字段则保持不变。
    """
    try:
        fields = body.model_dump(exclude_none=True)
        if "folder_id" in body.model_fields_set:
            fields["folder_id"] = body.folder_id
        # epic_id/story_id 显式 null = 清空关联（exclude_none 会吞 null，需按 fields_set 还原）
        if "epic_id" in body.model_fields_set:
            fields["epic_id"] = body.epic_id
        if "story_id" in body.model_fields_set:
            fields["story_id"] = body.story_id
        r = service.update_document(s, did, **fields)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(_need(r, "document"))


@app.put("/api/documents/{did}/status")
def set_document_status(did: int, body: StatusIn, s: Session = Depends(get_session)):
    """文档评审状态流转：draft→in_review→approved/cancelled/draft；approved→draft。非法迁移返回 400。"""
    try:
        result = service.set_document_status(s, did, body.status)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.IllegalTransition as e:
        raise HTTPException(status_code=400, detail=str(e))
    return service._ser(_need(result, "document"))


@app.delete("/api/documents/{did}")
def delete_document(did: int, s: Session = Depends(get_session)):
    if not service.delete_document(s, did):
        raise HTTPException(status_code=404, detail="document not found")
    return {"ok": True}


@app.post("/api/documents/{did}/comments", status_code=201)
def create_document_comment(did: int, body: DocumentCommentIn, s: Session = Depends(get_session)):
    """对文档添加评论（markdown），author 为成员或 Agent 账号名。"""
    try:
        c = service.create_document_comment(
            s, document_id=did, author=body.author, content=body.content,
            author_id=body.author_id,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(c)


@app.get("/api/documents/{did}/comments")
def list_document_comments(did: int, s: Session = Depends(get_session)):
    """列出文档评论，按 created_at 正序。"""
    try:
        return [service._ser(x) for x in service.list_document_comments(s, did)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.patch("/api/document-comments/{cid}")
def update_document_comment(cid: int, body: DocumentCommentPatch, s: Session = Depends(get_session)):
    """编辑文档评论：仅作者（成员或 Agent 账号）可编辑自己的评论。"""
    try:
        c = service.update_document_comment(s, cid, content=body.content, author=body.author)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(_need(c, "comment"))


@app.delete("/api/document-comments/{cid}")
def delete_document_comment(cid: int, s: Session = Depends(get_session)):
    if not service.delete_document_comment(s, cid):
        raise HTTPException(status_code=404, detail="comment not found")
    return {"ok": True}


# ---------- Proposals (Epic 96 P0：Proposal 澄清回路 / 人机协同需求分析) ----------
class ProposalIn(BaseModel):
    project_id: int
    title: str = Field(min_length=1, max_length=300)
    content: str = ""
    author_id: int | None = None


class ProposalPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = None
    converged_spec: str | None = None
    story_id: int | None = None


class ProposalStatusIn(BaseModel):
    status: str
    error: str | None = None


class ProposalClaimIn(BaseModel):
    """Worker 原子认领提案。agent 为服务账号名，仅用于排障与轮次署名。"""

    agent: str = ""


class ProposalReclaimIn(BaseModel):
    """回收租约过期的 analyzing 提案。省略 lease_seconds 时用服务端默认值。"""

    lease_seconds: int | None = Field(default=None, ge=0)


class RecoverFailedIn(BaseModel):
    """Agent 不可用导致的 failed 提案自动重投参数（后端 job）。"""

    window_seconds: int | None = Field(default=None, ge=0)
    max_retries: int | None = Field(default=None, ge=1)


class ProposalAskIn(BaseModel):
    """Agent 回写一轮 open questions。round 省略时自动取下一轮。"""

    questions: list[str] = Field(min_length=1)
    round: int | None = None
    summary: str = ""
    agent: str = ""


class ProposalAnswerIn(BaseModel):
    answer: str = ""
    unsure: bool = False


class ProposalConvertIn(BaseModel):
    """人工终审确认：把已收敛提案转化为 Story + 子 Task（Epic 96 P3）。

    epic_id 必填（目标 Epic 必须属于提案所在项目）；title 可覆盖 Story 标题，
    省略时用提案标题。
    """

    epic_id: int
    title: str | None = Field(default=None, min_length=1, max_length=300)


class ProposalTicketIn(BaseModel):
    """创建 Proposal → Ticket 转换请求（2026-08-08 文档 #59）。

    type: epic / story / task / bug；
    - epic 独立，无需父级；
    - story 必填 epic_id；
    - task / bug 必填 epic_id + story_id。
    """

    type: str
    epic_id: int | None = None
    story_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)


class TicketRequestExecuteIn(BaseModel):
    """agent 经 MCP 执行转换（execute-by-type 用）。与 ProposalTicketIn 同构。"""

    type: str
    epic_id: int | None = None
    story_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)


class TicketFailIn(BaseModel):
    error: str = ""


class TicketReclaimIn(BaseModel):
    lease_seconds: int | None = None


def _dispatch_proposal(proposal_id: int, round_no: int = 0, reason: str = "") -> None:
    """把提案投递到澄清工作队列（Epic 96 P2-1）。

    **best-effort**：MQ 未启用时是静默 no-op，broker 宕机时只记告警——派发通道
    出问题绝不能让用户的 REST 请求失败。真丢了消息也有兜底：Worker 的轮询模式与
    ``reclaim-stale`` 都能把工作项重新捞回来。
    """
    try:
        mq.publish_proposal_event(proposal_id, round_no, reason)
    except Exception:  # pragma: no cover - 双保险，publish 内部已兜底
        pass


@app.post("/api/proposals", status_code=201)
def create_proposal(body: ProposalIn, s: Session = Depends(get_session),
                    authorization: str | None = Header(None)):
    """新建需求提案（初始 draft）。需为目标项目成员或管理员。"""
    uid, is_admin = _caller_uid_admin(authorization)
    if not is_admin and not service.user_is_project_member(s, body.project_id, uid):
        raise HTTPException(status_code=403, detail="project membership required")
    try:
        p = service.create_proposal(
            s, project_id=body.project_id, title=body.title, content=body.content,
            author_id=body.author_id if body.author_id is not None else uid,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(p)


@app.get("/api/proposals")
def list_proposals(
    project_id: int | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """列出提案，支持按 project_id / status 过滤与关键词搜索，默认 updated_at 倒序。"""
    uid = _optional_user_id(authorization, s)
    try:
        rows = service.list_proposals(
            s, project_id=project_id, status=status, q=q,
            limit=limit, offset=offset, user_id=uid,
        )
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return [service._ser(p) for p in rows]


@app.get("/api/proposals/pending")
def list_pending_proposals(
    limit: int = Query(20, ge=1, le=200),
    s: Session = Depends(get_session),
):
    """Worker 拉取待认领提案（P1 先用 DB 轮询，P2 由 MQ 替换）。"""
    rows = service.list_proposals(s, status="queued", limit=limit)
    return [service._ser(p) for p in rows]


# 必须声明在 /api/proposals/{pid} 之前，否则 "reclaim-stale" 会被当作 pid 捕获。
@app.post("/api/proposals/reclaim-stale")
def reclaim_stale_proposals(
    body: ProposalReclaimIn | None = None, s: Session = Depends(get_session),
):
    """回收租约过期的 analyzing 提案（持有 Worker 已崩溃），批量回退 queued。

    判定依据是 claimed_at 而非 updated_at —— 后者会被用户作答等无关写入刷新，
    会让崩溃 Worker 的租约被无限续期。返回被回收的 proposal id 列表。
    """
    lease = (body.lease_seconds if body and body.lease_seconds is not None
             else service.DEFAULT_CLAIM_LEASE_SECONDS)
    try:
        ids = service.reclaim_stale_proposals(s, lease_seconds=lease)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 回收即重投：崩溃 Worker 留下的工作项重新进入队列，闭合「超时回退重投」回路。
    for pid in ids:
        _dispatch_proposal(pid, 0, mq.REASON_RECLAIMED)
    return {"reclaimed": ids, "count": len(ids), "lease_seconds": lease}


# 必须声明在 /api/proposals/{pid} 之前，避免 "recover-failed" 被当作 pid 捕获。
@app.post("/api/proposals/recover-failed")
def recover_failed_proposals(
    body: RecoverFailedIn | None = None, s: Session = Depends(get_session),
):
    """后端 job：把「Agent 不可用」导致的 failed 提案自动回退 queued 重投。

    前端不做手动 retry —— agent 恢复后由本端点自动重试（受 window_seconds
    频率控制与 max_retries 上限约束，超限转人工）。与 reclaim-stale
    （analyzing 租约超时）互补，共同构成自动闭环的自愈回路。
    """
    window = (body.window_seconds if body and body.window_seconds is not None
              else 120)
    max_r = (body.max_retries if body and body.max_retries is not None else 5)
    try:
        ids = service.recover_failed_proposals(
            s, window_seconds=window, max_retries=max_r,
        )
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    for pid in ids:
        _dispatch_proposal(pid, 0, mq.REASON_QUEUED)
    return {"recovered": ids, "count": len(ids),
            "window_seconds": window, "max_retries": max_r}


@app.get("/api/proposals/{pid}")
def get_proposal(pid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_proposal(s, pid), "proposal"))


@app.patch("/api/proposals/{pid}")
def update_proposal(pid: int, body: ProposalPatch, s: Session = Depends(get_session)):
    """编辑提案正文 / 收敛规格 / 回填 story_id（状态流转请用 PUT /status）。"""
    try:
        r = service.update_proposal(s, pid, **body.model_dump(exclude_none=True))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(_need(r, "proposal"))


@app.put("/api/proposals/{pid}/status")
def set_proposal_status(pid: int, body: ProposalStatusIn, s: Session = Depends(get_session)):
    """澄清状态机流转：draft→queued→analyzing→awaiting→answered→converged→story_created。

    非法迁移返回 400。失败态可带 error 说明并回退 queued 重投。
    """
    try:
        p = service.set_proposal_status(s, pid, body.status, error=body.error)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.IllegalTransition as e:
        raise HTTPException(status_code=400, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 进入 queued 即投递派发消息：覆盖 draft→queued 提交、failed→queued 重投、
    # analyzing→queued 超时回退三条入队边，无需各调用方各自记得发消息。
    if p is not None and str(p.status) == "queued":
        _dispatch_proposal(pid, getattr(p, "current_round", 0) or 0,
                           mq.REASON_QUEUED)
    return service._ser(p)


@app.post("/api/proposals/{pid}/claim")
def claim_proposal(pid: int, body: ProposalClaimIn | None = None,
                   s: Session = Depends(get_session)):
    """**原子**认领提案：queued/answered → analyzing，供 Worker 竞争消费。

    与 `PUT /status` 的关键区别：状态机对同状态迁移是幂等 no-op（返回 200），
    因此 PUT 无法仲裁并发认领 —— N 个 Worker 会全部「认领成功」。本端点把判定与
    写入压进单条条件 UPDATE，由数据库仲裁，恰好一个胜出。

    - 200：认领成功，返回提案（含 claimed_by / claimed_at 租约字段）
    - 409：已被他人持有或当前状态不可认领（与 400 非法迁移语义区分）
    - 404：提案不存在
    """
    agent = body.agent if body else ""
    try:
        p = service.claim_proposal(s, pid, agent=agent)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    if p is None:
        current = service.get_proposal(s, pid)
        raise HTTPException(
            status_code=409,
            detail=(f"proposal {pid} 无法认领：当前状态为 "
                    f"{current.status if current else 'unknown'}，"
                    f"仅 queued/answered 可认领"),
        )
    return service._ser(p)


@app.post("/api/proposals/{pid}/convert")
def convert_proposal(pid: int, body: ProposalConvertIn, s: Session = Depends(get_session)):
    """人工终审确认：把已收敛提案转化为 Story + 子 Task（Epic 96 P3）。

    保留人类最后一道闸 —— 不直接由 WorkBuddy/Worker 调 create_story，必须经
    本端点由人工/管理员确认后才转化。基于 converged_spec 生成 Story（description
    存原文）与子 Task（``- [ ]`` 清单项），回填 proposal.story_id 并推进
    converged → story_created。幂等：重复调用返回既有 Story，不重复创建。

    - 200：转化成功，返回 {proposal, story, tasks}
    - 400：提案非 converged / converged_spec 为空 / Epic 不属于提案项目
    - 404：提案或 Epic 不存在
    """
    try:
        story, tasks, p = service.convert_proposal_to_story(
            s, pid, epic_id=body.epic_id, title=body.title,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "proposal": service._ser(p),
        "story": service._ser(story),
        "tasks": [service._ser(t) for t in tasks],
    }


@app.delete("/api/proposals/{pid}")
def delete_proposal(pid: int, s: Session = Depends(get_session)):
    if not service.delete_proposal(s, pid):
        raise HTTPException(status_code=404, detail="proposal not found")
    return {"ok": True}


# ---------- Proposal → Ticket 异步转化（2026-08-08 文档 #59）----------

@app.get("/api/ticket-requests/pending")
def list_pending_ticket_requests(
    limit: int = Query(20, ge=1, le=200),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """Worker 拉取待认领转换请求（status=pending），跨项目全局池。

    权限（2026-08-09 review 修复）：REQUIRE_AUTH=1 下仅 admin 可访问
    （worker 服务账号须为 admin；避免任意登录用户枚举全部项目请求）。
    """
    uid, is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and not is_admin:
        raise HTTPException(status_code=403, detail="admin required")
    return [service._ser(r) for r in service.list_pending_ticket_requests(s, limit=limit)]


# 全局回收端点须声明在 /api/proposals/{pid} 动态路由之外的前缀之下，避免被 pid 捕获。
@app.post("/api/ticket-requests/reclaim-stale")
def reclaim_stale_ticket_requests(
    body: TicketReclaimIn | None = None, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """回收处理中超时的转换请求（processing 停滞 → failed），proposal 回退 converged。

    权限（2026-08-09 review 修复）：REQUIRE_AUTH=1 下仅 admin 可访问
    （worker 维护周期调用）。
    """
    uid, is_admin = _caller_uid_admin(authorization)
    if _auth_is_required() and not is_admin:
        raise HTTPException(status_code=403, detail="admin required")
    lease = (body.lease_seconds if body and body.lease_seconds is not None
             else service.DEFAULT_CLAIM_LEASE_SECONDS)
    try:
        ids = service.reclaim_stale_ticket_requests(s, lease_seconds=lease)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"reclaimed": ids, "count": len(ids), "lease_seconds": lease}


@app.post("/api/proposals/{pid}/ticket-requests", status_code=201)
def create_ticket_request(pid: int, body: ProposalTicketIn,
                          s: Session = Depends(get_session),
                          authorization: str | None = Header(None)):
    """用户点击「生成 ticket」：创建转换请求（幂等），proposal → ticket_preparing，
    发 MQ proposal.ticket_requested（worker 消费后拉起 agent 生成）。返回 201 请求。
    """
    uid, is_admin = _caller_uid_admin(authorization)
    p = service.get_proposal(s, pid)
    if not p:
        raise HTTPException(status_code=404, detail=f"proposal {pid} not found")
    if not is_admin and not service.user_is_project_member(s, p.project_id, uid):
        raise HTTPException(status_code=403, detail="project membership required")
    try:
        req = service.create_ticket_request(
            s, pid, type=body.type, epic_id=body.epic_id,
            story_id=body.story_id, title=body.title,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    mq.publish_workflow_event(mq.EVENT_TICKET_REQUESTED, "proposal", pid,
                              ref_id=req.id)
    return service._ser(req)


@app.get("/api/proposals/{pid}/ticket-requests")
def list_ticket_requests(pid: int, s: Session = Depends(get_session)):
    """列出提案的转换请求（前端轮询生成状态：pending/processing/done/failed）。"""
    try:
        return [service._ser(r) for r in service.list_ticket_requests(s, pid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/proposals/{pid}/ticket-requests/execute-by-type")
def execute_ticket_request_by_type(pid: int, body: TicketRequestExecuteIn,
                                   s: Session = Depends(get_session),
                                   authorization: str | None = Header(None)):
    """agent 经 MCP 调用（proposal_create_ticket）：按 (proposal, type) 定位/创建
    请求并执行转换，事务内创建实体 + 回填 + ticket_created。

    - 200：生成成功，返回 {proposal, request, ticket}
    - 409：请求正在生成中（processing），调用方轮询
    - 422：层级不合法 / 状态不符
    """
    uid, is_admin = _caller_uid_admin(authorization)
    p = service.get_proposal(s, pid)
    if not p:
        raise HTTPException(status_code=404, detail=f"proposal {pid} not found")
    if not is_admin and not service.user_is_project_member(s, p.project_id, uid):
        raise HTTPException(status_code=403, detail="project membership required")
    try:
        result = service.execute_ticket_request(
            s, pid, type=body.type, epic_id=body.epic_id,
            story_id=body.story_id, title=body.title,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=409 if "正在生成中" in str(e) else 422,
                            detail=str(e))
    mq.publish_workflow_event(mq.EVENT_TICKET_CREATED, "proposal", pid,
                              ref_id=result["request"]["id"])
    return result


@app.post("/api/proposals/{pid}/ticket-requests/{rid}/execute")
def execute_ticket_request_by_id(pid: int, rid: int,
                                 s: Session = Depends(get_session),
                                 authorization: str | None = Header(None)):
    """按显式 request id 执行转换（供测试/前端精确控制）。语义同 execute-by-type。"""
    uid, is_admin = _caller_uid_admin(authorization)
    p = service.get_proposal(s, pid)
    if not p:
        raise HTTPException(status_code=404, detail=f"proposal {pid} not found")
    if not is_admin and not service.user_is_project_member(s, p.project_id, uid):
        raise HTTPException(status_code=403, detail="project membership required")
    try:
        result = service.execute_ticket_request(
            s, pid, request_id=rid,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=409 if "正在生成中" in str(e) else 422,
                            detail=str(e))
    mq.publish_workflow_event(mq.EVENT_TICKET_CREATED, "proposal", pid,
                              ref_id=result["request"]["id"])
    return result


@app.post("/api/proposals/{pid}/ticket-requests/{rid}/fail")
def fail_ticket_request(pid: int, rid: int, body: TicketFailIn | None = None,
                        s: Session = Depends(get_session)):
    """worker 标记转换失败：request → failed，proposal ticket_preparing → converged。

    归属校验（2026-08-09 review 修复）：rid 必须属于 URL 的 proposal，
    防止跨 Proposal 误回退。
    """
    req = service.get_ticket_request(s, rid)
    if not req or req.proposal_id != pid:
        raise HTTPException(
            status_code=404,
            detail=f"ticket request {rid} 不属于 proposal {pid}",
        )
    try:
        req = service.fail_ticket_request(s, rid, error=(body.error if body else ""))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return service._ser(req)


@app.post("/api/proposals/{pid}/ticket-requests/{rid}/claim")
def claim_ticket_request(pid: int, rid: int, s: Session = Depends(get_session)):
    """**原子**认领转换请求：pending → processing（worker 竞争消费）。

    条件 UPDATE 由数据库仲裁，恰一个赢家；已被认领/已完成返回 409。
    归属校验（2026-08-09 review 修复）：rid 必须属于 URL 的 proposal。
    """
    req0 = service.get_ticket_request(s, rid)
    if not req0 or req0.proposal_id != pid:
        raise HTTPException(
            status_code=404,
            detail=f"ticket request {rid} 不属于 proposal {pid}",
        )
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


@app.post("/api/proposals/{pid}/questions", status_code=201)
def ask_proposal_questions(pid: int, body: ProposalAskIn, s: Session = Depends(get_session)):
    """Agent 回写一轮 open questions，并把提案推进到 awaiting（仅 analyzing 可提问）。

    同一 (proposal, round) 重复提交幂等复用既有轮次，兜底 at-least-once 重投。
    """
    try:
        return service.add_proposal_questions(
            s, proposal_id=pid, questions=body.questions, round_no=body.round,
            summary=body.summary, agent=body.agent,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.IllegalTransition as e:
        raise HTTPException(status_code=400, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/api/proposals/{pid}/rounds")
def list_proposal_rounds(pid: int, s: Session = Depends(get_session)):
    """按轮次正序返回澄清历史（含每轮问题与作答），供前端问答工作台渲染。"""
    try:
        return service.list_proposal_rounds(s, pid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.put("/api/proposal-questions/{qid}/answer")
def answer_proposal_question(qid: int, body: ProposalAnswerIn,
                             s: Session = Depends(get_session),
                             authorization: str | None = Header(None)):
    """用户逐条作答；unsure=true 表示标记不确定。整轮处理完自动推进 awaiting→answered。"""
    uid = _optional_user_id(authorization, s)
    try:
        q = service.answer_proposal_question(
            s, qid, answer=body.answer, unsure=body.unsure, user_id=uid,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 整轮答完会自动 awaiting→answered，此时立刻推一条消息触发下一轮澄清，
    # 免去「用户答完还要干等一个轮询周期」的延迟。
    p = service.get_proposal(s, q.proposal_id)
    if p is not None and str(p.status) == "answered":
        _dispatch_proposal(p.id, getattr(p, "current_round", 0) or 0,
                           mq.REASON_ANSWERED)
    return service._ser(q)


# ---------- Epic 22 Story 22.1: 审计日志中间件 ----------
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
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        uid = auth.parse_token(token)

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
        (r"^/api/comments/(\d+)", "comment"),
        (r"^/api/attachments/(\d+)", "attachment"),
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


def _write_audit_log(uid, action, entity_type, entity_id, path, request,
                    body_text, status_code, duration_ms) -> None:
    """在线程池中执行的审计落库（不阻塞事件循环）。"""
    try:
        with SessionLocal() as ss:
            service.create_audit_log(
                ss, user_id=uid, action=action, entity_type=entity_type or "unknown",
                entity_id=entity_id, method=request.method, path=path,
                ip_address=request.headers.get("x-forwarded-for", request.client.host if request.client else None),
                user_agent=request.headers.get("user-agent"),
                request_body=body_text, response_status=status_code, duration_ms=duration_ms,
            )
    except Exception:
        pass  # 不阻塞主流程


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

        with SessionLocal() as s:
            p = service.get_project(s, pid)
            if p is None:
                # Unknown project: let the endpoint return 404.
                return await call_next(request)
            uid, is_admin = _caller_uid_admin(request.headers.get("authorization"))
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
