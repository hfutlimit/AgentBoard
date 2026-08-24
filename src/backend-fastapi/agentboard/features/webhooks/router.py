"""Webhooks feature router (Phase 5 split from api.py)。

Phase 5:从 api.py 拆出的 FastAPI 路由。179 个端点按 2nd path segment 分组,
本文件包含本 feature 的所有 @router.X 端点。

老 import ``from agentboard import api; api.app`` 仍可用(api.py 末尾
``app.include_router(...)`` 装配所有 router)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ...core.infrastructure.database import get_session
from ...core.application import service
from .schemas import WebhookIn
from ... import api_helpers  # Phase 5: _current_user, _auth_is_required, etc.

router = APIRouter(tags=["webhooks"])


@router.post("/api/webhooks", status_code=201)
def create_webhook(
    body: WebhookIn,
    project_id: int | None = Query(None),
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """创建 Webhook 配置。"""
    user = api_helpers._current_user(authorization, s) if authorization else None
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



@router.get("/api/webhooks")
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



@router.delete("/api/webhooks/{wid}")
def delete_webhook(wid: int, s: Session = Depends(get_session)):
    """删除 Webhook 配置。"""
    try:
        service.delete_webhook(s, wid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}



@router.patch("/api/webhooks/{wid}")
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
