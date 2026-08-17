"""Shared helpers for api.py and feature routers (Phase 5 split).

Phase 5 refactor: _xxx helpers from api.py extracted so that feature routers
(agentboard/features/<X>/router.py) can import them instead of relying on
api.py module globals.

api.py still re-exports these names (Phase 5 facade compat) for any external
caller that does ``from agentboard import api; api._current_user(...)``.
"""
from __future__ import annotations
import os
import re
import json
import time
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from . import service, auth, mq, cache
from .cache import get_cache, API_CACHE_TTL
from .features.projects.models import Sprint  # noqa: E402 — sprint_id 归属解析
from .database import get_session, SessionLocal

def _invalidate_stats_cache(project_id: int) -> None:
    """Invalidate project stats cache when data changes"""
    try:
        cache = get_cache()
        cache.delete(f"stats:{project_id}")
    except Exception:
        pass  # Non-critical, don't fail the request


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


_cors_origins = [
    x.strip() for x in os.getenv("AGENTBOARD_CORS_ORIGINS", "*").split(",") if x.strip()
]


_PERMISSION_RE = re.compile(r"^[a-z][a-z0-9_-]*(?::(?:[a-z0-9_*.-]+))+$")


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


def _require_project_owner(
    s: Session, project_id: int, authorization: str | None,
) -> None:
    # Explicitly preserve the documented local open-CRUD mode when no identity is supplied.
    if not authorization and not _auth_is_required():
        return
    user = _current_user(authorization, s, required_permission="api:write")
    if not user.is_admin and not service.user_is_project_owner(s, project_id, user.id):
        raise HTTPException(status_code=403, detail="project owner or admin required")


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


def _api_key_response(item) -> dict:
    return {
        "id": item.id, "name": item.name, "prefix": item.key_prefix,
        "permissions": auth.decode_permissions(item.permissions), "enabled": item.enabled,
        "created_at": item.created_at, "updated_at": item.updated_at,
        "last_used_at": item.last_used_at,
    }


def _probe_cli_sync(cmd: str, *, model: str = "", timeout: int = 8) -> tuple[bool, str]:
    """CLI 探测（dry-run，B-A2 / Epic 145 / Story 291 整改）。

    历史 RCE：原实现 ``subprocess.run(<cmd> --version)`` + ``cmd /c`` 回退，dev
    默认 ``REQUIRE_AUTH=0`` 匿名可调 → 任意命令执行。现改为 **dry-run**：
    - 仅做 ``{model}`` 占位符替换 + argv 解析 + 元字符校验，**不执行子进程**；
    - 实际判活交给 Worker 端本地 ``heartbeat.probe_cli``（受信进程，周期心跳）；
    - API 侧仅返回"将要执行的命令"供前端展示。

    返回 ``(ok, msg)``：``ok=True`` 表示命令通过校验可被 worker 执行（不代表
    CLI 真的存在/可用）；``ok=False`` 表示未配置 / 含危险字符 / 解析失败。
    ``timeout`` 入参保留向后兼容（dry-run 不耗时，忽略）。
    """
    from .worker import split_command
    from .core.service_helpers import validate_cli_command
    full = (cmd or "").strip()
    if not full:
        return False, "未配置 cli_command"
    # {model} 占位符替换（与 worker._probe_cli 同语义：空则移除）
    full = full.replace("{model}", (model or "").strip())
    if "{model}" in full:
        full = full.replace("{model}", "").strip()
    # B-A2: 元字符 / shell 启动器黑名单（替换后再校验，防 model 字段注入）
    try:
        validate_cli_command(full)
    except Exception as e:
        return False, f"blocked: {e}"
    # 解析 argv（不执行子进程）
    try:
        argv = split_command(full) + ["--version"]
    except ValueError as e:
        return False, f"命令解析失败：{e}"
    preview = " ".join(argv)[:120]
    return True, f"dry-run: {preview}"


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


def _ext_for_mime(mime: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
            "image/webp": ".webp"}.get(mime, ".png")


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
