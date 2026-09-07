"""Integration tests for the #1716 MQ-less worker dispatch fallback.

These tests pin the **business invariant** ("a worker polling its
own agent gets only its agent's tasks; other agents' tasks are
invisible") rather than the internal implementation (which
function resolved the PK, which HTTP method was called, etc.).
That way future refactors (e.g. switching to a logical-ID filter
on the server side) don't break these tests for the wrong reason.

History:
- 247efcb / eece589 wired the poll path and fixed two P0 bugs.
- This file (P3 收口 review) rewrote the tests to be invariant-
  based, dropped the heartbeat-lookup helper, and removed the
  multi-PK confusion. Tests run at the service layer (the
  HTTP-router contract is exercised in test_search_tasks_agent_filter
  and the agentboard e2e suite, not here).
"""
from __future__ import annotations

import os

# DB URL must be set BEFORE the agentboard package is imported.
# Use a fresh, unique per-file path; the autouse fixture rebinds
# the module-level engine + SessionLocal to it via reset_engine()
# before init_db() runs alembic migrations.
os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_poll_assigned_v3_tmp.db"

import uuid
from unittest import mock

import pytest

from agentboard.core.common.enums import Status
from agentboard.core.infrastructure import database as _database
from agentboard.core.infrastructure.database import init_db, engine
from agentboard.features.identity.models import User
from agentboard.features.projects.models import Agent as AgentRow, Project
from agentboard.features.scheduling.models import TaskAssignment
from agentboard.features.work_items.models import Task


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    # Test isolation: the agentboard repo's tests share the process
    # so a single engine module-level. We rebind to a fresh per-file
    # path and force ``Base.metadata.create_all()`` instead of alembic
    # upgrade — alembic is a no-op on a freshly-cleared db (it can't
    # tell the db is empty from the alembic_version table alone after
    # a previous test wiped the file). ``create_all`` is enough for
    # the small surface these tests touch.
    db_path = os.path.abspath("_test_poll_assigned_v3_tmp.db")
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass
    from agentboard.core.infrastructure import database
    database.reset_engine()
    # Force schema creation (bypasses alembic which no-ops on an
    # empty db after a previous test wiped the file). Explicit model
    # imports ensure ``Base.metadata`` actually has the tables when
    # this test file is the first to run.
    from agentboard.core.common.models import Base
    from agentboard.features.identity import models as _id_models
    from agentboard.features.projects import models as _proj_models
    from agentboard.features.scheduling import models as _sched_models
    from agentboard.features.work_items import models as _wi_models
    Base.metadata.create_all(bind=database.engine)
    yield
    engine.dispose(close=True)


