import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DB_PATH = "_test_behavior_tmp.db"
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///./{DB_PATH}"

import pytest
from agentboard.core.infrastructure.database import SessionLocal, engine, init_db
from agentboard.features.projects.models import Agent, Project
from agentboard.processors.behavior.models import (
    AgentBehaviorConfigPayload,
    CollaborationBehavior,
    DocumentSourceConfig,
    LearningBehavior,
    PreparationBehavior,
)
from agentboard.features.scheduling.behavior_service import (
    delete_behavior_config,
    get_behavior_config_record,
    get_behavior_payload,
    list_behavior_configs_for_agent,
    list_behavior_configs_for_project,
    upsert_behavior_config,
)
from agentboard.features.scheduling.models import AgentBehaviorConfig


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


def test_upsert_and_get_project_behavior(db_session):
    p = Project(name=f"Proj_{uuid.uuid4().hex[:6]}")
    db_session.add(p)
    db_session.commit()

    payload = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(sync_code=True, inspect_code=True),
        collaboration=CollaborationBehavior(leave_summary=True),
        learning=LearningBehavior(qa_defect=False),
        additional_instructions="Project instruction test",
    )

    # 1. 创建 Project 覆盖
    rec = upsert_behavior_config(db_session, payload, project_id=p.id)
    assert rec.id is not None
    assert rec.project_id == p.id
    assert rec.agent_id is None
    assert rec.work_type is None

    # 2. 读取 Payload
    loaded = get_behavior_payload(db_session, project_id=p.id)
    assert loaded is not None
    assert loaded.preparation.sync_code is True
    assert loaded.learning.qa_defect is False
    assert loaded.additional_instructions == "Project instruction test"

    # 3. 更新 Project 覆盖
    payload.preparation.sync_code = False
    rec2 = upsert_behavior_config(db_session, payload, project_id=p.id)
    assert rec2.id == rec.id
    reloaded = get_behavior_payload(db_session, project_id=p.id)
    assert reloaded.preparation.sync_code is False


def test_upsert_and_get_agent_work_type_behavior(db_session):
    ag = Agent(agent_id=f"ag_{uuid.uuid4().hex[:8]}", name="Test Agent")
    db_session.add(ag)
    db_session.commit()

    payload = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(checkout_branch=True, sync_code=True),
        document_sources=[
            DocumentSourceConfig(type="mcp", source_id="jira-1", name="Jira MCP", scope="Core")
        ],
    )

    # 创建 Agent + WorkType 覆盖
    rec = upsert_behavior_config(db_session, payload, agent_id=ag.id, work_type="implementation")
    assert rec.agent_id == ag.id
    assert rec.work_type == "implementation"

    loaded = get_behavior_payload(db_session, agent_id=ag.id, work_type="implementation")
    assert loaded is not None
    assert loaded.preparation.checkout_branch is True
    assert len(loaded.document_sources) == 1
    assert loaded.document_sources[0].source_id == "jira-1"


def test_delete_behavior_config(db_session):
    p = Project(name=f"Proj_Del_{uuid.uuid4().hex[:6]}")
    db_session.add(p)
    db_session.commit()

    payload = AgentBehaviorConfigPayload()
    upsert_behavior_config(db_session, payload, project_id=p.id)
    assert get_behavior_config_record(db_session, project_id=p.id) is not None

    deleted = delete_behavior_config(db_session, project_id=p.id)
    assert deleted is True
    assert get_behavior_config_record(db_session, project_id=p.id) is None

    # 重复删除返回 False
    assert delete_behavior_config(db_session, project_id=p.id) is False


def test_list_behavior_configs(db_session):
    p = Project(name=f"Proj_List_{uuid.uuid4().hex[:6]}")
    db_session.add(p)
    db_session.commit()

    payload = AgentBehaviorConfigPayload()
    upsert_behavior_config(db_session, payload, project_id=p.id)
    upsert_behavior_config(db_session, payload, project_id=p.id, work_type="qa")

    configs = list_behavior_configs_for_project(db_session, project_id=p.id)
    assert len(configs) >= 2