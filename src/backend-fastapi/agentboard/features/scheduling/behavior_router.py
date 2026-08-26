"""Agent 行为配置与纠错学习 API 路由（Task 18：BehaviorRouter）。

提供项目级、Agent 级、WorkType 级的行为配置 CRUD、实时 Prompt 预览与历史学习检索接口。
"""
from __future__ import annotations

import json
from typing import Any, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import api_helpers
from ...core.infrastructure.database import get_session
from ...agent_runtime.behavior.defaults import PRESET_VERSION, get_default_payload_for_work_type
from ...agent_runtime.behavior.models import (
    AgentBehaviorConfigPayload,
    EffectiveBehaviorConfig,
)
from ...agent_runtime.behavior.prompt_builder import prompt_builder
from ...agent_runtime.behavior.resolver import behavior_resolver
from ...core.common.models import utc_now
from ..learning.models import Learning
from .behavior_service import (
    delete_behavior_config,
    get_behavior_config_record,
    get_behavior_payload,
    list_behavior_configs_for_agent,
    list_behavior_configs_for_project,
    upsert_behavior_config,
)

router = APIRouter(tags=["Agent Behavior & Learning"])


class BehaviorPreviewRequest(BaseModel):
    work_type: str = "implementation"
    agent_id: Optional[int] = None
    payload: Optional[AgentBehaviorConfigPayload] = None
    context_summary: Optional[str] = None
    project_instructions: Optional[str] = None


class BehaviorPreviewResponse(BaseModel):
    work_type: str
    effective_config: EffectiveBehaviorConfig
    rendered_prompt: str


class CreateLearningRequest(BaseModel):
    category: str
    summary: str
    lesson: str
    work_type: Optional[str] = None
    agent_id: Optional[int] = None
    tags: list[str] = Field(default_factory=list)
    confidence: float = 1.0


# -------------------------------------------------------------
# 1. 实时 Prompt 预览
# -------------------------------------------------------------
@router.post("/api/projects/{project_id}/agents/behavior/preview", response_model=BehaviorPreviewResponse)
def preview_agent_behavior(
    project_id: int,
    req: BehaviorPreviewRequest,
    s: Session = Depends(get_session),
):
    """根据指定的行为配置与上下文，实时预览 Prompt 渲染效果（无落库副作用）。"""
    effective = behavior_resolver.resolve(
        project_id=project_id,
        agent_id=req.agent_id,
        work_type=req.work_type,
        agent_work_type_override=req.payload,
        db=s,
    )
    prompt = prompt_builder.build(
        work_type=req.work_type,
        behavior=effective,
        context={"raw_context_summary": req.context_summary} if req.context_summary else None,
        project_instructions=req.project_instructions,
        preview_mode=True,
    )
    return BehaviorPreviewResponse(
        work_type=req.work_type,
        effective_config=effective,
        rendered_prompt=prompt,
    )


# -------------------------------------------------------------
# 2. 项目级行为配置
# -------------------------------------------------------------
@router.get("/api/projects/{project_id}/behavior", response_model=EffectiveBehaviorConfig)
def get_project_behavior(
    project_id: int,
    work_type: Optional[str] = Query(None, description="工作类型"),
    s: Session = Depends(get_session),
):
    """获取指定项目的生效行为配置。"""
    return behavior_resolver.resolve(
        project_id=project_id,
        work_type=work_type or "implementation",
        db=s,
    )


