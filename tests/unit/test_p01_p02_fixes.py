"""P0-1 / P0-2 修复的回归单测。

P0-1：dispatch_implementation_task 必须设 task.assignee_id
   （PR-10 follow-up：之前手写 assignment 漏了，导致 submit-review
   422 "only the assignee can submit"，happy path 第一步就卡住）

P0-2：assign-reviewer 发出的 task.review_requested event 必须有
   agent_type 字段（.NET WorkflowMessageMapper 必填，否则 DLQ）
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard.api import app
from agentboard.core.common.enums import ItemType, Status
from agentboard.core.common.models import Base, utc_now
from agentboard.core.infrastructure import messaging as mq_mod
from agentboard.features.identity.models import User
from agentboard.features.identity.service import register_user
from agentboard.features.projects.models import (
    Agent, AgentInstance, Project, ProjectMember, Story,
)
from agentboard.features.projects.service import create_project, create_story
from agentboard.features.scheduling.service import (
    dispatch_implementation_task, publish_workflow_event_for_agent,
    register_worker, resolve_agent_executor_type,
)
from agentboard.features.work_items import service as task_service
from agentboard.features.work_items.models import Task


# ---------- fixtures ----------

@pytest.fixture
def app_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    from agentboard.core.infrastructure import database
    database.engine = engine
    database.SessionLocal = Session
    database._session_factory = Session
    return engine


@pytest.fixture
def db_session(app_engine):
    Session = sessionmaker(bind=app_engine)
    s = Session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def broker(db_session):
    b = mq_mod.InMemoryWorkflowBroker()
    b.declare_topology()
    register_worker(db_session, worker_id="dev-pc-01", hostname="test")
    b.declare_agent_queue("dev-pc-01")
    publisher = mq_mod.WorkflowPublisher(broker=b)
    mq_mod.set_workflow_publisher(publisher)
    yield b
    mq_mod.set_workflow_publisher(None)


@pytest.fixture
def client(app_engine):
    from fastapi.testclient import TestClient
    return TestClient(app)


# ---------- helpers ----------

def _setup_user_project(db_session, role="owner"):
    u = register_user(db_session,
        username=f"u-{uuid.uuid4().hex[:6]}", password="test1234")
    p = create_project(db_session, name="t",
        key=f"P-{uuid.uuid4().hex[:6].upper()}")
    db_session.add(ProjectMember(project_id=p.id, user_id=u.id, role=role))
    db_session.commit()
    return u, p


def _setup_story(db_session, project_id):
    from agentboard.features.projects.service import create_epic
    e = create_epic(db_session, project_id=project_id, title="e", description="")
    return create_story(db_session, epic_id=e.id, title="s", description="").id


def _setup_codex_agent(db_session, user_id):
    a = Agent(
        agent_id="codex-A", name="codex-A", user_id=user_id,
        roles='["codex"]', cli_command="", model="",
        enabled=True, online=True, last_heartbeat=utc_now(),
    )
    db_session.add(a); db_session.flush()
    inst = AgentInstance(
        worker_id="dev-pc-01", agent_id="codex-A",
        cli_command="", model="", auth_key="",
        enabled=True, online=True, last_heartbeat=utc_now(),
        executor_type="codex",
    )
    db_session.add(inst); db_session.commit()
    return a


# ---------- P0-1: dispatch 设 assignee_id ----------

def test_dispatch_sets_assignee_id_for_submit_review(db_session, broker):
    """P0-1：dispatch 后 task.assignee_id 应是 agent.user_id，
    这样 submit-review 不会被 422 "only the assignee can submit" 拒。
    """
    user, project = _setup_user_project(db_session)
    story_id = _setup_story(db_session, project.id)
    codex_agent = _setup_codex_agent(db_session, user.id)

    dev_task = task_service.create_task(
        db_session, project_id=project.id, story_id=story_id,
        title="dev", type=ItemType.DEV.value,
        assignee_id=user.id, needs_human_confirmation=False,
    )

    # dispatch
    dispatch_implementation_task(db_session, dev_task.id)

    # 验 P0-1 关键修复点
    db_session.expire_all()
    t = db_session.get(Task, dev_task.id)
    assert t.assignee_id == codex_agent.user_id, \
        f"P0-1：dispatch 后 assignee_id 应是 codex-A user_id={codex_agent.user_id}，" \
        f"实际 {t.assignee_id}（PR-10 老 bug：assignee_id 没设，submit-review 422）"
    assert t.status == Status.IN_PROGRESS.value
    assert t.current_assignment_id is not None


# ---------- P0-2: resolve_agent_executor_type 从 roles 选 tool ----------

def test_resolve_agent_executor_type_picks_codex():
    """P0-2 helper：Agent.roles=['codex','reviewer'] → 返回 'codex'（不是 'reviewer'）。"""
    a = Agent(agent_id="x", name="x", user_id=1, roles='["codex","reviewer"]')
    assert resolve_agent_executor_type(a) == "codex"


def test_resolve_agent_executor_type_picks_workbuddy():
    a = Agent(agent_id="x", name="x", user_id=1, roles='["workbuddy","reviewer"]')
    assert resolve_agent_executor_type(a) == "workbuddy"


def test_resolve_agent_executor_type_only_reviewer_returns_empty():
    """只 roles=['reviewer']（没 executor type）→ 返回空串，caller fallback。"""
    a = Agent(agent_id="x", name="x", user_id=1, roles='["reviewer"]')
    assert resolve_agent_executor_type(a) == ""


def test_resolve_agent_executor_type_invalid_json_returns_empty():
    a = Agent(agent_id="x", name="x", user_id=1, roles="not json")
    assert resolve_agent_executor_type(a) == ""


# ---------- P0-2: review event 必须带 agent_type + workload_type=review ----------

def test_assign_reviewer_event_has_agent_type_and_workload_type(db_session, broker, client):
    """P0-2：/api/tasks/{id}/assign-reviewer 发出的 task.review_requested event
    body 必须有 agent_type + workload_type='review'。
    之前漏 → .NET mapper InvalidDataException → DLQ。
    """
    from agentboard.features.identity.service import register_user
    # (dead import removed)
    # 实测：直接通过 publish endpoint 触发，看 broker queue 收到的 event body

    # 1. setup 1 owner + 1 reviewer (workbuddy+reviewer)
    owner = register_user(db_session,
        username=f"o-{uuid.uuid4().hex[:6]}", password="test1234")
    reviewer_user = register_user(db_session,
        username=f"r-{uuid.uuid4().hex[:6]}", password="test1234")
    project = create_project(db_session, name="t",
        key=f"P-{uuid.uuid4().hex[:6].upper()}")
    db_session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="owner"))
    db_session.add(ProjectMember(project_id=project.id, user_id=reviewer_user.id, role="member"))
    db_session.commit()
    story_id = _setup_story(db_session, project.id)
    # roles 为空也可承担 review；executor_type 只决定物理执行器。
    a = Agent(agent_id="reviewer-wb", name="r", user_id=reviewer_user.id,
              roles="[]", cli_command="", model="",
              enabled=True, online=True, last_heartbeat=utc_now())
    db_session.add(a); db_session.flush()
    db_session.add(AgentInstance(worker_id="dev-pc-01",
        agent_id="reviewer-wb", cli_command="", model="", auth_key="",
        enabled=True, online=True, last_heartbeat=utc_now(),
        executor_type="workbuddy"))
    db_session.commit()

    # 2. 建 dev task → in_review（绕 review 链，PR-13b 思路）
    dev_task = task_service.create_task(
        db_session, project_id=project.id, story_id=story_id,
        title="dev", type=ItemType.DEV.value,
        assignee_id=owner.id, needs_human_confirmation=False,
    )
    from agentboard.features.work_items.service import set_status
    set_status(db_session, dev_task.id, Status.IN_PROGRESS, changed_by=owner.id)
    db_session.commit()
    set_status(db_session, dev_task.id, Status.IN_REVIEW, changed_by=owner.id)
    db_session.commit()

    # 3. clear broker，login，call assign-reviewer
    for q in broker._queues.values():
        q.clear()
    from agentboard.core.infrastructure.database import get_session
    token = client.post("/api/auth/login",
        json={"username": owner.username, "password": "test1234"}).json()["token"]
    r = client.post(f"/api/tasks/{dev_task.id}/assign-reviewer",
                    json={"count": 1},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (200, 201), f"assign-reviewer failed: {r.status_code} {r.text}"

    # 4. 验：直接 routing 到 reviewer-wb 的 direct queue（agent_id 路由）
    # 但 publish_workflow_event_for_agent 现在有 worker_id 优先 → 走
    # workflow.agent.dev-pc-01。两条都查（兼容两种 routing 路径）。
    found_msg = None
    for qn in ("agentboard.workflow.agent.dev-pc-01",
               "agentboard.workflow.broadcast"):
        for body in broker._queues.get(qn, []):
            msg = mq_mod.WorkflowMessage.from_bytes(body)
            if msg.event == "task.review_requested":
                found_msg = msg
                break
        if found_msg:
            break

    assert found_msg is not None, \
        f"task.review_requested 没找到，broker queues: " \
        f"{[(qn, len(qs)) for qn, qs in broker._queues.items()]}"
    # P0-2 关键：agent_type 必填
    assert found_msg.agent_type, \
        f"P0-2：review event 缺 agent_type（.NET mapper 会 DLQ），" \
        f"msg = ({found_msg.event}, agent_id={found_msg.agent_id}, " \
        f"workload_type={found_msg.workload_type})"
    assert found_msg.agent_type == "workbuddy", \
        f"reviewer agent 是 workbuddy role，agent_type 应是 workbuddy，实际 " \
        f"{found_msg.agent_type}"
    assert found_msg.workload_type == "review", \
        f"workload_type 应是 review（.NET mapper → WorkloadTypes.Review），" \
        f"实际 {found_msg.workload_type}"
