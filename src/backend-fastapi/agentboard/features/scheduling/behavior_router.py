"""Agent 行为配置与纠错学习 API 路由（Task 18：BehaviorRouter）。

提供项目级、Agent 级、WorkType 级的行为配置 CRUD、实时 Prompt 预览与历史学习检索接口。
严格遵循 AgentBoard 项目与角色权限校验体系（_enforce_member_or_admin / _enforce_owner_or_admin）。
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
    project_id: Optional[int] = None
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
# 1. 实时 Prompt 预览 (Preview)
# -------------------------------------------------------------
@router.post("/api/agents/behavior/preview", response_model=BehaviorPreviewResponse)
@router.post("/api/projects/{project_id}/agents/behavior/preview", response_model=BehaviorPreviewResponse)
def preview_agent_behavior(
    req: BehaviorPreviewRequest,
    project_id: Optional[int] = None,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """根据指定的行为配置与上下文，实时预览 Prompt 渲染效果（无落库副作用）。"""
    target_project_id = project_id or req.project_id
    if target_project_id is not None:
        uid, is_admin = api_helpers._caller_uid_admin(authorization, s=s)
        api_helpers._enforce_member_or_admin(s, target_project_id, uid, is_admin)

    effective = behavior_resolver.resolve(
        project_id=target_project_id,
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
# 2. 项目级行为配置 (Project Behavior Config)
# -------------------------------------------------------------
@router.get("/api/projects/{project_id}/behavior", response_model=EffectiveBehaviorConfig)
def get_project_behavior(
    project_id: int,
    work_type: Optional[str] = Query(None, description="工作类型"),
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """获取指定项目的生效行为配置（需具备项目读取权限）。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s=s)
    api_helpers._enforce_member_or_admin(s, project_id, uid, is_admin)

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
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """保存项目级行为配置覆盖（需项目所有者或管理员权限）。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s=s)
    api_helpers._enforce_owner_or_admin(s, project_id, uid, is_admin)

    rec = upsert_behavior_config(
        s,
        payload=payload,
        project_id=project_id,
        agent_id=None,
        work_type=work_type,
    )
    return {"status": "ok", "id": rec.id, "project_id": project_id, "work_type": work_type}


@router.delete("/api/projects/{project_id}/behavior")
def reset_project_behavior(
    project_id: int,
    work_type: Optional[str] = Query(None, description="工作类型"),
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """重置项目级行为配置为系统默认（需项目所有者或管理员权限）。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s=s)
    api_helpers._enforce_owner_or_admin(s, project_id, uid, is_admin)

    deleted = delete_behavior_config(s, project_id=project_id, agent_id=None, work_type=work_type)
    return {"status": "ok", "deleted": deleted}


# -------------------------------------------------------------
# 3. Agent 级行为配置 (Agent Behavior Config)
# -------------------------------------------------------------
@router.get("/api/projects/{project_id}/agents/{agent_id}/behavior", response_model=EffectiveBehaviorConfig)
def get_agent_behavior(
    project_id: int,
    agent_id: int,
    work_type: Optional[str] = Query(None, description="工作类型"),
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """获取指定 Agent 在项目与指定 WorkType 下的生效行为配置（需项目读取权限）。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s=s)
    api_helpers._enforce_member_or_admin(s, project_id, uid, is_admin)

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
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """设置指定 Agent 在项目或工作类型下的行为覆盖（需项目管理权限）。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s=s)
    api_helpers._enforce_owner_or_admin(s, project_id, uid, is_admin)

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
        "work_type": work_type,
    }


@router.delete("/api/projects/{project_id}/agents/{agent_id}/behavior")
def reset_agent_behavior(
    project_id: int,
    agent_id: int,
    work_type: Optional[str] = Query(None, description="工作类型"),
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """重置指定 Agent 行为覆盖（需项目管理权限）。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s=s)
    api_helpers._enforce_owner_or_admin(s, project_id, uid, is_admin)

    deleted = delete_behavior_config(s, project_id=project_id, agent_id=agent_id, work_type=work_type)
    return {"status": "ok", "deleted": deleted}


# -------------------------------------------------------------
# 4. 项目经验与纠错知识库 (Project Learnings)
# -------------------------------------------------------------
@router.get("/api/projects/{project_id}/learnings")
def list_project_learnings(
    project_id: int,
    category: Optional[str] = Query(None, description="经验类别"),
    work_type: Optional[str] = Query(None, description="关联工作类型"),
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """获取项目的历史纠错与沉淀经验（需项目读取权限）。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s=s)
    api_helpers._enforce_member_or_admin(s, project_id, uid, is_admin)

    stmt = select(Learning).where(Learning.project_id == project_id)
    if category:
        stmt = stmt.where(Learning.category == category)
    if work_type:
        stmt = stmt.where(Learning.work_type == work_type)

    stmt = stmt.order_by(Learning.created_at.desc()).limit(50)
    records = s.scalars(stmt).all()

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
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]


@router.post("/api/projects/{project_id}/learnings", status_code=201)
def create_project_learning(
    project_id: int,
    req: CreateLearningRequest,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """手动或通过 Agent 沉淀一条项目纠错经验（需项目成员或管理员权限）。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s=s)
    api_helpers._enforce_member_or_admin(s, project_id, uid, is_admin)


    rec = Learning(
        project_id=project_id,
        agent_id=req.agent_id,
        work_type=req.work_type,
        category=req.category,
        summary=req.summary,
        lesson=req.lesson,
        tags_json=json.dumps(req.tags, ensure_ascii=False),
        confidence=req.confidence,
    )
    s.add(rec)
    s.commit()
    s.refresh(rec)
    return {"status": "ok", "id": rec.id}