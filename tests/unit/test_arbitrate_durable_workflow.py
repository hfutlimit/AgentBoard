"""Regression for the bug found 2026-09-07: ``service.arbitrate_task``
internally called ``try_assign_task``, which carries a
``require_legacy_task`` gate that rejects tasks on projects listed in
``AGENTBOARD_DURABLE_PROJECT_IDS``. That made arbitration
self-contradictory: durable workflow is the *only* path for those
projects, but the assign helper refused to run on them.

Fix: ``try_assign_task`` now takes ``bypass_legacy_check=True``;
``arbitrate_task`` passes it through. These tests pin both the new
behavior and the unchanged legacy gate.
"""
import os

os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_arbitrate_durable_tmp.db"

import uuid
import pytest

from agentboard.core.common.enums import Status
from agentboard.core.infrastructure import database as _database
from agentboard.core.infrastructure.database import init_db, engine
from agentboard.features.identity.models import User
from agentboard.features.projects.models import Project
from agentboard.features.scheduling.durable_routing import durable_project_enabled
from agentboard.features.scheduling.models import TaskApplication
from agentboard.features.work_items import service as work_items_service
from agentboard.features.work_items.models import Task


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    db_path = os.path.abspath("_test_arbitrate_durable_tmp.db")
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass
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
    p = Project(name=f"arb-test-{suffix}", key=f"ARB{suffix}", description="")
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@pytest.fixture
def arbitrated_task(session, project, admin_user):
    t = Task(
        project_id=project.id,
        title="design stub",
        type="design",
        status=Status.TODO.value,
        assignment_mode="arbitrated",
        owner_user_id=admin_user.id,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


@pytest.fixture
def admin_codebuddy_agent(session, admin_user):
    """A real Agent row owned by admin user (id 4 in prod)."""
    from agentboard.features.projects.models import Agent as AgentRow
    a = AgentRow(
        agent_id=f"admin-codebuddy-{uuid.uuid4().hex[:6]}",
        name="test agent",
        user_id=admin_user.id,
        cli_command="codebuddy",
        model="hy4-preview",
        capabilities='[{"name": "design", "level": 1, "confidence": 0.5}]',
        roles="[\"worker\"]",
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def test_arbitrate_works_when_durable_project_enabled(
    session, admin_user, project, arbitrated_task, admin_codebuddy_agent, monkeypatch,
):
    """Force the project into the durable list and assert arbitration
    succeeds — this is the exact bug the fix addresses.
    """
    monkeypatch.setenv("AGENTBOARD_DURABLE_PROJECT_IDS", str(project.id))
    # Defensive: confirm the env actually opts the project in
    assert durable_project_enabled(project.id) is True

    # Apply + arbitrate the way a real worker would
    application = work_items_service.apply_for_task(
        session,
        arbitrated_task.id,
        user_id=admin_user.id,
        agent_registry_id=admin_codebuddy_agent.id,
    )
    assert application.status == "pending"

    assigned_task, assignment, winner = work_items_service.arbitrate_task(
        session, arbitrated_task.id,
    )

    session.refresh(assigned_task)
    assert assigned_task.status == Status.IN_PROGRESS.value
    assert assigned_task.current_assignment_id == assignment.id
    # application resolved to 'accepted' for the winner
    assert winner.status == "accepted"


def test_try_assign_task_still_rejects_legacy_on_durable_project(
    session, admin_user, project, admin_codebuddy_agent, monkeypatch,
):
    """The legacy gate on ``try_assign_task`` must remain in force for
    callers that don't pass ``bypass_legacy_check``. ``claim_development_task``
    relies on this to keep the two paths separate.
    """
    from agentboard.core.exceptions import InvalidValue
    monkeypatch.setenv("AGENTBOARD_DURABLE_PROJECT_IDS", str(project.id))
    t = Task(
        project_id=project.id,
        title="legacy reject",
        type="dev",
        status=Status.TODO.value,
        assignment_mode="claim",
    )
    session.add(t)
    session.commit()
    session.refresh(t)

    with pytest.raises(InvalidValue) as exc:
        work_items_service.try_assign_task(
            session, t.id,
            user_id=admin_user.id,
            agent_registry_id=admin_codebuddy_agent.id,
            source="claim",
        )
    assert "durable workflow" in str(exc.value)


def test_try_assign_task_bypass_legacy_skips_gate(
    session, admin_user, project, admin_codebuddy_agent, monkeypatch,
):
    """Explicit bypass must work for callers that already validated
    upstream (e.g. arbitrate_task after picking the winning application).
    """
    monkeypatch.setenv("AGENTBOARD_DURABLE_PROJECT_IDS", str(project.id))
    t = Task(
        project_id=project.id,
        title="bypass ok",
        type="dev",
        status=Status.TODO.value,
        assignment_mode="arbitrated",
        owner_user_id=admin_user.id,
    )
    session.add(t)
    session.commit()
    session.refresh(t)

    assigned_task, assignment = work_items_service.try_assign_task(
        session, t.id,
        user_id=admin_user.id,
        agent_registry_id=admin_codebuddy_agent.id,
        source="arbitration",
        bypass_legacy_check=True,
    )
    assert assigned_task.status == Status.IN_PROGRESS.value
    assert assigned_task.current_assignment_id == assignment.id
