"""学习域 API router（Epic 140）：agent 能力评分排行榜 + L3 judge。

- GET  /api/learning/agent-leaderboard?project_id=&task_type=&limit=
- GET  /api/learning/outcomes?project_id=&task_id=&limit=
- POST /api/learning/judge/{task_id}      （手动触发 L3 judge，同步回填）
- GET  /api/learning/judge/status          （judge provider 状态 / daily quota）

鉴权：与 search 系端点一致——存在 Authorization 时校验（api:read），
REQUIRE_AUTH=1 时由全局 middleware 强制；无 token 本地开发宽容放行。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from ...core.exceptions import NotFound
from ...core.infrastructure.database import get_session
from ... import api_helpers  # _optional_user_id（统一鉴权 helper）
from . import judge as learning_judge
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


@router.post("/api/learning/judge/{task_id}")
def judge_task_api(
    task_id: int,
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """手动触发 L3 judge 并同步回填（幂等：重复触发按最新输入重算）。

    未配置 LLM 时降级为 deterministic 启发式评分（provider=deterministic）。
    """
    api_helpers._optional_user_id(authorization, s)
    result = learning_judge.judge_task(s, task_id)
    if result is None:
        raise NotFound(f"task {task_id} 无终态 outcome，无法 judge")
    s.commit()
    return result


@router.get("/api/learning/judge/status")
def judge_status_api(
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """judge 服务状态：provider 是否启用 / daily quota 用量（dashboard 标注用）。"""
    api_helpers._optional_user_id(authorization, s)
    return {
        "llm_enabled": learning_judge.is_judge_llm_enabled(),
        "provider": "llm" if learning_judge.is_judge_llm_enabled() else "deterministic",
        "daily_quota": learning_judge.daily_llm_quota(),
        "daily_llm_used": learning_judge._llm_daily_used(s),
    }
