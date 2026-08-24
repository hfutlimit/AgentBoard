"""Auth feature router (Phase 5 split from api.py)。

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
from agentboard.schemas import *  # Phase 5: forward-ref-safe
import os
from ... import auth
from ... import api_helpers  # Phase 5: _current_user, _auth_is_required, etc.

router = APIRouter(tags=["auth"])


@router.post("/api/auth/register", status_code=201)
def register(body: AuthRegister, s: Session = Depends(get_session)):
    registration_open = os.getenv("AGENTBOARD_ALLOW_REGISTRATION", "1").lower() in {"1", "true", "yes"}
    if not registration_open and service.has_users(s):
        raise HTTPException(status_code=403, detail="registration is disabled")
    try:
        u = service.register_user(s, username=body.username, password=body.password)
    except service.Duplicate:
        raise HTTPException(status_code=409, detail=f"username '{body.username}' already exists")
    return {"id": u.id, "username": u.username, "is_admin": u.is_admin, "token": auth.make_token(u.id)}



@router.post("/api/auth/login")
def login(body: AuthLogin, s: Session = Depends(get_session)):
    u = service.authenticate_user(s, username=body.username, password=body.password)
    if u is None:
        raise HTTPException(status_code=401, detail="invalid username or password")
    return {"id": u.id, "username": u.username, "is_admin": u.is_admin, "token": auth.make_token(u.id)}



@router.get("/api/auth/me")
def me(authorization: str | None = Header(None), s: Session = Depends(get_session)):
    return api_helpers._user_response(api_helpers._current_user(authorization, s, required_permission="api:read"))



@router.patch("/api/auth/me")
def update_me(
    body: UserProfilePatch,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    user = api_helpers._current_user(authorization, s, required_permission="api:write")
    updated = service.update_user_profile(user=user, s=s, **body.model_dump(exclude_unset=True))
    return api_helpers._user_response(updated)



@router.post("/api/auth/change-password", status_code=204)
def change_password(
    body: PasswordChange,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    user = api_helpers._current_user(authorization, s, required_permission="api:write")
    try:
        service.change_user_password(
            s, user, current_password=body.current_password, new_password=body.new_password,
        )
    except service.InvalidValue as exc:
        raise HTTPException(status_code=400, detail=str(exc))



@router.post("/api/api-keys", status_code=201)
def create_api_key(body: ApiKeyCreate, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    user = api_helpers._current_user(authorization, s)
    item, plaintext = service.create_api_key(
        s, user_id=user.id, name=body.name, permissions=body.permissions,
        agent_ref=body.agent_ref,
    )
    return {**api_helpers._api_key_response(item, s), "key": plaintext}



@router.get("/api/api-keys")
def list_api_keys(authorization: str | None = Header(None), s: Session = Depends(get_session)):
    user = api_helpers._current_user(authorization, s)
    return {"items": [api_helpers._api_key_response(x, s) for x in service.list_api_keys(s, user_id=user.id)]}



@router.get("/api/api-keys/{api_key_id}")
def get_api_key(api_key_id: int, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    user = api_helpers._current_user(authorization, s)
    return api_helpers._api_key_response(api_helpers._need(service.get_api_key(s, user_id=user.id, api_key_id=api_key_id), "api key"), s)



@router.patch("/api/api-keys/{api_key_id}")
def update_api_key(body: ApiKeyPatch, api_key_id: int, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    user = api_helpers._current_user(authorization, s)
    item = api_helpers._need(service.get_api_key(s, user_id=user.id, api_key_id=api_key_id), "api key")
    updates = {
        "name": body.name,
        "enabled": body.enabled,
        "permissions": body.permissions,
    }
    if "agent_ref" in body.model_fields_set:
        updates["agent_ref"] = body.agent_ref
    updated = service.update_api_key(s, item, **updates)
    return api_helpers._api_key_response(updated, s)



@router.delete("/api/api-keys/{api_key_id}", status_code=204)
def revoke_api_key(api_key_id: int, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    user = api_helpers._current_user(authorization, s)
    if not service.revoke_api_key(s, user_id=user.id, api_key_id=api_key_id):
        raise HTTPException(status_code=404, detail="api key not found")


# ---------- Projects ----------

@router.get("/api/users/me/projects")
def list_my_projects(
    role: str | None = Query(None, pattern=r"^(owner|member)$"),
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
    authorization: str | None = Header(None), s: Session = Depends(get_session),
):
    user = api_helpers._current_user(authorization, s, required_permission="api:read")
    rows, total = service.list_user_projects(s, user.id, role=role, limit=limit, offset=offset)
    return {
        "items": [{**service._ser(project), "membership_role": membership_role} for project, membership_role in rows],
        "total": total,
    }

