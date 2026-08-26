import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DB_PATH = "_test_resolver_tmp.db"
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///./{DB_PATH}"

import pytest
from agentboard.core.infrastructure.database import SessionLocal, engine, init_db
from agentboard.features.projects.models import Agent, Project
from agentboard.agent_runtime.behavior.models import (
    AgentBehaviorConfigPayload,
    CollaborationBehavior,
    DocumentSourceConfig,
    LearningBehavior,
    PreparationBehavior,
)
from agentboard.agent_runtime.behavior.resolver import BehaviorResolver, merge_behavior_payload
from agentboard.agent_runtime.contract import WorkType
from agentboard.features.scheduling.behavior_service import upsert_behavior_config


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except OSError:
            pass
    init_db()
    yield
    engine.dispose()
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except OSError:
            pass


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_resolver_system_defaults():
    resolver = BehaviorResolver()
    eff = resolver.resolve(work_type=WorkType.IMPLEMENTATION)

    assert eff.preset == "agentboard-default"
    assert eff.preset_version == 1
    assert eff.preparation.sync_code is True
    assert eff.preparation.inspect_code is True
    assert eff.collaboration.leave_summary is True
    assert len(eff.document_sources) >= 1
    # system 是唯一来源；其他层不参与
    assert eff.sources["system"] is True
    assert eff.sources["project"] is False
    assert eff.sources["project_agent"] is False
    assert eff.sources["project_agent_work_type"] is False


def test_resolver_project_override_merges_field_by_field():
    resolver = BehaviorResolver()
    project_ov = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(sync_code=False, inspect_code=True),
        additional_instructions="Project guidelines here.",
    )

    eff = resolver.resolve(
        project_id=1,
        work_type=WorkType.IMPLEMENTATION,
        project_override=project_ov,
    )

    assert eff.preparation.sync_code is False
    assert eff.preparation.inspect_code is True
    assert eff.preparation.read_documents is True
    assert eff.collaboration.leave_summary is True
    assert eff.additional_instructions == "Project guidelines here."
    assert eff.sources["project"] is True


def test_resolver_agent_work_type_precedence():
    resolver = BehaviorResolver()

    project_ov = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(sync_code=False),
    )
    agent_ov = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(checkout_branch=True, sync_code=False),
    )
    agent_wt_ov = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(sync_code=True, checkout_branch=True),
        additional_instructions="Agent specific prompt",
    )

    eff = resolver.resolve(
        project_id=1,
        agent_id=10,
        work_type=WorkType.IMPLEMENTATION,
        project_override=project_ov,
        agent_override=agent_ov,
        agent_work_type_override=agent_wt_ov,
    )

    assert eff.preparation.sync_code is True
    assert eff.preparation.checkout_branch is True
    assert eff.additional_instructions == "Agent specific prompt"
    assert eff.sources["system"] is True
    assert eff.sources["project"] is True
    assert eff.sources["project_agent"] is True  # agent_override 映射到这一层
    assert eff.sources["project_agent_work_type"] is True  # agent_work_type_override 映射到这一层


def test_resolver_empty_document_sources_explicit_clear():
    resolver = BehaviorResolver()

    # System default has document sources
    default_eff = resolver.resolve(work_type=WorkType.IMPLEMENTATION)
    assert len(default_eff.document_sources) > 0

    # User explicitly sets document_sources = [] to clear all document reading
    clear_override = AgentBehaviorConfigPayload(
        document_sources=[],
    )
    eff = resolver.resolve(
        work_type=WorkType.IMPLEMENTATION,
        agent_override=clear_override,
    )

    # Must be truly empty list, not fallen back to system default!
    assert eff.document_sources == []


def test_resolver_empty_additional_instructions_clear():
    resolver = BehaviorResolver()

    project_ov = AgentBehaviorConfigPayload(
        additional_instructions="Project global policy",
    )

    # Agent explicitly sets "" to clear inherited instructions
    agent_ov = AgentBehaviorConfigPayload(
        additional_instructions="",
    )

    eff = resolver.resolve(
        project_id=1,
        agent_id=2,
        work_type=WorkType.IMPLEMENTATION,
        project_override=project_ov,
        agent_override=agent_ov,
    )

    assert eff.additional_instructions == ""


