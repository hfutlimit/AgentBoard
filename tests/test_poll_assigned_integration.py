"""Integration tests for the #1716 MQ-less worker dispatch fallback.

Regression coverage for the 247efcb-review P0/P1 findings:

P0-1: ``_resolve_self_agent_registry_id`` must call
``POST /api/agents/{id}/heartbeat`` (not a non-existent
``GET /api/agents/{id}``) and return the integer PK from the
``id`` field of the public DTO.

P0-2: ``GET /api/tasks?agent_id=N`` must propagate ``agent_id`` into
``service.search_tasks`` for BOTH call paths. A worker pulling
its own assigned tasks must never silently fall back to "all
agents' tasks" because the router dropped the filter.

P1: ``_poll_assigned_in_progress`` only runs when MQ is actually
disabled; otherwise MQ drives dispatch and the polling path would
double-process. (Encoded as a gate on ``mq_enabled`` — see the
``_poll_assigned_in_progress`` body.)

These tests use the real FastAPI app via ``TestClient`` (so the
real router is exercised) and an in-memory SQLite database.
"""
from __future__ import annotations

import os

# DB URL must be set BEFORE the agentboard package is imported.
os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_poll_assigned_tmp.db"

import sys
import uuid
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from agentboard.core.common.enums import Status
from agentboard.core.infrastructure.database import (
    SessionLocal, engine, init_db,
)
from agentboard.features.identity.models import User
from agentboard.features.projects.models import Agent as AgentRow, Project
from agentboard.features.scheduling.models import TaskAssignment
from agentboard.features.work_items.models import Task


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    db_path = os.path.abspath("_test_poll_assigned_tmp.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db()
    yield
    engine.dispose(close=True)


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
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
    """Two agents both owned by admin user 4 (so admin token can
    manage both). Different logical names so we can verify the
    router actually filters by agent_id rather than by user_id."""
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


def _make_in_progress_task(session, project, agent, *, title):
    t = Task(
        project_id=project.id, title=title, type="design",
        status=Status.IN_PROGRESS.value, assignment_mode="claim",
        assignee_id=4,
    )
    session.add(t)
    session.flush()
    a = TaskAssignment(
        task_id=t.id, agent_registry_id=agent.id, user_id=4,
        source="claim", status="active", active_slot="active",
    )
    session.add(a)
    session.flush()
    t.current_assignment_id = a.id
    session.commit()
    session.refresh(t)
    return t


def _admin_token() -> str:
    from agentboard.main import app
    client = TestClient(app)
    r = client.post("/api/auth/login",
                    json={"username": "admin", "password": "admin123"})
    if r.status_code == 200:
        return r.json()["token"]
    # Fallback for test DBs that may not have admin yet — use a known
    # token from mcp.json if available; otherwise fail.
    pytest.skip("admin login failed: " + r.text)


# ---------------------------------------------------------------------------
# P0-2: GET /api/tasks?agent_id=N actually filters server-side
# ---------------------------------------------------------------------------


def _override_admin_scope(app, admin_user):
    """Bypass the auth/login chain by injecting admin scope via
    FastAPI's ``app.dependency_overrides``. This lets us hit the real
    router → service contract without seeding a real admin user /
    password in the test DB.
    """
    from agentboard.core.infrastructure.database import get_session as real_get_session
    from agentboard.features.work_items import router as wi_router

    def fake_get_session():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    def fake_caller_uid_admin(authorization, s):
        return admin_user.id, True

    def fake_auth_is_required():
        return False

    def fake_readable_project_ids(s, uid, is_admin=False):
        return None  # skip the readable-projects filter branch

    app.dependency_overrides[real_get_session] = fake_get_session
    app.dependency_overrides[wi_router.api_helpers._caller_uid_admin] = (
        fake_caller_uid_admin
    )
    app.dependency_overrides[wi_router.api_helpers._auth_is_required] = (
        fake_auth_is_required
    )
    app.dependency_overrides[wi_router.service.readable_project_ids] = (
        fake_readable_project_ids
    )


def test_router_passes_agent_id_to_service_filter(
    session, project, two_agents, admin_user,
):
    """When the worker sends ``?agent_id=N``, the router must propagate
    that into ``service.search_tasks`` so the SQL JOINs on
    ``TaskAssignment.agent_registry_id``. If the router drops the
    filter, all in-progress tasks come back and any agent can grab
    any task — the P0-2 ownership hole the 247efcb-review flagged.

    We hit the real FastAPI router via TestClient, override the auth
    chain so we don't need a seeded admin user, and capture the
    kwargs the router passes to ``service.search_tasks``.
    """
    from agentboard.main import app
    from agentboard.features.work_items import router as wi_router

    agent_a, agent_b = two_agents
    _make_in_progress_task(session, project, agent_a, title="a")
    _make_in_progress_task(session, project, agent_b, title="b")

    captured: dict = {}

    def fake_search_tasks(s, **kwargs):
        captured.update(kwargs)
        return []

    _override_admin_scope(app, admin_user)
    try:
        with mock.patch.object(wi_router.service, "search_tasks",
                               side_effect=fake_search_tasks):
            with TestClient(app) as c:
                r = c.get(
                    "/api/tasks",
                    params={"project_id": project.id,
                            "status": "in_progress",
                            "agent_id": agent_a.id},
                )
                # Must succeed (200) and the search_tasks kwarg must
                # carry agent_id. If the router drops it, agent_a's task
                # is the only one that comes back and the assertion
                # below catches that.
                assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()

    # CRITICAL P0-2 assertion: agent_id must reach service.search_tasks
    # as a kwarg. If the router ever drops it again, this fails.
    assert captured.get("agent_id") == agent_a.id, captured
    assert captured.get("project_id") == project.id
    assert captured.get("status") == "in_progress"


def test_search_tasks_router_rejects_str_agent_id(admin_user):
    """Type-level guard: ``agent_id: int | None = Query(None, ge=1)``
    must reject a non-int up front (regression for the P0-2
    silent-string-drop). Without the int type the router would forward
    a string and ``service.search_tasks(agent_id="codebuddy-1")``
    would silently ignore the filter and leak every agent's tasks.
    """
    from agentboard.main import app
    from agentboard.features.work_items import router as wi_router

    captured: dict = {}

    def fake_search_tasks(s, **kwargs):
        captured.update(kwargs)
        return []

    _override_admin_scope(app, admin_user)
    try:
        with mock.patch.object(wi_router.service, "search_tasks",
                               side_effect=fake_search_tasks):
            with TestClient(app) as c:
                r = c.get("/api/tasks", params={"agent_id": "codebuddy-1"})
                # FastAPI must reject the str before service.search_tasks
                # is called.
                assert r.status_code == 422, r.text
                assert captured == {}, captured
    finally:
        app.dependency_overrides.clear()


# TestClient helper that opens a fresh client per call (avoids
# app-lifespan issues in the test session).
def client_get(path, *, params, headers):
    from agentboard.main import app
    with TestClient(app) as c:
        return c.get(path, params=params, headers=headers)


# ---------------------------------------------------------------------------
# P0-1: coordinator resolve uses heartbeat, not a non-existent GET
# ---------------------------------------------------------------------------


def test_resolve_uses_heartbeat_and_returns_pk(session, two_agents, monkeypatch):
    """``_resolve_self_agent_registry_id`` must:
    1. Hit ``POST /api/agents/{logical}/heartbeat`` (returns to_public_dict)
    2. Pull the integer ``id`` out of that response
    3. NOT hit ``GET /api/agents/{logical}`` (which does not exist on the
       server — adding it would re-trigger a scheduling-router circular
       import)."""
    from agentboard.processors.coordinator import ProcessorCoordinator
    from agentboard.processors.config import ProcessorConfig

    agent_a, _ = two_agents
    config = ProcessorConfig(
        api_url="http://test", token="x", agent_id=agent_a.agent_id,
    )
    coord = ProcessorCoordinator.__new__(ProcessorCoordinator)
    coord.config = config
    coord.client = mock.MagicMock()

    resp = mock.MagicMock(status_code=200)
    resp.raise_for_status = mock.MagicMock()
    resp.json.return_value = {"id": agent_a.id, "agent_id": agent_a.agent_id}
    coord.client.post.return_value = resp

    pk = coord._resolve_self_agent_registry_id()
    assert pk == agent_a.id, pk

    # Must hit heartbeat POST, NOT GET on a non-existent endpoint
    called_post = coord.client.post.call_args
    assert called_post is not None
    url = called_post[0][0]
    assert url == f"/api/agents/{agent_a.agent_id}/heartbeat"
    assert coord.client.get.call_count == 0, \
        f"must not call GET /api/agents/{{id}} (no such endpoint); calls={coord.client.get.call_args_list}"


def test_resolve_returns_none_when_agent_id_blank():
    from agentboard.processors.coordinator import ProcessorCoordinator
    from agentboard.processors.config import ProcessorConfig

    config = ProcessorConfig(api_url="http://test", token="x", agent_id="")
    coord = ProcessorCoordinator.__new__(ProcessorCoordinator)
    coord.config = config
    coord.client = mock.MagicMock()
    assert coord._resolve_self_agent_registry_id() is None
    coord.client.post.assert_not_called()


# ---------------------------------------------------------------------------
# P1: poll_assigned runs only when MQ is disabled
# ---------------------------------------------------------------------------


def _make_minimal_coord(config):
    """Create a ProcessorCoordinator that has just enough attributes
    to run ``poll_once`` (the real constructor pulls in handlers,
    heartbeats, etc — far too much for these isolated unit tests)."""
    from agentboard.processors.coordinator import ProcessorCoordinator

    coord = ProcessorCoordinator.__new__(ProcessorCoordinator)
    coord.config = config
    coord.client = mock.MagicMock()
    coord.client.get.return_value.status_code = 200
    coord.client.get.return_value.raise_for_status = mock.MagicMock()
    # dispatch returns a result with status=SUCCESS so the
    # ``if result.status is ExecutionStatus.SUCCESS`` branch fires.
    from agentboard.processors.contract import ExecutionStatus
    _success = mock.MagicMock()
    _success.status = ExecutionStatus.SUCCESS
    coord.dispatch = mock.MagicMock(return_value=_success)
    # poll_once accesses self.registry, self.invoker, self._coordinator,
    # self._work_executor; mock them all.
    coord.registry = mock.MagicMock()
    coord.invoker = mock.MagicMock()
    coord._coordinator = None
    coord._work_executor = None
    return coord


def test_poll_assigned_skipped_when_mq_enabled(monkeypatch):
    """P1: when AGENTBOARD_MQ_URL is set, the worker is driven by the
    MQ consumer; ``_poll_assigned_in_progress`` must not also run or
    we'll double-dispatch."""
    from agentboard.processors.config import ProcessorConfig

    config = ProcessorConfig(api_url="http://test", token="x",
                             agent_id="cb-1")
    coord = _make_minimal_coord(config)
    coord.client.get.return_value.json.return_value = []

    with mock.patch.object(coord, "_resolve_self_agent_registry_id",
                           return_value=42), \
         mock.patch.object(coord, "_mapped_project_ids",
                           return_value=[3]), \
         mock.patch.dict(os.environ, {
             "AGENTBOARD_WORKER_TASK_POLL": "1",
             "AGENTBOARD_MQ_URL": "amqp://example/rabbit",
         }, clear=False):
        stats = coord.poll_once()

    # The MQ path would consume events; the poll-assigned path must
    # not have been entered, so 'assigned_in_progress' must be absent
    # (or zero) in stats.
    assert stats.get("assigned_in_progress", 0) == 0
    coord.dispatch.assert_not_called()


def test_poll_assigned_runs_when_mq_disabled(monkeypatch):
    from agentboard.processors.config import ProcessorConfig

    config = ProcessorConfig(api_url="http://test", token="x",
                             agent_id="cb-1")
    coord = _make_minimal_coord(config)
    # Two in_progress tasks for our agent.
    coord.client.get.return_value.json.return_value = [
        {"id": 11, "type": "design"},
        {"id": 22, "type": "design"},
    ]

    # Force MQ-disabled deterministically (don't rely on host env).
    monkeypatch.delenv("AGENTBOARD_MQ_URL", raising=False)
    monkeypatch.setenv("AGENTBOARD_WORKER_TASK_POLL", "1")

    with mock.patch.object(coord, "_resolve_self_agent_registry_id",
                           return_value=42), \
         mock.patch.object(coord, "_mapped_project_ids",
                           return_value=[3]):
        stats = coord.poll_once()

    # With MQ disabled, the poll-assigned path runs; it scanned one
    # project and found two in_progress tasks for our agent.
    # dispatch is called once per task (2 here).
    assert coord.dispatch.call_count == 2
    assert stats.get("assigned_in_progress") == 2
