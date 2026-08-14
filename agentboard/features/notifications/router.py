"""Notifications feature router (Phase 5 split from api.py)。

Phase 5:从 api.py 拆出的 FastAPI 路由。179 个端点按 2nd path segment 分组,
本文件包含本 feature 的所有 @router.X 端点。

老 import ``from agentboard import api; api.app`` 仍可用(api.py 末尾
``app.include_router(...)`` 装配所有 router)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ...core.infrastructure.database import get_session
from ... import service
from ... import api_helpers  # Phase 5: _current_user, _auth_is_required, etc.

router = APIRouter(tags=["notifications"])

@router.get("/api/notifications")
def list_notifications(
    s: Session = Depends(get_session),
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    authorization: str | None = Header(None),
):
    uid = api_helpers._current_user(authorization, s, required_permission="api:read").id
    items, total = service.list_notifications(s, uid, limit=limit, offset=offset, unread_only=unread_only)
    return {"items": [service._ser(n) for n in items], "total": total}

@router.get("/api/notifications/unread-count")
def unread_count(
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = api_helpers._current_user(authorization, s, required_permission="api:read").id
    _, total = service.list_notifications(s, uid, limit=1, unread_only=True)
    return {"count": total}

@router.post("/api/notifications/{nid}/read")
def mark_read(
    nid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = api_helpers._current_user(authorization, s, required_permission="api:write").id
    n = service.mark_notification_read(s, nid, uid)
    if not n:
        raise HTTPException(status_code=404, detail="notification not found")
    return service._ser(n)

@router.post("/api/notifications/read-all")
def mark_all_read(
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = api_helpers._current_user(authorization, s, required_permission="api:write").id
    count = service.mark_all_notifications_read(s, uid)
    return {"ok": True, "count": count}

@router.delete("/api/notifications/{nid}")
def delete_notification(
    nid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = api_helpers._current_user(authorization, s, required_permission="api:write").id
    if not service.delete_notification(s, nid, uid):
        raise HTTPException(status_code=404, detail="notification not found")
    return {"ok": True}

# ---------- Project Statistics ----------
# 配置化 TTL：全局默认 AGENTBOARD_CACHE_TTL，各端点可单独覆盖
# 统计端点默认回退到全局默认 TTL