@router.put("/api/projects/{project_id}/behavior")
def update_project_behavior(
    project_id: int,
    payload: AgentBehaviorConfigPayload,
    work_type: Optional[str] = Query(None, description="工作类型"),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """保存项目级行为配置覆盖。"""
    # TODO: enforce owner/admin role check
    rec = upsert_behavior_config(
        s,
        payload=payload,
        project_id=project_id,
        work_type=work_type,
    )
    return {"status": "ok", "id": rec.id, "project_id": project_id, "work_type": rec.work_type}


@router.delete("/api/projects/{project_id}/behavior")
def reset_project_behavior(
    project_id: int,
    work_type: Optional[str] = Query(None, description="工作类型"),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """重置项目级行为配置（恢复系统默认）。"""
    # TODO: enforce owner/admin role check
    deleted = delete_behavior_config(s, project_id=project_id, work_type=work_type)
    return {"status": "ok", "deleted": deleted}


# -------------------------------------------------------------
# 3. Agent 级 / Agent + WorkType 级行为配置
# -------------------------------------------------------------
@router.get("/api/projects/{project_id}/agents/{agent_id}/behavior", response_model=EffectiveBehaviorConfig)
def get_agent_effective_behavior(
    project_id: int,
    agent_id: int,
    work_type: Optional[str] = Query(None, description="工作类型"),
    s: Session = Depends(get_session),
):
    """获取指定 Agent 在指定工作项类型下的最终生效行为（三级合并后）。"""
    return behavior_resolver.resolve(
        project_id=project_id,
        agent_id=agent_id,
        work_type=work_type or "implementation",
        db=s,
    )


@router.put("/api/projects/{project_id}/agents/{agent_id}/behavior")
def update_agent_behavior(
    project_id: int,
    agent_id: int,
    payload: AgentBehaviorConfigPayload,
    work_type: Optional[str] = Query(None, description="工作类型"),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """保存 Agent 的专属行为配置覆盖。"""
    rec = upsert_behavior_config(
        s,
        payload=payload,
        project_id=project_id,
        agent_id=agent_id,
        work_type=work_type,
    )
    return {
        "status": "ok",
        "id": rec.id,
        "project_id": project_id,
        "agent_id": agent_id,
        "work_type": rec.work_type,
    }


@router.delete("/api/projects/{project_id}/agents/{agent_id}/behavior")
def reset_agent_behavior(
    project_id: int,
    agent_id: int,
    work_type: Optional[str] = Query(None, description="工作类型"),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """重置 Agent 的行为配置覆盖（继承项目或系统默认）。"""
    deleted = delete_behavior_config(
        s,
        project_id=project_id,
        agent_id=agent_id,
        work_type=work_type,
    )
    return {"status": "ok", "deleted": deleted}


# -------------------------------------------------------------
# 4. 项目历史纠错学习 (Learnings)
# -------------------------------------------------------------
@router.get("/api/projects/{project_id}/learnings")
def list_project_learnings(
    project_id: int,
    category: Optional[str] = Query(None, description="分类过滤"),
    work_type: Optional[str] = Query(None, description="类型过滤"),
    s: Session = Depends(get_session),
):
    """获取项目的历史纠错经验与经验教训列表。"""
    stmt = select(Learning).where(Learning.project_id == project_id)
    if category:
        stmt = stmt.where(Learning.category == category)
    if work_type:
        stmt = stmt.where(Learning.work_type == work_type)
    stmt = stmt.order_by(Learning.created_at.desc())

    records = list(s.scalars(stmt).all())
    return [
        {
            "id": r.id,
            "project_id": r.project_id,
            "agent_id": r.agent_id,
            "work_type": r.work_type,
            "category": r.category,
            "summary": r.summary,
            "lesson": r.lesson,
            "tags": json.loads(r.tags_json) if r.tags_json else [],
            "confidence": r.confidence,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in records
    ]


@router.post("/api/projects/{project_id}/learnings")
def create_project_learning(
    project_id: int,
    req: CreateLearningRequest,
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """手工沉淀或录入项目经验。"""
    item = Learning(
        project_id=project_id,
        agent_id=req.agent_id,
        work_type=req.work_type,
        category=req.category,
        summary=req.summary,
        lesson=req.lesson,
        tags_json=json.dumps(req.tags, ensure_ascii=False),
        confidence=req.confidence,
        created_at=utc_now(),
    )
    s.add(item)
    s.commit()
    s.refresh(item)
    return {
        "status": "ok",
        "id": item.id,
        "summary": item.summary,
        "category": item.category,
    }