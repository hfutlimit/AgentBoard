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
    is_private: bool | None = None


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


def _optional_user_id(authorization: str | None, s: Session) -> int | None:
    if not authorization:
        return None
    return _current_user(authorization, s, required_permission="api:read").id


def _auth_is_required() -> bool:
    return os.getenv("AGENTBOARD_REQUIRE_AUTH", "0").lower() in {"1", "true", "yes"}


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
    """列表 API：public 项目所有人可见；private 项目仅成员可见"""
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
    p = service.create_project(s, name=body.name, key=body.key, description=body.description, is_private=body.is_private)
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
    """获取项目：private 项目仅成员可见（系统管理员始终可见）"""
    p = _need(service.get_project(s, pid), "project")
    uid = _optional_user_id(authorization, s)
    user = service.get_user(s, uid) if uid else None
    if p.is_private and not (user and user.is_admin) and not service.user_is_project_member(s, pid, uid):
        raise HTTPException(status_code=403, detail="access denied: private project")
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
    try:
        result = service.set_status(s, tid, body.status)
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


@app.delete("/api/tasks/{tid}")
def delete_task(tid: int, s: Session = Depends(get_session)):
    task = service.get_task(s, tid)
    pid = task.project_id if task else None
    if not service.delete_task(s, tid):
        raise HTTPException(status_code=404, detail="task not found")
    if pid:
        _invalidate_stats_cache(pid)
    return {"ok": True}


# ---------- Bulk Task Operations ----------
@app.post("/api/tasks/bulk-update")
def bulk_update_tasks(body: BulkTaskUpdate, s: Session = Depends(get_session)):
    """批量更新任务：支持 status / priority / sprint_id"""
    results = []
    errors = []
    affected_pids = set()
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
        return [service._ser(x) for x in service.list_comments(s, tid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/tasks/{tid}/comments", status_code=201)
def create_comment(
    tid: int, body: CommentIn, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    try:
        comment = service.create_comment(s, task_id=tid, author=body.author, content=body.content)
        mentioned_user_ids: set[int] = set()
        for username in re.findall(r"@([A-Za-z0-9_.-]{1,64})", body.content):
            mentioned = service.get_user_by_username(s, username)
            if mentioned and mentioned.id not in mentioned_user_ids:
                mentioned_user_ids.add(mentioned.id)
                service.create_notification(
                    s, user_id=mentioned.id, notif_type="mentioned",
                    title=f"{body.author} 在评论中提到了你", content=body.content[:500],
                    link=f"/task/{tid}",
                )
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
    author_id: int | None = None


class DocumentPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    content: str | None = None
    type: str | None = None
    status: str | None = None


class DocumentCommentIn(BaseModel):
    author: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)
    author_id: int | None = None


class DocumentCommentPatch(BaseModel):
    content: str = Field(min_length=1)
    author: str = Field(min_length=1, max_length=100)


@app.post("/api/documents", status_code=201)
def create_document(body: DocumentIn, s: Session = Depends(get_session)):
    """新建文档（title/content/type/project_id 必填，status 默认 draft）。"""
    try:
        d = service.create_document(
            s, project_id=body.project_id, title=body.title, content=body.content,
            type=body.type, status=body.status, epic_id=body.epic_id,
            story_id=body.story_id, author_id=body.author_id,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(d)


@app.get("/api/documents")
def list_documents(
    project_id: int | None = Query(None),
    type: str | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
):
    """列出文档，支持按 project_id / type / status 过滤与关键词搜索。默认按 updated_at 倒序。"""
    try:
        rows = service.list_documents(
            s, project_id=project_id, type=type, status=status, q=q,
            limit=limit, offset=offset,
        )
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return [service._ser(d) for d in rows]


@app.get("/api/documents/{did}")
def get_document(did: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_document(s, did), "document"))


@app.patch("/api/documents/{did}")
def update_document(did: int, body: DocumentPatch, s: Session = Depends(get_session)):
    """编辑文档 title/content/type（状态流转请用 PUT /status）。"""
    try:
        r = service.update_document(s, did, **body.model_dump(exclude_none=True))
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
    ]:
        m = re.match(pattern, path)
        if m:
            entity_type = etype
            entity_id = int(m.group(1))
            break

    # 异步记录日志（避免阻塞响应）
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

    return response


@app.middleware("http")
async def project_access_middleware(request: Request, call_next):
    """Enforce project-scoped access control on all /api routes.

    Active only when ``AGENTBOARD_REQUIRE_AUTH=1`` (the Docker / production posture).
    Local open-CRUD mode (``REQUIRE_AUTH=0``) is intentionally left untouched.

    Rules:
    - Resolve the target project from the route (direct ``/api/projects/{pid}`` or via a
      child resource such as epic/story/task/sprint/schedule, by id or query param).
    - Routes that are not project-scoped pass through.
    - Reads (GET/HEAD): public projects are visible to every authenticated user; private
      projects are visible only to members and system admins.
    - Writes (POST/PUT/PATCH/DELETE): the project root (settings / deletion) requires the
      owner or an admin; sub-resources require membership or admin.
    - System admins (``is_admin``) always pass.
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
                return JSONResponse(status_code=401, content={"detail": "unauthorized"})
            if p.is_private:
                if is_admin:
                    return await call_next(request)
                if uid is None:
                    return JSONResponse(status_code=403, content={"detail": "access denied: private project"})
                if not service.user_is_project_member(s, pid, uid):
                    return JSONResponse(status_code=403, content={"detail": "access denied: private project"})
                if is_write and is_project_root:
                    try:
                        _enforce_owner_or_admin(s, pid, uid, is_admin)
                    except HTTPException as e:
                        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
                return await call_next(request)
            # Public project
            if is_write:
                try:
                    if is_project_root:
                        _enforce_owner_or_admin(s, pid, uid, is_admin)
                    else:
                        _enforce_member_or_admin(s, pid, uid, is_admin)
                except HTTPException as e:
                    return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
            return await call_next(request)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
