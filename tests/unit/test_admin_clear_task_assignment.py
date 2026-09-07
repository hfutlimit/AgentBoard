"""Unit tests for ``service.admin_clear_task_assignment``.

Regression for the bug fixed 2026-09-07 (#1716): every claim / apply /
arbitrate / transfer path requires ``current_assignment_id IS NULL``,
but no HTTP path could clear it on a task stuck in ``todo``. The admin
endpoint is the explicit escape hatch — these tests pin the service
behavior so the contract is stable.
"""
import os

os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_admin_clear_tmp.db"

import sys
import uuid
import pytest

from agentboard.core.common.enums import Status
from agentboard.core.exceptions import NotFound
from pydantic import ValidationError
from agentboard.core.infrastructure import database as _database
from agentboard.core.infrastructure.database import init_db, engine
from agentboard.features.identity.models import User
from agentboard.features.projects.models import Project
from agentboard.features.scheduling.models import TaskAssignment
from agentboard.features.work_items import service as work_items_service
from agentboard.features.work_items.models import Task
from agentboard.features.work_items.schemas import AdminClearAssignmentIn


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    db_path = os.path.abspath("_test_admin_clear_tmp.db")
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
    p = Project(name=f"ac-test-{suffix}", key=f"AC{suffix}", description="")
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@pytest.fixture
def task_with_assignment(session, project, admin_user):
    """Task in todo with an active TaskAssignment pinned via FK."""
    t = Task(
        project_id=project.id,
        title="stuck design",
        type="design",
        status=Status.TODO.value,
        assignment_mode="claim",
        assignee_id=admin_user.id,
    )
    session.add(t)
    session.flush()
    a = TaskAssignment(
        task_id=t.id,
        user_id=admin_user.id,
        source="claim",
        status="active",
        active_slot="active",
    )
    session.add(a)
    session.flush()
    t.current_assignment_id = a.id
    session.commit()
    session.refresh(t)
    session.refresh(a)
    return t, a


def test_clears_fk_and_marks_assignment_superseded(
    session, task_with_assignment, admin_user,
):
    t, a = task_with_assignment
    assert t.current_assignment_id == a.id
    assert a.status == "active"

    result = work_items_service.admin_clear_task_assignment(
        session, t.id, admin_user_id=admin_user.id, reason="operator unblock",
    )

    assert result.current_assignment_id is None
    assert result.assignment_deferred_reason is None
    assert result.assignment_deferred_at is None
    # status must NOT change (caller decides)
    assert result.status == Status.TODO.value

    # TaskAssignment row: status='superseded', active_slot cleared
    session.refresh(a)
    assert a.status == "superseded"
    assert a.active_slot is None
    assert a.completed_at is not None  # type: ignore[attr-defined]


def test_noop_when_fk_already_null(session, task_with_assignment, admin_user):
    t, _ = task_with_assignment
    # First clear: real work
    work_items_service.admin_clear_task_assignment(
        session, t.id, admin_user_id=admin_user.id, reason="first",
    )
    # Second call: no-op, must not raise
    result = work_items_service.admin_clear_task_assignment(
        session, t.id, admin_user_id=admin_user.id, reason="second",
    )
    assert result.current_assignment_id is None
    assert result.status == Status.TODO.value


def test_missing_task_raises_not_found(session, admin_user):
    with pytest.raises(NotFound):
        work_items_service.admin_clear_task_assignment(
            session, 999_999, admin_user_id=admin_user.id,
        )


def test_assignment_already_completed_does_not_overwrite(
    session, task_with_assignment, admin_user,
):
    t, a = task_with_assignment
    # Pretend a prior path already closed the assignment as 'completed'
    a.status = "completed"
    a.active_slot = None
    a.completed_at = None  # type: ignore[attr-defined]
    session.commit()

    work_items_service.admin_clear_task_assignment(
        session, t.id, admin_user_id=admin_user.id,
    )

    session.refresh(a)
    # We must NOT downgrade a 'completed' assignment to 'superseded'
    assert a.status == "completed"
    # But the FK is cleared either way
    session.refresh(t)
    assert t.current_assignment_id is None


def test_writes_status_history_with_admin_reason(
    session, task_with_assignment, admin_user,
):
    t, _ = task_with_assignment
    work_items_service.admin_clear_task_assignment(
        session, t.id, admin_user_id=admin_user.id, reason="manual unstick",
    )
    history = work_items_service.list_task_status_history(session, t.id)
    # Most recent first
    latest = history[0]
    assert latest.changed_by == admin_user.id
    assert "manual unstick" in (latest.reason or "")


def test_schema_max_length_on_reason():
    """Reason must be bounded to keep history rows compact."""
    # At the boundary: 500 chars accepted
    body_ok = AdminClearAssignmentIn(reason="x" * 500)
    assert len(body_ok.reason) == 500
    # Over the limit: Pydantic raises ValidationError at construction time
    with pytest.raises(ValidationError):
        AdminClearAssignmentIn(reason="x" * 501)