# -------------------------------------------------------------
# P1 修复：项目级 Agent 默认 Behavior 解析（DB 端）
# -------------------------------------------------------------
def test_resolver_picks_up_project_scoped_agent_default_from_db(db_session):
    """用户在 Project 上下文里给 Agent 设置默认 behavior，DB 落 (project_id, agent_id, work_type=None)。

    这条记录必须被 resolver 命中（修复前：resolver 只查 (project_id=None, agent_id, work_type)，
    永远不命中项目级 Agent 默认）。
    """
    p = Project(name=f"Proj_{uuid.uuid4().hex[:6]}")
    ag = Agent(agent_id=f"ag_{uuid.uuid4().hex[:8]}", name="Test Agent")
    db_session.add_all([p, ag])
    db_session.commit()

    # 模拟用户在 Project → Agent 设置默认：必须存 (project_id, agent_id, work_type=None)
    payload = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(checkout_branch=True, sync_code=True),
        additional_instructions="Project-scoped agent default",
    )
    upsert_behavior_config(db_session, payload, project_id=p.id, agent_id=ag.id, work_type=None)

    resolver = BehaviorResolver()
    eff = resolver.resolve(
        project_id=p.id,
        agent_id=ag.id,
        work_type=WorkType.IMPLEMENTATION,
        db=db_session,
    )

    # 必须命中（修复前：preparation 全是 system default，sync_code=True 但 checkout_branch=False）
    assert eff.preparation.checkout_branch is True
    assert eff.preparation.sync_code is True
    assert eff.additional_instructions == "Project-scoped agent default"
    assert eff.sources["project_agent"] is True
    # 步骤 4 因为 work_type 不为 None 但 DB 里没 (project, agent, work_type) 记录，不会触发
    assert eff.sources["project_agent_work_type"] is False


def test_resolver_project_agent_default_does_not_leak_across_projects(db_session):
    """项目 A 给 Agent 设的默认，不应泄漏到项目 B。"""
    pa = Project(name=f"ProjA_{uuid.uuid4().hex[:6]}")
    pb = Project(name=f"ProjB_{uuid.uuid4().hex[:6]}")
    ag = Agent(agent_id=f"ag_{uuid.uuid4().hex[:8]}", name="Test Agent")
    db_session.add_all([pa, pb, ag])
    db_session.commit()

    payload = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(checkout_branch=True),
        additional_instructions="Project A only",
    )
    upsert_behavior_config(db_session, payload, project_id=pa.id, agent_id=ag.id, work_type=None)

    resolver = BehaviorResolver()
    eff_b = resolver.resolve(
        project_id=pb.id,
        agent_id=ag.id,
        work_type=WorkType.IMPLEMENTATION,
        db=db_session,
    )

    # Project B 看不到 Project A 的 Agent 默认
    assert eff_b.preparation.checkout_branch is False
    assert eff_b.additional_instructions is None
    assert eff_b.sources["project_agent"] is False


def test_resolver_legacy_project_id_none_agent_preset_still_works(db_session):
    """旧的 ``project_id=None, agent_id, work_type=wt`` 记录仍可命中（向后兼容兜底）。

    修复后我们优先查 (project_id, agent_id, work_type=wt)，没命中才 fallback 到 (None, agent_id, work_type=wt)。
    """
    ag = Agent(agent_id=f"ag_{uuid.uuid4().hex[:8]}", name="Test Agent")
    db_session.add(ag)
    db_session.commit()

    # 旧的 global 记录（project_id=None, work_type=implementation）
    payload = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(sync_code=False),
        additional_instructions="Legacy global agent+wt override",
    )
    upsert_behavior_config(db_session, payload, agent_id=ag.id, work_type="implementation")

    resolver = BehaviorResolver()
    eff = resolver.resolve(
        project_id=999,  # 假设一个项目，但项目内没有 (project, agent, wt) 记录
        agent_id=ag.id,
        work_type=WorkType.IMPLEMENTATION,
        db=db_session,
    )

    # 命中 legacy 兜底
    assert eff.preparation.sync_code is False
    assert eff.additional_instructions == "Legacy global agent+wt override"
    assert eff.sources["legacy_agent_work_type"] is True


def test_resolver_project_agent_work_type_takes_precedence_over_project_agent_default(db_session):
    """(project, agent, work_type) 应覆盖 (project, agent) 默认。"""
    p = Project(name=f"Proj_{uuid.uuid4().hex[:6]}")
    ag = Agent(agent_id=f"ag_{uuid.uuid4().hex[:8]}", name="Test Agent")
    db_session.add_all([p, ag])
    db_session.commit()

    # 项目级 Agent 默认
    default_payload = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(sync_code=True, checkout_branch=True),
        additional_instructions="Project+agent default",
    )
    upsert_behavior_config(db_session, default_payload, project_id=p.id, agent_id=ag.id, work_type=None)

    # 项目级 Agent + WorkType 特定
    specific_payload = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(sync_code=False, checkout_branch=True),
        additional_instructions="Project+agent+wt specific",
    )
    upsert_behavior_config(db_session, specific_payload, project_id=p.id, agent_id=ag.id, work_type="implementation")

    resolver = BehaviorResolver()
    eff = resolver.resolve(
        project_id=p.id,
        agent_id=ag.id,
        work_type=WorkType.IMPLEMENTATION,
        db=db_session,
    )

    # WorkType-specific 覆盖 default
    assert eff.preparation.sync_code is False  # 来自 wt-specific
    assert eff.preparation.checkout_branch is True  # 继承自 default
    assert eff.additional_instructions == "Project+agent+wt specific"
    assert eff.sources["project_agent"] is True
    assert eff.sources["project_agent_work_type"] is True
    # 因为命中了 project+agent+wt，legacy 不会触发
    assert eff.sources["legacy_agent_work_type"] is False