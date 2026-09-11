"""WorkflowRun service helpers (Active Workflows Overview slice 1, 2026-09-11).

Pins the contract that the application / slice-2 ``emit_workflow_event`` code
will rely on:

- ``create_workflow_run`` rejects non-``story`` workflow_type in v1.
- ``transition_workflow_run_status`` raises on illegal moves (including the
  pinned ``failed -> running`` invariant).
- ``transition_workflow_run_phase`` raises on illegal graph moves.
- ``touch_workflow_run`` bumps ``version`` without changing status / phase.
"""
import os

os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_workflow_runs_service_tmp.db"

import pytest

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy.orm import sessionmaker

from agentboard.core.infrastructure import database as _database
from agentboard.core.infrastructure.database import engine
from agentboard.core.common.models import Base
from agentboard.features.identity import models as _id_models  # noqa: F401
from agentboard.features.projects import models as _proj_models  # noqa: F401
from agentboard.features.scheduling import models as _sched_models  # noqa: F401
from agentboard.features.work_items import models as _wi_models  # noqa: F401
from agentboard.features.workflow_runs import models as _wfr_models  # noqa: F401

from agentboard.features.workflow_runs.service import (
    IllegalPhaseTransition,
    IllegalWorkflowTransition,
    create_workflow_run,
    transition_workflow_run_phase,
    transition_workflow_run_status,
    touch_workflow_run,
)
from agentboard.features.identity.models import User
from agentboard.features.projects.models import Project


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    db_path = os.path.abspath("_test_workflow_runs_service_tmp.db")
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass
    _database.reset_engine()
    Base.metadata.create_all(bind=_database.engine)
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
def project(session):
    suffix = os.urandom(4).hex()
    user = User(
        username=f"u-{suffix}",
        password_hash="x",
        email=f"{suffix}@x.com",
    )
    session.add(user)
    session.flush()
    proj = Project(key=f"P-{suffix}", name="P1")
    session.add(proj)
    session.commit()
    return proj


def test_create_workflow_run_queued(session, project):
    run = create_workflow_run(
        session,
        project_id=project.id,
        workflow_type="story",
        initial_phase="design",
    )
    assert run.id is not None
    assert run.status == "queued"
    assert run.phase == "design"
    assert run.workflow_type == "story"
    assert run.version == 1


def test_create_workflow_run_rejects_non_story_in_v1(session, project):
    with pytest.raises(ValueError, match="v1 only accepts 'story'"):
        create_workflow_run(
            session,
            project_id=project.id,
            workflow_type="schedule",
        )


def test_create_workflow_run_rejects_illegal_initial_phase(session, project):
    with pytest.raises(IllegalPhaseTransition):
        create_workflow_run(
            session,
            project_id=project.id,
            workflow_type="story",
            initial_phase="qa",  # not the initial phase
        )


def test_transition_status_legal(session, project):
    run = create_workflow_run(
        session,
        project_id=project.id,
        workflow_type="story",
        initial_phase="design",
    )
    transition_workflow_run_status(session, run, to_status="running")
    assert run.status == "running"
    assert run.started_at is not None  # auto-set on first running
    assert run.version == 2


def test_transition_status_failed_strictly_terminal(session, project):
    """Pinned: failed cannot transition to running."""
    run = create_workflow_run(
        session,
        project_id=project.id,
        workflow_type="story",
        initial_phase="design",
    )
    transition_workflow_run_status(session, run, to_status="failed")
    assert run.status == "failed"
    # No path back.
    with pytest.raises(IllegalWorkflowTransition):
        transition_workflow_run_status(session, run, to_status="running")


def test_transition_status_finished_at_set_on_terminal(session, project):
    run = create_workflow_run(
        session,
        project_id=project.id,
        workflow_type="story",
        initial_phase="design",
    )
    transition_workflow_run_status(session, run, to_status="running")
    transition_workflow_run_status(
        session, run, to_status="completed", set_finished_at=True,
    )
    assert run.finished_at is not None


def test_transition_phase_legal(session, project):
    run = create_workflow_run(
        session,
        project_id=project.id,
        workflow_type="story",
        initial_phase="design",
    )
    transition_workflow_run_phase(session, run, to_phase="development")
    assert run.phase == "development"
    assert run.status == "queued"  # status is independent
    transition_workflow_run_phase(session, run, to_phase="qa")
    assert run.phase == "qa"
    transition_workflow_run_phase(session, run, to_phase="development")  # rework
    assert run.phase == "development"


def test_transition_phase_illegal_skips(session, project):
    run = create_workflow_run(
        session,
        project_id=project.id,
        workflow_type="story",
        initial_phase="design",
    )
    with pytest.raises(IllegalPhaseTransition):
        transition_workflow_run_phase(session, run, to_phase="qa")  # skip dev


def test_touch_bumps_version_only(session, project):
    run = create_workflow_run(
        session,
        project_id=project.id,
        workflow_type="story",
        initial_phase="design",
    )
    initial_status = run.status
    initial_phase = run.phase
    initial_version = run.version
    touch_workflow_run(session, run)
    assert run.status == initial_status
    assert run.phase == initial_phase
    assert run.version == initial_version + 1
    assert run.last_activity_at is not None


def test_reopened_from_run_id_associates_history(session, project):
    """Story reopen: new run points to the old terminal run; old run is
    untouched. The database does not cascade-delete the new history when
    the old run is hard-deleted (ON DELETE SET NULL)."""
    old = create_workflow_run(
        session,
        project_id=project.id,
        workflow_type="story",
        initial_phase="design",
    )
    transition_workflow_run_status(session, old, to_status="running")
    transition_workflow_run_status(
        session, old, to_status="failed", set_finished_at=True,
    )
    assert old.status == "failed"

    new_run = create_workflow_run(
        session,
        project_id=project.id,
        workflow_type="story",
        reopened_from_run_id=old.id,
        initial_phase="design",
    )
    assert new_run.reopened_from_run_id == old.id
    assert old.status == "failed"  # untouched
    assert old.finished_at is not None  # untouched
