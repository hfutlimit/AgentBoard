"""Agent ??????????Task 2??

??????Agent ?? WorkType ???????? CRUD ???
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from .models import AgentBehaviorConfig
from ...agent_runtime.behavior.models import AgentBehaviorConfigPayload
from ...agent_runtime.behavior.defaults import PRESET_VERSION
from ...core.common.models import utc_now


def _normalize_work_type(work_type: str | None) -> str | None:
    if not work_type:
        return None
    val = str(work_type).lower().strip()
    if "." in val:
        val = val.split(".")[-1]
    return val


def get_behavior_config_record(
    db: Session,
    project_id: int | None = None,
    agent_id: int | None = None,
    work_type: str | None = None,
) -> AgentBehaviorConfig | None:
    """? (project_id, agent_id, work_type) ?????????"""
    wt = _normalize_work_type(work_type)
    stmt = select(AgentBehaviorConfig).where(
        and_(
            AgentBehaviorConfig.project_id == project_id,
            AgentBehaviorConfig.agent_id == agent_id,
            AgentBehaviorConfig.work_type == wt,
        )
    )
    return db.scalars(stmt).first()


def get_behavior_payload(
    db: Session,
    project_id: int | None = None,
    agent_id: int | None = None,
    work_type: str | None = None,
) -> AgentBehaviorConfigPayload | None:
    """?????? AgentBehaviorConfigPayload??????? None?"""
    rec = get_behavior_config_record(db, project_id, agent_id, work_type)
    if not rec or not rec.config_json:
        return None
    try:
        data = json.loads(rec.config_json)
        return AgentBehaviorConfigPayload.model_validate(data)
    except Exception:
        return None


def upsert_behavior_config(
    db: Session,
    payload: AgentBehaviorConfigPayload,
    project_id: int | None = None,
    agent_id: int | None = None,
    work_type: str | None = None,
    preset_version: int = PRESET_VERSION,
) -> AgentBehaviorConfig:
    """????????????"""
    wt = _normalize_work_type(work_type)
    rec = get_behavior_config_record(db, project_id, agent_id, wt)
    raw_json = json.dumps(payload.model_dump(), ensure_ascii=False)

    if rec is None:
        rec = AgentBehaviorConfig(
            project_id=project_id,
            agent_id=agent_id,
            work_type=wt,
            config_json=raw_json,
            preset_version=preset_version,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(rec)
    else:
        rec.config_json = raw_json
        rec.preset_version = preset_version
        rec.updated_at = utc_now()

    db.commit()
    db.refresh(rec)
    return rec


def delete_behavior_config(
    db: Session,
    project_id: int | None = None,
    agent_id: int | None = None,
    work_type: str | None = None,
) -> bool:
    """??/??????????????"""
    rec = get_behavior_config_record(db, project_id, agent_id, work_type)
    if rec:
        db.delete(rec)
        db.commit()
        return True
    return False


def list_behavior_configs_for_project(
    db: Session,
    project_id: int,
) -> list[AgentBehaviorConfig]:
    """???????????????"""
    stmt = select(AgentBehaviorConfig).where(AgentBehaviorConfig.project_id == project_id)
    return list(db.scalars(stmt).all())


def list_behavior_configs_for_agent(
    db: Session,
    agent_id: int,
) -> list[AgentBehaviorConfig]:
    """?? Agent ????????????"""
    stmt = select(AgentBehaviorConfig).where(AgentBehaviorConfig.agent_id == agent_id)
    return list(db.scalars(stmt).all())