@pytest.fixture
def session():
    s = _database.SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def admin_user(session):
    suffix = uuid.uuid4().hex[:8]
    u = User(
        username=f"admin-{suffix}",
        password_hash="x",
        is_admin=True,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


@pytest.fixture
def project(session):
    suffix = uuid.uuid4().hex[:8]
    p = Project(name=f"poll-{suffix}", key=f"PL{suffix}", description="")
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@pytest.fixture
def two_agents(session, admin_user):
    """Two distinct logical agents both owned by admin user 4. The
    invariant test cares that a worker asking for Agent A's tasks
    only sees Agent A's, never B's.
    """
    suffix = uuid.uuid4().hex[:6]
    a = AgentRow(
        agent_id=f"poller-{suffix}-a",
        name="poller-a",
        user_id=admin_user.id,
        cli_command="codebuddy",
        model="hy4-preview",
        capabilities="[]",
    )
    b = AgentRow(
        agent_id=f"poller-{suffix}-b",
        name="poller-b",
        user_id=admin_user.id,
        cli_command="codebuddy",
        model="glm-5.3-flash",
        capabilities="[]",
    )
    session.add_all([a, b])
    session.commit()
    session.refresh(a)
    session.refresh(b)
    return a, b


def _make_in_progress_task(session, project, agent, *, title, admin_user_id):
    t = Task(
        project_id=project.id, title=title, type="design",
        status=Status.IN_PROGRESS.value, assignment_mode="claim",
        assignee_id=admin_user_id,
    )
    session.add(t)
    session.flush()
    a = TaskAssignment(
        task_id=t.id, agent_registry_id=agent.id, user_id=admin_user_id,
        source="claim", status="active", active_slot="active",
    )
    session.add(a)
    session.flush()
    t.current_assignment_id = a.id
    session.commit()
    session.refresh(t)
    return t


# ---------------------------------------------------------------------------
# INVARIANT 1: each worker's poll only sees its own agent's tasks
# ---------------------------------------------------------------------------


def test_assigned_polling_filter_isolates_agents_per_project(
    session, project, two_agents, admin_user,
):
    """Invariant: Agent A and Agent B both have an in-progress task
    in the same project. The service-layer
    ``search_tasks(assigned_agent_id=...)`` filter must return only
    Agent A's task when the worker asks for A, and only Agent B's
    task when it asks for B. This is the ownership guarantee that
    fixes the #1716 P0 silent-filter-drop bug."""
    from agentboard.core.application import service as core_service

    agent_a, agent_b = two_agents
    task_a = _make_in_progress_task(
        session, project, agent_a, title="a", admin_user_id=admin_user.id)
    task_b = _make_in_progress_task(
        session, project, agent_b, title="b", admin_user_id=admin_user.id)

    rows_a = core_service.search_tasks(
        session, project_id=project.id, status=Status.IN_PROGRESS.value,
        assigned_agent_id=agent_a.agent_id,
    )
    rows_b = core_service.search_tasks(
        session, project_id=project.id, status=Status.IN_PROGRESS.value,
        assigned_agent_id=agent_b.agent_id,
    )
    ids_a = {r.id for r in rows_a}
    ids_b = {r.id for r in rows_b}
    assert ids_a == {task_a.id}, ids_a
    assert ids_b == {task_b.id}, ids_b
    assert task_b.id not in ids_a
    assert task_a.id not in ids_b


def test_assigned_polling_filter_via_registry_id(
    session, project, two_agents, admin_user,
):
    """The ``agent_registry_id`` (int PK) filter path is the
    admin/internal one. Workers must NOT use it — they should pass
    the logical ``assigned_agent_id`` instead."""
    from agentboard.core.application import service as core_service

    agent_a, agent_b = two_agents
    task_a = _make_in_progress_task(
        session, project, agent_a, title="a",
        admin_user_id=admin_user.id)
    _make_in_progress_task(
        session, project, agent_b, title="b",
        admin_user_id=admin_user.id)

    rows = core_service.search_tasks(
        session, project_id=project.id, status=Status.IN_PROGRESS.value,
        agent_registry_id=agent_a.id,
    )
    assert {r.id for r in rows} == {task_a.id}


def test_assigned_polling_filter_does_not_match_other_statuses(
    session, project, two_agents, admin_user,
):
    """Agent A has a todo, an in-progress, and a done task all
    assigned to it. The assigned polling filter
    (``status=in_progress + assigned_agent_id=A``) must return only
    the in-progress one."""
    from agentboard.core.application import service as core_service

    agent_a, _ = two_agents
    in_progress = _make_in_progress_task(
        session, project, agent_a, title="a-in-prog",
        admin_user_id=admin_user.id)

    todo = Task(
        project_id=project.id, title="a-todo", type="design",
        status=Status.TODO.value, assignment_mode="claim",
    )
    session.add(todo)
    session.commit()
    done = Task(
        project_id=project.id, title="a-done", type="design",
        status=Status.DONE.value, assignment_mode="claim",
    )
    session.add(done)
    session.commit()

    rows = core_service.search_tasks(
        session, project_id=project.id, status=Status.IN_PROGRESS.value,
        assigned_agent_id=agent_a.agent_id,
    )
    assert {r.id for r in rows} == {in_progress.id}


# ---------------------------------------------------------------------------
# INVARIANT 2: Coordinator gate — polling only runs when MQ is disabled
# ---------------------------------------------------------------------------


def _make_minimal_coord(config):
    """Minimal ProcessorCoordinator with the attrs ``poll_once``
    touches."""
    from agentboard.processors.coordinator import ProcessorCoordinator

    coord = ProcessorCoordinator.__new__(ProcessorCoordinator)
    coord.config = config
    coord.client = mock.MagicMock()
    coord.client.get.return_value.status_code = 200
    coord.client.get.return_value.raise_for_status = mock.MagicMock()
    from agentboard.processors.contract import ExecutionStatus
    _success = mock.MagicMock()
    _success.status = ExecutionStatus.SUCCESS
    coord.dispatch = mock.MagicMock(return_value=_success)
    coord.registry = mock.MagicMock()
    coord.invoker = mock.MagicMock()
    coord._coordinator = None
    coord._work_executor = None
    return coord


def test_assigned_polling_skipped_when_mq_enabled(monkeypatch):
    """P3 review: ``_poll_assigned_in_progress`` only runs when
    ``config.mq.enabled`` is false. With MQ configured, the broker
    is the sole execution delivery path and polling would
    double-process. No heartbeat call — that was the P0-1 source
    of the previous commit's side-effect bug."""
    from agentboard.processors.config import ProcessorConfig

    config = ProcessorConfig(api_url="http://test", token="x",
                             agent_id="cb-1", task_poll_enabled=True,
                             mq=mock.MagicMock(enabled=True))
    coord = _make_minimal_coord(config)
    coord.client.get.return_value.json.return_value = []

    with mock.patch.object(coord, "_mapped_project_ids",
                           return_value=[3]):
        stats = coord.poll_once()

    assert stats.get("assigned_in_progress", 0) == 0
    coord.dispatch.assert_not_called()


def test_assigned_polling_runs_when_mq_disabled(monkeypatch):
    """P3 review: with ``config.mq.enabled = False`` the worker
    must pull its agent's in_progress tasks and dispatch them, using
    the logical ``assigned_agent_id`` (NOT a PK — workers never
    resolve PKs). No heartbeat call."""
    from agentboard.processors.config import ProcessorConfig

    config = ProcessorConfig(api_url="http://test", token="x",
                             agent_id="cb-1", task_poll_enabled=True,
                             mq=mock.MagicMock(enabled=False))
    coord = _make_minimal_coord(config)
    coord.client.get.return_value.json.return_value = [
        {"id": 11, "type": "design"},
        {"id": 22, "type": "design"},
    ]

    with mock.patch.object(coord, "_mapped_project_ids",
                           return_value=[3]):
        stats = coord.poll_once()

    assert coord.dispatch.call_count == 2
    assert stats.get("assigned_in_progress") == 2
    # No heartbeat identity lookup — that was the previous-commit bug.
    heartbeat_calls = [c for c in coord.client.post.call_args_list
                      if "/heartbeat" in str(c)]
    assert not heartbeat_calls, (
        f"must not call heartbeat as identity lookup; got {heartbeat_calls}"
    )
    get_args = coord.client.get.call_args_list
    assert get_args, "expected at least one GET /api/tasks call"
    saw_assigned = False
    for c in get_args:
        params = c.kwargs.get("params") or (c.args[1] if len(c.args) > 1 else {})
        if params.get("assigned_agent_id") == "cb-1":
            saw_assigned = True
            assert "agent_registry_id" not in params, (
                "worker should pass the logical agent_id; "
                f"agent_registry_id PK should not leak here: {params}"
            )
    assert saw_assigned, (
        f"expected one GET with assigned_agent_id=cb-1; got {get_args}"
    )


def test_task_poll_enabled_reads_from_config_not_env(monkeypatch):
    """P3 review: ``poll_once`` reads ``self.config.task_poll_enabled``,
    not ``os.getenv('AGENTBOARD_WORKER_TASK_POLL')`` — env is read
    once at boot, the config is the source of truth. This test
    explicitly checks that even when MQ is disabled (so polling
    would otherwise run), a config with ``task_poll_enabled=False``
    short-circuits the whole path."""
    from agentboard.processors.config import ProcessorConfig

    config = ProcessorConfig(api_url="http://test", token="x",
                             agent_id="cb-1",
                             task_poll_enabled=False,
                             mq=mock.MagicMock(enabled=False))
    coord = _make_minimal_coord(config)
    # Note: env var is "1" but config says False — config must win.
    monkeypatch.setenv("AGENTBOARD_WORKER_TASK_POLL", "1")

    stats = coord.poll_once()
    assert "tasks" not in stats or stats.get("tasks", 0) == 0
    assert stats.get("assigned_in_progress", 0) == 0
    coord.client.get.assert_not_called()


# ---------------------------------------------------------------------------
# INVARIANT 3: 轮询重投有上限（这条路径没有 MQ ack，也没有 WorkerWork
# 的 attempts<3，只能自己按 message_attempts 记账）
# ---------------------------------------------------------------------------


def _make_poll_coord(*, dispatch_status, task_payload=None):
    """Coordinator whose assigned-poll always sees one task and whose
    dispatch outcome is fixed to ``dispatch_status``."""
    from agentboard.processors.config import ProcessorConfig
    from agentboard.processors.contract import ExecutionResult, ExecutionStatus

    config = ProcessorConfig(api_url="http://test", token="x",
                             agent_id="cb-1", task_poll_enabled=True,
                             mq=mock.MagicMock(enabled=False))
    coord = _make_minimal_coord(config)
    # 类属性 dict 会被所有实例共享，测试必须自带一份。
    coord._msg_retries = {}
    coord._session_factory = None
    result = mock.MagicMock(spec=ExecutionResult)
    result.status = ExecutionStatus(dispatch_status)
    result.summary = "boom"
    coord.dispatch = mock.MagicMock(return_value=result)
    coord.client.get.return_value.json.return_value = [
        task_payload or {"id": 11, "type": "design", "current_assignment_id": 77},
    ]
    return coord


def _poll(coord):
    with mock.patch.object(coord, "_mapped_project_ids", return_value=[3]):
        coord.poll_once()
    return coord.dispatch.call_count


def test_assigned_poll_transient_failures_are_bounded():
    """连续瞬时失败最多重投 len(WORKFLOW_RETRY_BACKOFF_SECONDS) 次。"""
    from agentboard.processors.coordinator import WORKFLOW_RETRY_BACKOFF_SECONDS

    cap = len(WORKFLOW_RETRY_BACKOFF_SECONDS)
    coord = _make_poll_coord(dispatch_status="failed_transient")
    for _ in range(cap):
        _poll(coord)
    assert coord.dispatch.call_count == cap

    _poll(coord)  # 已死信 → 不再打扰
    assert coord.dispatch.call_count == cap


def test_assigned_poll_new_assignment_gets_fresh_budget():
    """回收后重新 arbitrate → 新 assignment id → 新的重试预算。"""
    from agentboard.processors.coordinator import WORKFLOW_RETRY_BACKOFF_SECONDS

    cap = len(WORKFLOW_RETRY_BACKOFF_SECONDS)
    coord = _make_poll_coord(dispatch_status="failed_transient")
    for _ in range(cap + 1):
        _poll(coord)
    assert coord.dispatch.call_count == cap

    coord.client.get.return_value.json.return_value = [
        {"id": 11, "type": "design", "current_assignment_id": 78},
    ]
    _poll(coord)
    assert coord.dispatch.call_count == cap + 1


def test_assigned_poll_success_clears_the_budget():
    """跑成功一次就清零，后续失败重新享有完整预算。"""
    from agentboard.processors.coordinator import WORKFLOW_RETRY_BACKOFF_SECONDS
    from agentboard.processors.contract import ExecutionStatus

    cap = len(WORKFLOW_RETRY_BACKOFF_SECONDS)
    coord = _make_poll_coord(dispatch_status="failed_transient")
    _poll(coord)
    assert coord.dispatch.call_count == 1

    coord.dispatch.return_value.status = ExecutionStatus.SUCCESS
    _poll(coord)
    assert coord.dispatch.call_count == 2

    coord.dispatch.return_value.status = ExecutionStatus.FAILED_TRANSIENT
    for _ in range(cap):
        _poll(coord)
    # 清零后又能重投 cap 次（1 次成功前的失败 + cap 次新预算）
    assert coord.dispatch.call_count == 2 + cap


def test_assigned_poll_permanent_failure_dead_letters_at_once():
    """永久失败没有重试意义 → 立刻死信，不再每 60s 起一次 agent。"""
    coord = _make_poll_coord(dispatch_status="failed_permanent")
    _poll(coord)
    assert coord.dispatch.call_count == 1
    _poll(coord)
    assert coord.dispatch.call_count == 1


def test_assigned_poll_inflight_duplicate_keeps_the_budget():
    """SKIPPED（进程内 in-flight 重复）既不消耗也不清零预算。"""
    coord = _make_poll_coord(dispatch_status="skipped")
    _poll(coord)
    assert coord.dispatch.call_count == 1
    assert coord._msg_retries == {}
    _poll(coord)
    assert coord.dispatch.call_count == 2


def _poll_with_assignment(coord, assignment_id):
    coord.client.get.return_value.json.return_value = [
        {"id": 11, "type": "design", "current_assignment_id": assignment_id},
    ]
    return _poll(coord)


def _run_assignment_round(coord, assignment_id) -> int:
    """把一份分配预算跑到头：轮询 cap+1 轮，返回实际派发次数（应为 cap）。"""
    from agentboard.processors.coordinator import WORKFLOW_RETRY_BACKOFF_SECONDS

    cap = len(WORKFLOW_RETRY_BACKOFF_SECONDS)
    before = coord.dispatch.call_count
    for _ in range(cap + 1):
        _poll_with_assignment(coord, assignment_id)
    return coord.dispatch.call_count - before


def test_assigned_poll_task_cycle_ceiling_stops_re_arbitration_loops():
    """P0-2 的实质风险：reclaim → 重新 arbitrate → 又一份 6 次预算，可以无限续。

    Task 级上限把总轮数封住：默认 3 份分配预算用完后 Worker 不再自动重投，
    哪怕分配 id 一直在换。
    """
    from agentboard.processors.config import ProcessorConfig
    from agentboard.processors.coordinator import WORKFLOW_RETRY_BACKOFF_SECONDS

    cap = len(WORKFLOW_RETRY_BACKOFF_SECONDS)
    config = ProcessorConfig(api_url="http://test", token="x",
                             agent_id="cb-1", task_poll_enabled=True,
                             task_max_cycles=2,
                             mq=mock.MagicMock(enabled=False))
    coord = _make_minimal_coord(config)
    coord._msg_retries = {}
    coord._session_factory = None
    from agentboard.processors.contract import ExecutionResult, ExecutionStatus
    result = mock.MagicMock(spec=ExecutionResult)
    result.status = ExecutionStatus.FAILED_TRANSIENT
    result.summary = "boom"
    coord.dispatch = mock.MagicMock(return_value=result)

    assert _run_assignment_round(coord, 101) == cap   # 烧掉第 1 轮
    assert _run_assignment_round(coord, 102) == cap   # 烧掉第 2 轮 = 上限
    assert _run_assignment_round(coord, 103) == 0     # Task 级预算已尽


def test_assigned_poll_default_task_cycles_allows_three_rounds():
    """默认 3 轮：前 3 份分配各自拿到完整预算，第 4 份被拒。"""
    coord = _make_poll_coord(dispatch_status="failed_transient")
    for assignment_id in (201, 202, 203):
        assert _run_assignment_round(coord, assignment_id) > 0, assignment_id
    assert _run_assignment_round(coord, 204) == 0


def test_lease_shorter_than_agent_timeout_warns_at_config_time(caplog):
    """P1-1 的最小正确版本：租约必须盖得住一次执行，否则回收会放掉活着的持有者。"""
    import logging

    from agentboard.processors.config import ProcessorConfig

    with caplog.at_level(logging.WARNING, logger="agentboard.processors"):
        ProcessorConfig(lease_seconds=600, agent_timeout=900)
    assert any("AGENTBOARD_WORKER_LEASE" in rec.getMessage()
               for rec in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="agentboard.processors"):
        ProcessorConfig()  # 默认 1800 > 900
    assert not [r for r in caplog.records if "AGENTBOARD_WORKER_LEASE" in r.getMessage()]
