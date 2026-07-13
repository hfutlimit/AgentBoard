"""AgentBoard REST API（纯 JSON，前后端分离的后端）。

独立运行：uvicorn agentboard.api:app --port 8000
供 Web 前端（fetch）与 MCP（httpx）调用；不含任何 HTML 渲染。
"""
import os
import re
from datetime import datetime
from contextlib import asynccontextmanager
from sqlalchemy import text
from fastapi import FastAPI, Depends, HTTPException, Query, Header, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from .database import get_session, init_db, SessionLocal
from . import service, auth
from .models import ALL_TYPES, ALL_STATUSES, ALL_PRIORITIES, ALL_SPRINT_STATUSES, ALL_SCHEDULE_TYPES, ALL_RUN_STATUSES


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.validate_runtime_security()
    init_db()
    yield


app = FastAPI(title="AgentBoard API", version="0.2", lifespan=lifespan)

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

# ---------- Rate Limiting ----------
import threading
import time
from collections import defaultdict

RATE_LIMIT = int(os.getenv("AGENTBOARD_RATE_LIMIT", "60"))  # requests per window
RATE_WINDOW = int(os.getenv("AGENTBOARD_RATE_WINDOW", "60"))  # seconds

_rate_limits: dict[str, list[float]] = defaultdict(list)
_rate_lock = threading.Lock()

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Simple token-bucket rate limiting: RATE_LIMIT requests per RATE_WINDOW seconds per IP."""
    if RATE_LIMIT <= 0:
        return await call_next(request)
    # Skip rate limiting for health/meta/auth endpoints
    if request.url.path in {"/api/meta", "/api/health", "/api/auth/register", "/api/auth/login"}:
        return await call_next(request)
    # Get client IP (handle proxies)
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
    if "," in ip:
        ip = ip.split(",")[0].strip()
    # Skip rate limiting for localhost/127.0.0.1 (test environments)
    if ip in {"127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"}:
        return await call_next(request)
    now = time.time()
    with _rate_lock:
        # Remove expired timestamps
        _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < RATE_WINDOW]
        if len(_rate_limits[ip]) >= RATE_LIMIT:
            resp = JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded: {RATE_LIMIT} requests per {RATE_WINDOW}s. Retry after {RATE_WINDOW}s."},
            )
            resp.headers["Retry-After"] = str(RATE_WINDOW)
            origin = request.headers.get("origin")
            if origin:
                resp.headers["Access-Control-Allow-Origin"] = origin
            return resp
        _rate_limits[ip].append(now)
    return await call_next(request)


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
        token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
        uid = auth.parse_token(token)
        with SessionLocal() as s:
            if not uid or service.get_user(s, uid) is None:
                resp = JSONResponse(status_code=401, content={"detail": "unauthorized"})
                origin = request.headers.get("origin")
                if origin:
                    resp.headers["Access-Control-Allow-Origin"] = origin
                    resp.headers["Access-Control-Allow-Credentials"] = "true"
                return resp
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


class StoryPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = None
    status: str | None = None


class TaskIn(BaseModel):
    project_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=300)
    type: str = "task"
    description: str = ""
    spec: str = ""
    priority: str = "medium"


class TaskPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    type: str | None = None
    description: str | None = None
    spec: str | None = None
    priority: str | None = None
    sprint_id: int | None = None


class CommentIn(BaseModel):
    author: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)


class StatusIn(BaseModel):
    status: str


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


class SchedulePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    schedule_type: str | None = None
    cron_expr: str | None = None
    enabled: bool | None = None
    next_run_at: str | None = None


class RunIn(BaseModel):
    task_id: int | None = None
    idempotency_key: str | None = Field(None, max_length=128)


class RunPatch(BaseModel):
    status: str | None = None
    output: str | None = None
    error_message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    task_id: int | None = None


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


_PERMISSION_RE = re.compile(r"^[a-z][a-z0-9_-]*(?::(?:[a-z0-9_*.-]+))+$")


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    permissions: list[str] = Field(default_factory=list, max_length=100)

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


def _current_user(authorization: str | None, s: Session):
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token)
    u = service.get_user(s, uid) if uid else None
    if u is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return u


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
    u = _current_user(authorization, s)
    return {"id": u.id, "username": u.username, "is_admin": u.is_admin}


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


# ---------- Projects ----------
@app.get("/api/projects")
def list_projects_ext(
    s: Session = Depends(get_session),
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
    authorization: str | None = Header(None),
):
    """列表 API：public 项目所有人可见；private 项目仅成员可见"""
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token) if token else None
    projects, total = service.list_accessible_projects(s, uid, limit=limit, offset=offset)
    return {"items": [service._ser(p) for p in projects], "total": total}


@app.post("/api/projects", status_code=201)
def create_project(
    body: ProjectIn, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    p = service.create_project(s, name=body.name, key=body.key, description=body.description)
    # 创建者自动成为项目 owner
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token) if token else None
    if uid:
        service.add_project_member(s, project_id=p.id, user_id=uid, role="owner")
    return service._ser(p)


@app.get("/api/projects/{pid}")
def get_project_ext(
    pid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """获取项目：private 项目仅成员可见"""
    p = _need(service.get_project(s, pid), "project")
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token) if token else None
    if p.is_private and not service.user_is_project_member(s, pid, uid):
        raise HTTPException(status_code=403, detail="access denied: private project")
    return service._ser(p)


@app.patch("/api/projects/{pid}")
def update_project(pid: int, body: ProjectPatchExtended, s: Session = Depends(get_session)):
    r = service.update_project(s, pid, **body.model_dump(exclude_none=True))
    return service._ser(_need(r, "project"))


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, s: Session = Depends(get_session)):
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
    _need(service.get_epic(s, eid), "epic")
    return service._ser(service.create_story(s, epic_id=eid, title=body.title, description=body.description))


@app.get("/api/stories/{sid}")
def get_story(sid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_story(s, sid), "story"))


@app.patch("/api/stories/{sid}")
def update_story(sid: int, body: StoryPatch, s: Session = Depends(get_session)):
    r = service.update_story(s, sid, **body.model_dump(exclude_none=True))
    return service._ser(_need(r, "story"))


@app.delete("/api/stories/{sid}")
def delete_story(sid: int, s: Session = Depends(get_session)):
    if not service.delete_story(s, sid):
        raise HTTPException(status_code=404, detail="story not found")
    return {"ok": True}


# ---------- Tasks ----------
@app.get("/api/stories/{sid}/tasks")
def list_tasks(sid: int, s: Session = Depends(get_session), limit: int = Query(100, ge=1, le=200),
               offset: int = Query(0, ge=0), sprint_id: int | None = Query(None)):
    return [service._ser(t) for t in service.list_tasks(s, sid, sprint_id=sprint_id, limit=limit, offset=offset)]


@app.post("/api/stories/{sid}/tasks", status_code=201)
def create_task(sid: int, body: TaskIn, s: Session = Depends(get_session)):
    story = _need(service.get_story(s, sid), "story")
    try:
        t = service.create_task(s, project_id=body.project_id, story_id=story.id,
                                title=body.title, type=body.type,
                                description=body.description, spec=body.spec,
                                priority=body.priority)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(t)


@app.get("/api/tasks/{tid}")
def get_task(tid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_task(s, tid), "task"))


@app.patch("/api/tasks/{tid}")
def update_task(tid: int, body: TaskPatch, s: Session = Depends(get_session)):
    try:
        r = service.update_task(s, tid, **body.model_dump(exclude_none=True))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(_need(r, "task"))


@app.put("/api/tasks/{tid}/status")
def set_status(tid: int, body: StatusIn, s: Session = Depends(get_session)):
    try:
        return service._ser(service.set_status(s, tid, body.status))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.IllegalTransition as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/tasks/{tid}")
def delete_task(tid: int, s: Session = Depends(get_session)):
    if not service.delete_task(s, tid):
        raise HTTPException(status_code=404, detail="task not found")
    return {"ok": True}


# ---------- Bulk Task Operations ----------
@app.post("/api/tasks/bulk-update")
def bulk_update_tasks(body: BulkTaskUpdate, s: Session = Depends(get_session)):
    """批量更新任务：支持 status / priority / sprint_id"""
    results = []
    errors = []
    for tid in body.task_ids:
        task = service.get_task(s, tid)
        if not task:
            errors.append({"task_id": tid, "error": "task not found"})
            continue
        try:
            updates = {}
            if body.status is not None:
                service.set_status(s, tid, body.status)
            if body.priority is not None:
                updates["priority"] = body.priority
            if body.sprint_id is not None:
                updates["sprint_id"] = body.sprint_id
            if updates:
                service.update_task(s, tid, **updates)
            results.append({"task_id": tid, "ok": True})
        except Exception as e:
            errors.append({"task_id": tid, "error": str(e)})
    return {"updated": results, "errors": errors}


@app.post("/api/tasks/bulk-delete")
def bulk_delete_tasks(body: BulkTaskDelete, s: Session = Depends(get_session)):
    """批量删除任务"""
    results = []
    errors = []
    for tid in body.task_ids:
        try:
            if service.delete_task(s, tid):
                results.append({"task_id": tid, "ok": True})
            else:
                errors.append({"task_id": tid, "error": "task not found"})
        except Exception as e:
            errors.append({"task_id": tid, "error": str(e)})
    return {"deleted": results, "errors": errors}


@app.post("/api/tasks/{tid}/spec/append")
def append_task_spec(tid: int, body: SpecAppendIn, s: Session = Depends(get_session)):
    return service._ser(_need(service.append_task_spec(s, tid, body.text), "task"))


# ---------- Comments ----------
@app.get("/api/tasks/{tid}/comments")
def list_comments(tid: int, s: Session = Depends(get_session)):
    try:
        return [service._ser(x) for x in service.list_comments(s, tid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/tasks/{tid}/comments", status_code=201)
def create_comment(tid: int, body: CommentIn, s: Session = Depends(get_session)):
    try:
        return service._ser(service.create_comment(s, task_id=tid, author=body.author,
                                                   content=body.content))
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
                 limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
                 s: Session = Depends(get_session)):
    try:
        rows = service.search_tasks(s, project_id=project_id, epic_id=epic_id,
                                    story_id=story_id, sprint_id=sprint_id,
                                    type=type, status=status,
                                    priority=priority, q=q, limit=limit, offset=offset)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return [service._ser(t) for t in rows]


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
        return service._ser(service.activate_sprint(s, sid))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/sprints/{sid}/complete", status_code=200)
def complete_sprint(sid: int, s: Session = Depends(get_session)):
    try:
        return service._ser(service.complete_sprint(s, sid))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))


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
        sch = service.create_schedule(s, project_id=pid, title=body.title,
                                      schedule_type=body.schedule_type,
                                      cron_expr=body.cron_expr)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(sch)


@app.get("/api/schedules/{sid}")
def get_schedule(sid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_schedule(s, sid), "schedule"))


@app.patch("/api/schedules/{sid}")
def update_schedule(sid: int, body: SchedulePatch, s: Session = Depends(get_session)):
    try:
        r = service.update_schedule(s, sid, **body.model_dump(exclude_none=True))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
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
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token) if token else None
    if not service.user_is_project_owner(s, pid, uid):
        u = service.get_user(s, uid) if uid else None
        if not (u and u.is_admin):
            raise HTTPException(status_code=403, detail="only owner or admin can add members")
    try:
        user_id = body.get("user_id") or (service.get_user_by_username(s, body.get("username")) or {}).get("id")
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
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    current_uid = auth.parse_token(token) if token else None
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
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    current_uid = auth.parse_token(token) if token else None
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
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token) if token else None
    if not uid:
        raise HTTPException(status_code=401, detail="unauthorized")
    items, total = service.list_notifications(s, uid, limit=limit, offset=offset, unread_only=unread_only)
    return {"items": [service._ser(n) for n in items], "total": total}


@app.get("/api/notifications/unread-count")
def unread_count(
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token) if token else None
    if not uid:
        raise HTTPException(status_code=401, detail="unauthorized")
    _, total = service.list_notifications(s, uid, limit=1, unread_only=True)
    return {"count": total}


@app.post("/api/notifications/{nid}/read")
def mark_read(
    nid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token) if token else None
    if not uid:
        raise HTTPException(status_code=401, detail="unauthorized")
    n = service.mark_notification_read(s, nid, uid)
    if not n:
        raise HTTPException(status_code=404, detail="notification not found")
    return service._ser(n)


@app.post("/api/notifications/read-all")
def mark_all_read(
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token) if token else None
    if not uid:
        raise HTTPException(status_code=401, detail="unauthorized")
    count = service.mark_all_notifications_read(s, uid)
    return {"ok": True, "count": count}


@app.delete("/api/notifications/{nid}")
def delete_notification(
    nid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token) if token else None
    if not uid:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not service.delete_notification(s, nid, uid):
        raise HTTPException(status_code=404, detail="notification not found")
    return {"ok": True}


# ---------- Project Statistics ----------
@app.get("/api/projects/{pid}/stats")
def project_stats(pid: int, s: Session = Depends(get_session)):
    _need(service.get_project(s, pid), "project")
    return service.get_project_stats(s, pid)


# ---------- Admin: Users ----------
@app.get("/api/admin/users")
def admin_list_users(
    s: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    authorization: str | None = Header(None),
):
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token) if token else None
    u = service.get_user(s, uid) if uid else None
    if not (u and u.is_admin):
        raise HTTPException(status_code=403, detail="admin only")
    users, total = service.list_users(s, limit=limit, offset=offset)
    return {"items": [service._ser(x) for x in users], "total": total}


@app.patch("/api/admin/users/{uid}")
def admin_update_user(
    uid: int, body: UserAdminPatch, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    current_uid = auth.parse_token(token) if token else None
    current_user = service.get_user(s, current_uid) if current_uid else None
    if not (current_user and current_user.is_admin):
        raise HTTPException(status_code=403, detail="admin only")
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
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token) if token else None
    u = service.get_user(s, uid) if uid else None
    if not (u and u.is_admin):
        raise HTTPException(status_code=403, detail="admin only")
    projects, total = service.list_all_projects_admin(s, limit=limit, offset=offset)
    return {"items": projects, "total": total}


@app.delete("/api/admin/projects/{pid}")
def admin_delete_project(
    pid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token) if token else None
    u = service.get_user(s, uid) if uid else None
    if not (u and u.is_admin):
        raise HTTPException(status_code=403, detail="admin only")
    if not service.delete_project(s, pid):
        raise HTTPException(status_code=404, detail="project not found")
    return {"ok": True}
