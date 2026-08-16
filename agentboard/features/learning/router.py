"""学习域 API router（Epic 140）：agent 能力评分排行榜 + L3 judge + RAG/Playbook。

- GET  /api/learning/agent-leaderboard?project_id=&task_type=&limit=
- GET  /api/learning/outcomes?project_id=&task_id=&limit=
- POST /api/learning/judge/{task_id}      （手动触发 L3 judge，同步回填）
- GET  /api/learning/judge/status          （judge provider 状态 / daily quota）
- GET  /api/learning/project-playbook?project_id=   （项目 Playbook 读取）
- POST /api/learning/playbook/{project_id}/append   （手动追加 pattern）
- GET  /api/learning/recall?project_id=&spec=&top_k=（RAG recall 调试）

鉴权：与 search 系端点一致——存在 Authorization 时校验（api:read），
REQUIRE_AUTH=1 时由全局 middleware 强制；无 token 本地开发宽容放行。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...core.exceptions import InvalidValue, NotFound
from ...core.infrastructure.database import get_session
from ... import api_helpers  # _optional_user_id（统一鉴权 helper）
from . import judge as learning_judge
from . import memory as learning_memory
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


class PlaybookAppendIn(BaseModel):
    """手动追加 playbook pattern（管理员整理用，切片 3 Story 268 项 8）。"""

    task_type: str = "dev"
    summary: str
    outcome: str = "success"  # success / fail


@router.get("/api/learning/project-playbook")
def project_playbook_api(
    project_id: int = Query(..., description="项目 ID"),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """读取项目 Playbook（Worker prompt 注入 / 前端视图数据源）。"""
    api_helpers._optional_user_id(authorization, s)
    if not _project_exists(s, project_id):
        raise NotFound(f"project {project_id} not found")
    return learning_memory.get_playbook(s, project_id=project_id)


@router.post("/api/learning/playbook/{project_id}/append")
def playbook_append_api(
    project_id: int,
    body: PlaybookAppendIn,
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """手动追加 playbook pattern（幂等：同内容不重复；管理员/成员可整理）。"""
    api_helpers._optional_user_id(authorization, s)
    if not _project_exists(s, project_id):
        raise NotFound(f"project {project_id} not found")
    if body.outcome not in ("success", "fail"):
        raise InvalidValue("outcome must be success or fail")
    pb = learning_memory.update_playbook(
        s,
        project_id=project_id,
        task_type=body.task_type,
        summary=body.summary,
        outcome=body.outcome,
    )
    if pb is None:
        raise InvalidValue("playbook 更新失败（详见服务端日志）")
    s.commit()
    return {"project_id": project_id, "version": pb.version}


@router.get("/api/learning/recall")
def recall_api(
    project_id: int = Query(..., description="项目 ID"),
    spec: str = Query(..., description="查询文本（task spec / 描述摘要）"),
    top_k: int = Query(8, ge=1, le=20),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """RAG recall 调试端点：给定 spec 返回项目内相似 episodes（成功/失败分组）。"""
    api_helpers._optional_user_id(authorization, s)
    if not _project_exists(s, project_id):
        raise NotFound(f"project {project_id} not found")
    hits = learning_memory.recall_episodes(
        s, project_id=project_id, task_spec=spec, top_k=top_k,
    )
    return {
        "project_id": project_id,
        "query": spec[:500],
        "hits": hits,
        "count": len(hits),
        "injectable": learning_memory.build_recall_section(hits),
    }


def _project_exists(s: Session, project_id: int) -> bool:
    from ..projects.models import Project

    return s.get(Project, project_id) is not None
