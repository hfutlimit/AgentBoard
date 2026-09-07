"""Unit tests for ``search_tasks`` agent_id filter (#1716 fallback).

When ``AGENTBOARD_MQ_URL`` is empty the server's
``publish_workflow_event_for_agent`` is a no-op, so an in_progress
task that arbitration assigned to a given agent has no MQ consumer
to wake the worker. The worker has to poll for the task itself.

We add an ``agent_id`` filter to ``search_tasks`` that joins the
active ``TaskAssignment`` so the worker can ask: "give me every
in_progress task currently assigned to me". These tests pin both
the new behaviour and that the filter coexists with the other
search filters.
"""
import os

os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_search_agent_v2_tmp.db"

import uuid
import pytest

from agentboard.core.common.enums import Status
from agentboard.core.infrastructure import database as _database
from agentboard.core.infrastructure.database import init_db, engine
from agentboard.features.identity.models import User
from agentboard.features.projects.models import Agent as AgentRow, Project
from agentboard.features.scheduling.models import TaskAssignment
from agentboard.core.application import service as core_service
from agentboard.features.work_items.models import Task


@pytest.fixture(autouse=True)
def _init_db():
    from agentboard.core.infrastructure import database
    database.reset_engine()
    # Force schema creation via SQLAlchemy metadata (alembic no-ops
    # on a freshly-cleared db after a previous test file wiped it).
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
        s.close()


@pytest.fixture
def admin_user(session):
    u = User(
        username=f"admin-{uuid.uuid4().hex[:8]}",
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
    p = Project(name=f"search-agent-{suffix}", key=f"SA{suffix}", description="")
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@pytest.fixture
def codebuddy_agent(session, admin_user):
    a = AgentRow(
        agent_id=f"search-cb-{uuid.uuid4().hex[:6]}",
        name="search-test",
        user_id=admin_user.id,
        cli_command="codebuddy",
        model="hy4-preview",
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


@pytest.fixture
def other_agent(session, admin_user):
    a = AgentRow(
        agent_id=f"other-cb-{uuid.uuid4().hex[:6]}",
        name="other",
        user_id=admin_user.id,
        cli_command="codebuddy",
        model="glm-5.3-flash",
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def _make_in_progress_task(session, project, agent, *, title="x"):
    """Create a task in_progress pinned to a fresh TaskAssignment on agent."""
    t = Task(
        project_id=project.id, title=title, type="design",
        status=Status.IN_PROGRESS.value,
        assignment_mode="claim",
        assignee_id=4,
    )
    session.add(t)
    session.flush()
    a = TaskAssignment(
        task_id=t.id,
        agent_registry_id=agent.id,
        user_id=4,
        source="claim",
        status="active",
        active_slot="active",
    )
    session.add(a)
    session.flush()
    t.current_assignment_id = a.id
    session.commit()
    session.refresh(t)
    return t


def test_search_by_agent_registry_id_returns_only_assigned_tasks(
    session, project, codebuddy_agent, other_agent,
):
    mine = _make_in_progress_task(session, project, codebuddy_agent, title="mine")
    _make_in_progress_task(session, project, other_agent, title="theirs")

    rows = core_service.search_tasks(
        session, project_id=project.id, status=Status.IN_PROGRESS.value,
        agent_registry_id=codebuddy_agent.id,
    )
    assert {r.id for r in rows} == {mine.id}


def test_search_by_assigned_agent_id_returns_only_assigned_tasks(
    session, project, codebuddy_agent, other_agent,
):
    """P3 review: the worker-polling path uses ``assigned_agent_id``
    (logical name, e.g. ``codebuddy-1``) — not the registry PK. This
    test mirrors that filter and pins the same isolation invariant.
    """
    mine = _make_in_progress_task(session, project, codebuddy_agent, title="mine")
    _make_in_progress_task(session, project, other_agent, title="theirs")

    rows = core_service.search_tasks(
        session, project_id=project.id, status=Status.IN_PROGRESS.value,
        assigned_agent_id=codebuddy_agent.agent_id,
    )
    assert {r.id for r in rows} == {mine.id}


def test_search_by_agent_excludes_todo_and_terminal(
    session, project, codebuddy_agent,
):
    """Three tasks: todo, in_progress, done — all assigned to my
    agent. Only the in_progress one comes back."""
    # todo task assigned to my agent (should NOT come back)
    todo = Task(
        project_id=project.id, title="todo", type="design",
        status=Status.TODO.value, assignment_mode="claim",
    )
    session.add(todo)
    session.flush()
    a = TaskAssignment(
        task_id=todo.id, agent_registry_id=codebuddy_agent.id,
        user_id=4, source="claim", status="active", active_slot="active",
    )
    session.add(a)
    session.flush()
    todo.current_assignment_id = a.id

    # done task assigned to my agent (should NOT come back)
    done = Task(
        project_id=project.id, title="done", type="design",
        status=Status.DONE.value, assignment_mode="claim",
    )
    session.add(done)
    session.flush()
    a2 = TaskAssignment(
        task_id=done.id, agent_registry_id=codebuddy_agent.id,
        user_id=4, source="claim", status="completed", active_slot=None,
    )
    session.add(a2)
    session.flush()
    done.current_assignment_id = a2.id
    session.commit()

    in_progress = _make_in_progress_task(session, project, codebuddy_agent, title="active")
    rows = core_service.search_tasks(
        session, project_id=project.id, status=Status.IN_PROGRESS.value,
        agent_registry_id=codebuddy_agent.id,
    )
    assert {r.id for r in rows} == {in_progress.id}


def test_search_without_agent_filter_returns_everything(
    session, project, codebuddy_agent, other_agent,
):
    mine = _make_in_progress_task(session, project, codebuddy_agent, title="a")
    theirs = _make_in_progress_task(session, project, other_agent, title="b")
    rows = core_service.search_tasks(
        session, project_id=project.id, status=Status.IN_PROGRESS.value,
    )
    assert {r.id for r in rows} == {mine.id, theirs.id}


