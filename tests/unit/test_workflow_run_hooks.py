"""WorkflowRun service hooks (Active Workflows Overview slice 2, 2026-09-11).

Pins:
- ``confirm_story`` creates a WorkflowRun (idempotent) and emits
  ``workflow_started`` exactly once.
- ``set_status`` emits ``task_reopened`` when a terminal task is moved back
  to ``in_progress`` (in-run review-rejected reopen).
- Event emission is best-effort: state changes MUST NOT break if the event
  store errors.
"""
import os

os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_workflow_runs_hooks_tmp.db"

import sys
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest

from agentboard.core.infrastructure import database as _database
from agentboard.core.common.models import Base
from agentboard.features.identity import models as _id_models  # noqa: F401
from agentboard.features.projects import models as _proj_models  # noqa: F401
from agentboard.features.scheduling import models as _sched_models  # noqa: F401
from agentboard.features.work_items import models as _wi_models  # noqa: F401
from agentboard.features.workflow_runs import models as _wfr_models  # noqa: F401

from agentboard.core.application.service import confirm_story
from agentboard.features.work_items import service as task_service
from agentboard.features.work_items.models import Task, Status
from agentboard.features.identity.models import User
from agentboard.features.projects.models import Epic, Project, Story


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    db_path = os.path.abspath("_test_workflow_runs_hooks_tmp.db")
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass
    _database.reset_engine()
    Base.metadata.create_all(bind=_database.engine)
    yield
    _database.engine.dispose(close=True)


@pytest.fixture
def session():
    s = _database.SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def fixture_world(session):
    suffix = os.urandom(4).hex()
    user = User(username=f"u-{suffix}", password_hash="x", email=f"{suffix}@x.com")
    session.add(user)
    session.flush()
    proj = Project(key=f"P-{suffix}", name="P1")
    session.add(proj)
    session.flush()
    epic = Epic(project_id=proj.id, title="E1", status="backlog")
    session.add(epic)
    session.flush()
    story = Story(epic_id=epic.id, title="S1", status="backlog")
    session.add(story)
    session.flush()
    task = Task(
        project_id=proj.id, story_id=story.id, title="T1",
        type="dev", status=Status.IN_PROGRESS, priority="medium",
        assignee_id=user.id,
    )
    session.add(task)
    session.commit()
    return proj, story, task, user


# ---------- confirm_story hook ----------

def test_confirm_story_creates_workflow_run_and_workflow_started_event(session, fixture_world):
    p, story, _, user = fixture_world
    # Confirm: backlog → confirmed.
    confirm_story(session, story.id, changed_by=user.id)

    s = session
    from agentboard.features.workflow_runs.models import WorkflowRun, WorkflowRunEvent
    runs = (
        s.query(WorkflowRun)
        .filter(WorkflowRun.story_id == story.id)
        .all()
    )
    assert len(runs) == 1
    assert runs[0].status == "queued"
    assert runs[0].phase == "design"
    assert runs[0].workflow_type == "story"

    events = (
        s.query(WorkflowRunEvent)
        .filter(WorkflowRunEvent.workflow_run_id == runs[0].id)
        .filter(WorkflowRunEvent.event_type == "workflow_started")
        .all()
    )
    assert len(events) == 1
    payload = json.loads(events[0].payload)
    assert payload["workflow_type"] == "story"
    assert payload["initial_phase"] == "design"
    assert events[0].actor_id == user.id


def test_confirm_story_idempotent_no_duplicate_run(session, fixture_world):
    """Calling confirm twice (idempotent path) must NOT create a second run."""
    p, story, _, user = fixture_world
    confirm_story(session, story.id, changed_by=user.id)
    confirm_story(session, story.id, changed_by=user.id)  # already confirmed, no-op

    s = session
    from agentboard.features.workflow_runs.models import WorkflowRun, WorkflowRunEvent
    runs = (
        s.query(WorkflowRun)
        .filter(WorkflowRun.story_id == story.id)
        .all()
    )
    assert len(runs) == 1

    events = (
        s.query(WorkflowRunEvent)
        .filter(WorkflowRunEvent.workflow_run_id == runs[0].id)
        .filter(WorkflowRunEvent.event_type == "workflow_started")
        .all()
    )
    # Only the first confirm emits the event; the second is a no-op.
    assert len(events) == 1


# ---------- set_status reopen hook ----------

def test_set_status_in_review_to_in_progress_emits_task_reopened(session, fixture_world):
    p, story, task, user = fixture_world
    # Move task to in_review first (so we can reopen it).
    task_service.set_status(session, task.id, Status.IN_REVIEW, changed_by=user.id)

    # Now reopen: in_review → in_progress should emit task_reopened.
    s = session
    from agentboard.features.workflow_runs.models import WorkflowRunEvent
    task_service.set_status(session, task.id, Status.IN_PROGRESS, changed_by=user.id, reason="review rejected")

    events = (
        s.query(WorkflowRunEvent)
        .filter(WorkflowRunEvent.task_id == task.id)
        .filter(WorkflowRunEvent.event_type == "task_reopened")
        .all()
    )
    assert len(events) == 1
    payload = json.loads(events[0].payload)
    assert payload["task_id"] == task.id
    assert payload["from_status"] == Status.IN_REVIEW
    assert payload["reason"] == "review rejected"


def test_set_status_done_to_in_progress_emits_task_reopened(session, fixture_world):
    """Pinned: 'done → in_progress' (e.g. user manually reopens) also emits
    task_reopened. This is the in-run reopen path; the cross-run reopen
    (Story reopen) emits workflow_reopened instead."""
    p, story, task, user = fixture_world
    task_service.set_status(session, task.id, Status.IN_REVIEW, changed_by=user.id)
    task_service.set_status(session, task.id, Status.DONE, changed_by=user.id, status_reason="completed")

    s = session
    from agentboard.features.workflow_runs.models import WorkflowRunEvent
    task_service.set_status(session, task.id, Status.IN_PROGRESS, changed_by=user.id, reason="user reopen")

    events = (
        s.query(WorkflowRunEvent)
        .filter(WorkflowRunEvent.task_id == task.id)
        .filter(WorkflowRunEvent.event_type == "task_reopened")
        .all()
    )
    assert len(events) == 1
    payload = json.loads(events[0].payload)
    assert payload["from_status"] == Status.DONE


def test_set_status_normal_transition_does_not_emit_task_reopened(session, fixture_world):
    """in_progress → in_review is a normal forward move, not a reopen."""
    p, story, task, user = fixture_world
    s = session
    from agentboard.features.workflow_runs.models import WorkflowRunEvent
    task_service.set_status(session, task.id, Status.IN_REVIEW, changed_by=user.id)

    events = (
        s.query(WorkflowRunEvent)
        .filter(WorkflowRunEvent.task_id == task.id)
        .filter(WorkflowRunEvent.event_type == "task_reopened")
        .all()
    )
    assert len(events) == 0
