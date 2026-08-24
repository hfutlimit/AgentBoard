"""Search feature router (Phase 5 split from api.py)。

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

router = APIRouter(tags=["search"])


@router.get("/api/search/stories")
def search_stories_api(
    q: str = Query(..., min_length=1, description="关键词"),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
):
    rows = service.search_stories(s, q=q, limit=limit)
    return [service._ser(x) for x in rows]


# 全局 Epic 关键词搜索（命令面板等场景，Epic v6.13）；路径用 /api/search/epics 避免与 /api/epics/{eid} 冲突

@router.get("/api/search/epics")
def search_epics_api(
    q: str = Query(..., min_length=1, description="关键词"),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
):
    rows = service.search_epics(s, q=q, limit=limit)
    return [service._ser(x) for x in rows]


# 全局 Sprint 关键词搜索（命令面板等场景，v6.14）；路径用 /api/search/sprints 避免与 /api/projects/{pid}/sprints 冲突

@router.get("/api/search/sprints")
def search_sprints_api(
    q: str = Query(..., min_length=1, description="关键词"),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
):
    rows = service.search_sprints(s, q=q, limit=limit)
    return [service._ser(x) for x in rows]


# 当前用户通知关键词搜索（命令面板等场景，v6.15）；通知属隐私数据，必须带鉴权且仅返回本人通知

@router.get("/api/search/notifications")
def search_notifications_api(
    q: str = Query(..., min_length=1, description="关键词"),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = api_helpers._current_user(authorization, s, required_permission="api:read").id
    rows = service.search_notifications(s, user_id=uid, q=q, limit=limit)
    return [service._ser(n) for n in rows]


# 全局 Agent 关键词搜索（命令面板等场景，Epic 131 v6.16）；路径用 /api/search/agents
# 避免与 /api/agents/{agent_id} 冲突；带鉴权（镜像 search_notifications）

@router.get("/api/search/agents")
def search_agents_api(
    q: str = Query(..., min_length=1, description="关键词"),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    api_helpers._current_user(authorization, s, required_permission="api:read")
    rows = service.search_agents(s, q=q, limit=limit)
    return [service._ser(x) for x in rows]


# 全局 Proposal 关键词搜索（命令面板等场景，Epic 132 v6.17）；路径用 /api/search/proposals
# 避免与 /api/proposals/{pid} 冲突；带鉴权 + 可见性收敛（镜像 search_notifications + list_proposals）

@router.get("/api/search/proposals")
def search_proposals_api(
    q: str = Query(..., min_length=1, description="关键词"),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = api_helpers._current_user(authorization, s, required_permission="api:read").id
    rows = service.search_proposals(s, q=q, limit=limit, user_id=uid)
    return [service._ser(x) for x in rows]


# 全局 Ticket 关键词搜索（命令面板等场景，Epic 133 v6.18）；路径用 /api/search/tickets
# 避免与提案下工单端点混淆；带鉴权 + 可见性收敛（镜像 search_proposals：按提案所属项目收敛）

@router.get("/api/search/tickets")
def search_tickets_api(
    q: str = Query(..., min_length=1, description="关键词"),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = api_helpers._current_user(authorization, s, required_permission="api:read").id
    rows = service.search_ticket_requests(s, q=q, limit=limit, user_id=uid)
    return rows  # 已含 _ser 全列 + 附加 project_id


# 全局定时计划关键词搜索（命令面板等场景，Epic 134 v6.19）；路径用 /api/search/schedules
# 避免与项目内 /api/projects/{pid}/schedules 混淆；带鉴权 + 可见性收敛（镜像 search_proposals）

@router.get("/api/search/schedules")
def search_schedules_api(
    q: str = Query(..., min_length=1, description="关键词"),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = api_helpers._current_user(authorization, s, required_permission="api:read").id
    rows = service.search_schedules(s, q=q, limit=limit, user_id=uid)
    return [service._ser(x) for x in rows]


# 全局执行记录关键词搜索（命令面板等场景，Epic 135 v6.20）；路径用 /api/search/runs
# 避免与 /api/schedules/{sid}/runs、/api/runs/{rid} 混淆；带鉴权 + 可见性收敛（镜像 search_schedules）

@router.get("/api/search/runs")
def search_runs_api(
    q: str = Query(..., min_length=1, description="关键词"),
    limit: int = Query(20, ge=1, le=50),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = api_helpers._current_user(authorization, s, required_permission="api:read").id
    return service.search_runs(s, q=q, limit=limit, user_id=uid)


