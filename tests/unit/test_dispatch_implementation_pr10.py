"""PR-10 dispatch_implementation_task 单测。

覆盖：
1. _agent_type_for 基础映射（dev→codex, design→workbuddy, ...）
2. _excluded_prior_agent_ids：查 active/completed 的 agent_registry_id
3. _online_agents_for_type：online+enabled+executor_type 匹配
4. _pick_implementation_agent：随机选 + 排除历史
5. dispatch_implementation_task 主路径：
   - 选 agent → 状态 in_progress → publish task.assigned
   - 4 字段齐全（agent_id, agent_type, worker_id, workload_type）
   - TaskAssignment 写入
6. 失败语义：无候选返 None 不抛异常；有 TaskAssignment 时 skip
7. 同 task 历史 assignee 被排除（"task 和 qa 不同 agent"）

运行：
    cd <repo>
    PYTHONPATH=src/backend-fastapi python -m pytest tests/unit/test_dispatch_implementation_pr10.py -q
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard.core.common.enums import ItemType, Status
from agentboard.core.common.models import Base, utc_now
from agentboard.core.exceptions import InvalidValue
from agentboard.core.infrastructure.messaging import rabbitmq as mq_mod
from agentboard.features.identity.service import register_user
from agentboard.features.projects.models import (
    Agent, AgentInstance, Epic, Project, ProjectMember, Story, Worker,
)
from agentboard.features.scheduling.models import TaskAssignment
from agentboard.features.scheduling.service import (
    _agent_type_for,
    _excluded_prior_agent_ids,
    _online_agents_for_type,
    _pick_implementation_agent,
    dispatch_implementation_task,
    resolve_worker_for_agent,
    publish_workflow_event_for_agent,
)
from agentboard.features.work_items import service as task_service
from agentboard.features.work_items.models import Task


# ---------- fixtures ----------

@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
        engine.dispose()


@pytest.fixture
def broker(db_session):
    """在 dispatch_implementation_task 触发 publish_workflow_event 时
    把消息收到 in-memory broker 而不是真的发到 RabbitMQ。"""
    b = mq_mod.InMemoryWorkflowBroker()
    b.declare_topology()
    b.declare_agent_queue("dev-pc-01")  # 多数测试的 worker_id
    b.declare_agent_queue("dev-pc-02")  # 少数测试用 unique worker
    publisher = mq_mod.WorkflowPublisher(broker=b)
    mq_mod.set_workflow_publisher(publisher)
    yield b
    mq_mod.set_workflow_publisher(None)


# ---------- helpers ----------

def _setup_user_project(db_session) -> tuple[int, int]:
    username = f"u-{uuid.uuid4().hex[:8]}"
    u = register_user(db_session, username=username, password="test1234")
    p = Project(
        key=f"P-{uuid.uuid4().hex[:6].upper()}",
        name="PR-10 test project",
    )
    db_session.add(p); db_session.commit(); db_session.refresh(p)
    db_session.add(ProjectMember(project_id=p.id, user_id=u.id, role="owner"))
    db_session.commit()
    return u.id, p.id


def _setup_worker_agent(db_session, agent_id: str, tool: str, user_id: int,
                       worker_id: str = "dev-pc-01") -> Tuple[Agent, Worker, AgentInstance]:
    """建 worker + agent + AgentInstance（online, last_heartbeat=now）。

    worker_id 默认 dev-pc-01（broker fixture 已 declare 这个 queue）。
    不同测试可显式传不同 worker_id 避免冲突，但本 helper 的多数测试
    用默认即可。
    """
    w = Worker(
        worker_id=worker_id, hostname="test-host", status="active",
        last_heartbeat=utc_now(),
    )
    db_session.add(w); db_session.commit()
    a = Agent(
        agent_id=agent_id, name=agent_id, user_id=user_id,
        cli_command="", model="", enabled=True, online=True,
        last_heartbeat=utc_now(), roles="[]",
    )
    db_session.add(a); db_session.commit()
    inst = AgentInstance(
        worker_id=worker_id, agent_id=agent_id,
        cli_command="", model="", auth_key="", enabled=True, online=True,
        last_heartbeat=utc_now(), executor_type=tool,
    )
    db_session.add(inst); db_session.commit()
    db_session.refresh(a); db_session.refresh(w); db_session.refresh(inst)
    return a, w, inst


def _setup_task(db_session, project_id: int, story_id: int, type_: str,
                assignee_id: int | None = None) -> int:
    t = task_service.create_task(
        db_session, project_id=project_id, story_id=story_id,
        title=f"PR-10 {type_} task", type=type_,
        assignee_id=assignee_id,
    )
    return t.id


def _setup_epic_story(db_session, project_id: int) -> int:
    """建 epic + story 链，返回 story_id。"""
    e = Epic(project_id=project_id, title="PR-10 epic", description="")
    db_session.add(e); db_session.commit(); db_session.refresh(e)
    s = Story(epic_id=e.id, title="PR-10 story", description="")
    db_session.add(s); db_session.commit(); db_session.refresh(s)
    return s.id


# ---------- 1. _agent_type_for ----------

def test_agent_type_dev_task_is_codex():
    assert _agent_type_for("dev", "task") == "codex"

def test_agent_type_design_task_is_workbuddy():
    assert _agent_type_for("design", "task") == "workbuddy"

def test_agent_type_qa_task_is_workbuddy():
    assert _agent_type_for("qa", "task") == "workbuddy"

def test_agent_type_bug_task_is_codex():
    assert _agent_type_for("bug", "task") == "codex"

def test_agent_type_dev_rework_is_codex():
    assert _agent_type_for("dev", "rework") == "codex"

def test_agent_type_review_not_dispatched_returns_none():
    """review 走 assign_task_reviewer 旧路径，dispatch 返 None。"""
    assert _agent_type_for("dev", "review") is None
    assert _agent_type_for("design", "review") is None

def test_agent_type_unknown_combination_returns_none():
    assert _agent_type_for("unknown_type", "task") is None


# ---------- 2. _excluded_prior_agent_ids ----------

def test_excluded_prior_returns_active_assignment_agent(db_session):
    """有 active TaskAssignment 的 agent_id 在 excluded 里。"""
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "dev", assignee_id=user_id)
    agent, _, _ = _setup_worker_agent(db_session, "codex-dev-1", "codex", user_id)

    # 写 active TaskAssignment
    ta = TaskAssignment(
        task_id=task_id, agent_registry_id=agent.id, user_id=user_id,
        source="schedule", status="active", active_slot=1,
    )
    db_session.add(ta); db_session.commit()

    excluded = _excluded_prior_agent_ids(db_session, task_id)
    assert agent.id in excluded


def test_excluded_prior_returns_completed_assignment_agent(db_session):
    """completed TaskAssignment 也算历史。"""
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "dev", assignee_id=user_id)
    agent, _, _ = _setup_worker_agent(db_session, "codex-dev-1", "codex", user_id)
    ta = TaskAssignment(
        task_id=task_id, agent_registry_id=agent.id, user_id=user_id,
        source="schedule", status="completed", active_slot=2,  # 不同 slot
    )
    db_session.add(ta); db_session.commit()
    assert agent.id in _excluded_prior_agent_ids(db_session, task_id)


def test_excluded_prior_ignores_released_cancelled(db_session):
    """released / cancelled 不算历史。"""
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "dev", assignee_id=user_id)
    agent, _, _ = _setup_worker_agent(db_session, "codex-dev-1", "codex", user_id)
    for i, st in enumerate(("released", "cancelled")):
        ta = TaskAssignment(
            task_id=task_id, agent_registry_id=agent.id, user_id=user_id,
            source="manual", status=st, active_slot=3 + i,  # 不同 slot 避免 UNIQUE
        )
        db_session.add(ta); db_session.commit()
    assert agent.id not in _excluded_prior_agent_ids(db_session, task_id)


# ---------- 3. _online_agents_for_type ----------

def test_online_agents_filters_by_executor_type(db_session):
    user_id, _ = _setup_user_project(db_session)
    a1, _, _ = _setup_worker_agent(
        db_session, "codex-dev-1", "codex", user_id,
        worker_id="dev-pc-01",
    )
    a2, _, _ = _setup_worker_agent(
        db_session, "workbuddy-1", "workbuddy", user_id,
        worker_id="dev-pc-02",  # 不同 worker_id 避免 UNIQUE
    )
    codex_agents = _online_agents_for_type(db_session, "codex")
    workbuddy_agents = _online_agents_for_type(db_session, "workbuddy")
    assert {a.id for a in codex_agents} == {a1.id}
    assert {a.id for a in workbuddy_agents} == {a2.id}


def test_online_agents_excludes_offline(db_session):
    user_id, _ = _setup_user_project(db_session)
    a, _, _ = _setup_worker_agent(db_session, "codex-dev-1", "codex", user_id)
    a.online = False
    db_session.commit()
    assert _online_agents_for_type(db_session, "codex") == []


def test_online_agents_excludes_disabled(db_session):
    user_id, _ = _setup_user_project(db_session)
    a, _, _ = _setup_worker_agent(db_session, "codex-dev-1", "codex", user_id)
    a.enabled = False
    db_session.commit()
    assert _online_agents_for_type(db_session, "codex") == []


# ---------- 4. _pick_implementation_agent ----------

def test_pick_returns_agent_and_instance_for_dev_task(db_session):
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "dev", user_id)
    codex, w, inst = _setup_worker_agent(db_session, "codex-1", "codex", user_id)

    picked = _pick_implementation_agent(db_session,
                                       db_session.get(Task, task_id), "task")
    assert picked is not None
    agent, instance = picked
    assert agent.id == codex.id
    assert instance.worker_id == w.worker_id


def test_design_role_agent_can_execute_qa_when_it_did_not_implement_upstream_dev(
    db_session,
):
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "qa", user_id)
    design_agent, _, _ = _setup_worker_agent(
        db_session, "design-agent", "workbuddy", user_id,
    )
    design_agent.roles = '["design"]'
    db_session.commit()

    picked = _pick_implementation_agent(
        db_session, db_session.get(Task, task_id), "task",
    )
    assert picked is not None
    assert picked[0].id == design_agent.id


def test_pick_excludes_prior_agent(db_session):
    """QA 不得由其上游 Dev 的实现 Agent 执行。"""
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    dev_task_id = _setup_task(db_session, project_id, story_id, "dev", user_id)
    task_id = _setup_task(db_session, project_id, story_id, "qa", user_id)
    task_service.add_task_dependency(
        db_session, task_id=task_id, depends_on_id=dev_task_id,
    )
    # codex-1 实现过 QA 的上游 Dev task。
    codex1, _, _ = _setup_worker_agent(
        db_session, "codex-1", "workbuddy", user_id, worker_id="dev-pc-01",
    )
    ta = TaskAssignment(
        task_id=dev_task_id, agent_registry_id=codex1.id, user_id=user_id,
        source="schedule", status="completed", active_slot=1,
    )
    db_session.add(ta); db_session.commit()
    # codex-2 也 workbuddy role，但没历史
    codex2, _, _ = _setup_worker_agent(
        db_session, "codex-2", "workbuddy", user_id, worker_id="dev-pc-02",
    )

    # 选 random 100 次，必须每次都是 codex-2
    chosen = set()
    for _ in range(50):
        picked = _pick_implementation_agent(
            db_session, db_session.get(Task, task_id), "task",
        )
        if picked:
            chosen.add(picked[0].agent_id)
    assert chosen == {"codex-2"}, f"应只选 codex-2，实际 {chosen}"


def test_try_assign_task_rejects_upstream_dev_agent_for_qa(db_session):
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    dev_task_id = _setup_task(db_session, project_id, story_id, "dev", user_id)
    qa_task_id = _setup_task(db_session, project_id, story_id, "qa", user_id)
    task_service.add_task_dependency(
        db_session, task_id=qa_task_id, depends_on_id=dev_task_id,
    )
    dev_agent, _, _ = _setup_worker_agent(
        db_session, "dev-agent", "codex", user_id,
    )
    db_session.add(TaskAssignment(
        task_id=dev_task_id,
        agent_registry_id=dev_agent.id,
        user_id=user_id,
        source="schedule",
        status="completed",
        active_slot=1,
    ))
    db_session.get(Task, dev_task_id).status = Status.DONE.value
    db_session.commit()

    with pytest.raises(InvalidValue, match="upstream_dev_implementer"):
        task_service.try_assign_task(
            db_session,
            qa_task_id,
            user_id=user_id,
            agent_registry_id=dev_agent.id,
            source="schedule",
            workload_type="task",
        )


def test_pick_returns_none_when_no_candidate(db_session):
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "dev", user_id)
    # 没注册 codex agent
    assert _pick_implementation_agent(
        db_session, db_session.get(Task, task_id), "task",
    ) is None


def test_pick_returns_none_when_unknown_workload(db_session):
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "dev", user_id)
    # workload_type="review" 不在 _DISPATCH_AGENT_TYPE 里
    assert _pick_implementation_agent(
        db_session, db_session.get(Task, task_id), "review",
    ) is None


# ---------- 5. dispatch_implementation_task 主路径 ----------

def test_dispatch_publishes_task_assigned_with_all_fields(db_session, broker):
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "dev", user_id)
    codex, _, _ = _setup_worker_agent(db_session, "codex-1", "codex", user_id)

    # 清 broker
    for q in list(broker._queues.keys()):  # type: ignore[attr-defined]
        broker._queues[q].clear()  # type: ignore[attr-defined]

    result = dispatch_implementation_task(db_session, task_id)
    assert result is not None
    task, inst = result
    assert task.status == Status.IN_PROGRESS.value
    assert task.current_assignment_id is not None

    # 验事件（worker queue 用实际 worker_id，不是 hardcode 的 dev-pc-01）
    worker_queue = f"agentboard.workflow.agent.{inst.worker_id}"
    msgs = [
        mq_mod.WorkflowMessage.from_bytes(b)
        for b in broker._queues[worker_queue]  # type: ignore[attr-defined]
    ]
    assert len(msgs) == 1
    m = msgs[0]
    assert m.event == "task.assigned"
    # 4 字段齐全（PR-10 核心：缺 agent_type → .NET DLQ）
    assert m.agent_type == "codex"
    # agent_id 字段是 PR-11 范围（WorkflowMessage 加 agent_id 字段），
    # PR-10 publish 时虽然传了 agent_id kwarg，但 Python WorkflowMessage
    # dataclass 还没这个字段。PR-11 加字段后再验 body.agent_id。
    assert m.workload_type == "task"


def test_dispatch_no_candidate_leaves_task_in_todo(db_session, broker):
    """无候选：task 留 todo，并持久化 deferred 原因。"""
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "dev", user_id)
    # 没 codex agent

    result = dispatch_implementation_task(db_session, task_id)
    assert result is None
    db_session.refresh(db_session.get(Task, task_id))
    task = db_session.get(Task, task_id)
    assert task.status == Status.TODO.value
    assert '"code": "no_runnable_agent"' in task.assignment_deferred_reason
    assert task.assignment_deferred_at is not None


def test_dispatch_skips_when_already_in_progress(db_session, broker):
    """task 已经 in_progress → 不重复派。"""
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "dev", user_id)
    codex, _, _ = _setup_worker_agent(db_session, "codex-1", "codex", user_id)

    # 第一次 dispatch
    dispatch_implementation_task(db_session, task_id)
    # 第二次 dispatch 应 skip（task 已 in_progress）
    result2 = dispatch_implementation_task(db_session, task_id)
    assert result2 is None


def test_dispatch_writes_task_assignment_record(db_session, broker):
    """TaskAssignment active 记录被正确写入。"""
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "dev", user_id)
    codex, _, _ = _setup_worker_agent(db_session, "codex-1", "codex", user_id)

    dispatch_implementation_task(db_session, task_id)
    db_session.expire_all()
    ta = (
        db_session.query(TaskAssignment)
        .filter(TaskAssignment.task_id == task_id)
        .first()
    )
    assert ta is not None
    assert ta.status == "active"
    assert ta.source == "schedule"
    assert ta.agent_registry_id == codex.id
    assert ta.user_id == user_id


# ---------- 8. P0-2 preferred executor (2026-09-01) ----------

def test_pick_prefers_workbuddy_for_design(db_session):
    """Design 任务在 workbuddy/codex 都在线时优先选 workbuddy。

    回归背景：preferred 过滤之前 _pick_implementation_agent 只按 capability
    score 排序（无 needed_capabilities 时 coverage 全是 1.0），最后按
    load/id 落位 —— Design 可能派给 codex。
    """
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "design", user_id)
    wb, _, _ = _setup_worker_agent(
        db_session, "wb-main", "workbuddy", user_id, worker_id="dev-pc-01")
    _setup_worker_agent(
        db_session, "codex-main", "codex", user_id, worker_id="dev-pc-02")

    picked = _pick_implementation_agent(
        db_session, db_session.get(Task, task_id), "task")
    assert picked is not None
    assert picked[0].id == wb.id


def test_pick_prefers_codex_for_dev(db_session):
    """Dev 任务在 workbuddy/codex 都在线时优先选 codex。"""
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "dev", user_id)
    _setup_worker_agent(
        db_session, "wb-main", "workbuddy", user_id, worker_id="dev-pc-01")
    codex, _, _ = _setup_worker_agent(
        db_session, "codex-main", "codex", user_id, worker_id="dev-pc-02")

    picked = _pick_implementation_agent(
        db_session, db_session.get(Task, task_id), "task")
    assert picked is not None
    assert picked[0].id == codex.id


def test_pick_prefers_workbuddy_for_qa(db_session):
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "qa", user_id)
    wb, _, _ = _setup_worker_agent(
        db_session, "wb-main", "workbuddy", user_id, worker_id="dev-pc-01")
    _setup_worker_agent(
        db_session, "codex-main", "codex", user_id, worker_id="dev-pc-02")

    picked = _pick_implementation_agent(
        db_session, db_session.get(Task, task_id), "task")
    assert picked is not None
    assert picked[0].id == wb.id


def test_pick_falls_back_to_generic_pool_when_preferred_offline(db_session):
    """preferred executor 离线时回退其他 capable agent，不做硬性角色授权。"""
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "design", user_id)
    # workbuddy 离线（enabled=False → list_runnable_candidates 不收）
    _setup_worker_agent(
        db_session, "wb-main", "workbuddy", user_id, worker_id="dev-pc-01")
    codex, _, _ = _setup_worker_agent(
        db_session, "codex-main", "codex", user_id, worker_id="dev-pc-02")
    wb = db_session.query(Agent).filter(Agent.agent_id == "wb-main").one()
    wb.enabled = False
    db_session.commit()

    picked = _pick_implementation_agent(
        db_session, db_session.get(Task, task_id), "task")
    assert picked is not None
    assert picked[0].id == codex.id


def test_pick_scenario_executor_not_preferred_but_fallback_allowed(db_session):
    """executor_type=scenario（golden gate）不匹配 preferred，回退通用池。"""
    user_id, project_id = _setup_user_project(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    task_id = _setup_task(db_session, project_id, story_id, "dev", user_id)
    scenario, _, _ = _setup_worker_agent(
        db_session, "scenario-main", "scenario", user_id, worker_id="dev-pc-01")

    picked = _pick_implementation_agent(
        db_session, db_session.get(Task, task_id), "task")
    assert picked is not None
    assert picked[0].id == scenario.id
