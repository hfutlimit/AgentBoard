"""学习域 API router（Epic 140 切片 1）：agent 能力评分排行榜。

- GET /api/learning/agent-leaderboard?project_id=&task_type=&limit=
- GET /api/learning/outcomes?project_id=&task_id=&limit=
   （dashboard 明细 / 调试）

鉴权：与 search 系端点一致——存在 Authorization 时校验（api:read），
REQUIRE_AUTH=1 时由全局 middleware 强制；无 token 本地开发宽容放行。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from ...core.infrastructure.database import get_session
from ... import api_helpers  # _optional_user_id（统一鉴权 helper）
from . import service as learning_service

router = APIRouter(tags=["learning"])


@router.get("/api/learning/agent-leaderboard")
def agent_leaderboard_api(
    project_id: int | None = Query(None, description="按项目过滤（可选）"),
    task_type: str | None = Query(None, description="按任务类型过滤，如 dev/qa/design/bug"),
    limit: int = Query(50, ge=1, le=200),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    api_helpers._optional_user_id(authorization, s)
    return learning_service.agent_leaderboard(
        s, project_id=project_id, task_type=task_type, limit=limit,
    )


@router.get("/api/learning/outcomes")
def list_outcomes_api(
    project_id: int | None = Query(None),
    task_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    api_helpers._optional_user_id(authorization, s)
    return learning_service.list_outcomes(
        s, project_id=project_id, task_id=task_id, limit=limit,
    )
