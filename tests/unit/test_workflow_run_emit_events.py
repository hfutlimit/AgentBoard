"""WorkflowRun event emission helpers (Active Workflows Overview slice 2, 2026-09-11).

Pins:
- ``emit_workflow_event`` writes the event row AND atomically bumps
  ``last_activity_at`` + ``version`` on the parent run.
- Validation is enforced BEFORE the write (contract drift fails fast).
- ``ensure_story_workflow_run`` returns the active run (or creates a new one
  in queued/design phase).
- ``reopen_story_workflow`` creates a new run + emits ``workflow_reopened``
  with ``reopened_from_run_id``; the old run is unchanged.
- ``record_retry_scheduled`` enforces retry_kind and accepts all three kinds.
"""
import os

os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_workflow_runs_emit_tmp.db"

import sys
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

from agentboard.features.workflow_runs.event_contract import InvalidWorkflowEvent
from agentboard.features.workflow_runs.models import (
    WorkflowRun,
    WorkflowRunEvent,
)
from agentboard.features.workflow_runs.service import (
    emit_workflow_event,
    ensure_story_workflow_run,
    record_retry_scheduled,
    reopen_story_workflow,
    transition_workflow_run_phase,
)
from agentboard.features.identity.models import User
from agentboard.features.projects.models import Epic, Project, Story


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    db_path = os.path.abspath("_test_workflow_runs_emit_tmp.db")
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
def project(session):
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
    session.commit()
    return proj, story, user


def test_emit_writes_event_and_bumps_run(session, project):
    p, st, user = project
    run, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    initial_version = run.version
    initial_activity = run.last_activity_at

    event = emit_workflow_event(
        session,
        workflow_run_id=run.id,
        event_type="task_created",
        actor_type="system",
        task_id=None,
        summary="Design task created",
        payload={"task_id": 1, "task_type": "design", "title": "Design"},
    )

    assert event.id is not None
    assert event.event_type == "task_created"
    assert event.payload  # JSON string

    # Atomic parent-run touch.
    s = session
    s.refresh(run)
    assert run.version == initial_version + 1
    assert run.last_activity_at is not None
    if initial_activity is not None:
        assert run.last_activity_at >= initial_activity


def test_emit_rejects_unknown_event_type(session, project):
    p, st, _ = project
    run, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    with pytest.raises(InvalidWorkflowEvent):
        emit_workflow_event(
            session,
            workflow_run_id=run.id,
            event_type="not_a_real_event",
            actor_type="system",
        )


def test_emit_rejects_missing_required_payload(session, project):
    p, st, _ = project
    run, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    with pytest.raises(InvalidWorkflowEvent, match="missing required payload keys"):
        emit_workflow_event(
            session,
            workflow_run_id=run.id,
            event_type="task_created",
            actor_type="system",
            payload={"task_id": 1},  # missing task_type and title
        )


def test_ensure_returns_existing_active_run(session, project):
    p, st, _ = project
    r1, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    r2, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    assert r1.id == r2.id  # idempotent


def test_ensure_creates_new_after_previous_terminal(session, project):
    """If previous run is terminal, ensure creates a new active run.

    2026-09-15 follow-up: ``ensure_story_workflow_run`` now auto-advances
    the freshly-created run from ``queued`` to ``running`` so the row
    never stays stuck on the brand-new side of the lifecycle. So the
    pre-condition for the "create new after terminal" check is just
    "previous is terminal", no need to manually transition queued→running.
    """
    p, st, _ = project
    r1, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    assert r1.status == "running"
    from agentboard.features.workflow_runs.service import transition_workflow_run_status
    transition_workflow_run_status(session, r1, to_status="completed", set_finished_at=True)
    r2, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    assert r2.id != r1.id
    assert r1.status == "completed"  # untouched
    assert r2.status == "running"  # auto-advanced


def test_reopen_cancels_previous_active_run(session, project):
    """2026-09-15 follow-up: ``reopen_story_workflow`` must cancel any
    non-terminal predecessor before creating the new run. Previously the
    docstring promised this behaviour but the implementation just
    created the new run, leaving ``ensure_story_workflow_run`` to see
    two active rows for the same Story.
    """
    p, st, _ = project
    r1, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    assert r1.status == "running"
    # Confirm the pre-condition: r1 is the active run.
    active_before, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    assert active_before.id == r1.id

    r2 = reopen_story_workflow(
        session, story_id=st.id, project_id=p.id, reason="story_reopened",
    )
    session.refresh(r1)
    session.refresh(r2)

    # r1 must now be cancelled (terminal) — the docstring's promise, made real.
    assert r1.status == "cancelled"
    assert r1.finished_at is not None
    # r2 is the new active run.
    assert r2.id != r1.id
    assert r2.reopened_from_run_id == r1.id
    assert r2.status == "running"
    # And ensure picks r2, not r1 (find-or-create invariant).
    after, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    assert after.id == r2.id

    # Exactly one event on r2 (workflow_reopened); r1's cancel transition
    # bumped its own version but did NOT emit a cross-run event.
    s = session
    events = (
        s.query(WorkflowRunEvent)
        .filter(WorkflowRunEvent.workflow_run_id == r2.id)
        .all()
    )
    assert len(events) == 1
    assert events[0].event_type == "workflow_reopened"


def test_reopen_does_not_mutate_already_terminal_predecessor(session, project):
    """If the previous run is already terminal (``completed`` /
    ``failed`` / ``cancelled``), reopen must not try to transition it
    again — that's an ``IllegalWorkflowTransition`` from a terminal
    state and would break the reopen flow."""
    p, st, _ = project
    r1, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    from agentboard.features.workflow_runs.service import transition_workflow_run_status
    transition_workflow_run_status(session, r1, to_status="failed", set_finished_at=True)
    session.refresh(r1)
    assert r1.status == "failed"

    # Should NOT raise — ``failed`` is terminal so the cancel guard skips.
    r2 = reopen_story_workflow(
        session, story_id=st.id, project_id=p.id, reason="after_failure",
    )
    session.refresh(r1)
    session.refresh(r2)
    assert r1.status == "failed", "predecessor must remain failed, untouched"
    assert r2.id != r1.id
    assert r2.reopened_from_run_id == r1.id
    assert r2.status == "running"


def test_reopen_creates_new_run_with_link(session, project):
    p, st, _ = project
    r1, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    from agentboard.features.workflow_runs.service import transition_workflow_run_status
    transition_workflow_run_status(session, r1, to_status="completed", set_finished_at=True)
    r2 = reopen_story_workflow(
        session, story_id=st.id, project_id=p.id, reason="story_reopened",
    )
    assert r2.id != r1.id
    assert r2.reopened_from_run_id == r1.id
    assert r2.phase == "design"
    assert r2.status == "running"  # 2026-09-15: auto-advanced
    assert r1.status == "completed"  # old run is untouched

    # workflow_reopened event was emitted on the new run.
    s = session
    events = (
        s.query(WorkflowRunEvent)
        .filter(WorkflowRunEvent.workflow_run_id == r2.id)
        .filter(WorkflowRunEvent.event_type == "workflow_reopened")
        .all()
    )
    assert len(events) == 1
    import json as _json
    payload = _json.loads(events[0].payload)
    assert payload["reopened_from_run_id"] == r1.id
    assert payload["reason"] == "story_reopened"


def test_transition_phase_emits_phase_changed_event(session, project):
    p, st, _ = project
    run, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    transition_workflow_run_phase(session, run, to_phase="development", reason="auto")

    s = session
    events = (
        s.query(WorkflowRunEvent)
        .filter(WorkflowRunEvent.workflow_run_id == run.id)
        .filter(WorkflowRunEvent.event_type == "phase_changed")
        .all()
    )
    assert len(events) == 1
    import json as _json
    payload = _json.loads(events[0].payload)
    assert payload["from_phase"] == "design"
    assert payload["to_phase"] == "development"
    assert payload["reason"] == "auto"


def test_transition_phase_bumps_version_exactly_once(session, project):
    """2026-09-15 follow-up: a single ``transition_workflow_run_phase`` call
    must bump ``run.version`` exactly once (inside the emitted
    ``phase_changed`` event). Previously the helper also bumped version
    directly, so every phase change increased version by 2 — which made
    the SignalR ``workflow.changed`` broadcast skip every other event.
    """
    p, st, _ = project
    run, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    # ``ensure_story_workflow_run`` triggers an internal queued→running
    # transition that itself emits no event but DOES set ``started_at``
    # and bumps ``version`` once via ``transition_workflow_run_status``.
    # Capture the post-ensure baseline explicitly.
    session.refresh(run)
    baseline = run.version
    transition_workflow_run_phase(session, run, to_phase="development", reason="auto")
    session.refresh(run)
    assert run.version == baseline + 1, (
        f"phase transition bumped version by {run.version - baseline}, "
        "expected exactly 1 (the touch inside emit_workflow_event)"
    )
    # And a second phase change must be +1 again, not +2.
    transition_workflow_run_phase(session, run, to_phase="qa", reason="auto")
    session.refresh(run)
    assert run.version == baseline + 2


def test_record_retry_scheduled_all_three_kinds(session, project):
    p, st, _ = project
    run, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    for kind in ("execution", "review_cycle", "mq_delivery"):
        record_retry_scheduled(
            session,
            workflow_run_id=run.id,
            retry_kind=kind,
            attempt=1,
            reason=f"test {kind}",
        )
    s = session
    kinds = {
        row.payload
        for row in s.query(WorkflowRunEvent)
        .filter(WorkflowRunEvent.workflow_run_id == run.id)
        .filter(WorkflowRunEvent.event_type == "retry_scheduled")
        .all()
    }
    import json as _json
    decoded = [_json.loads(k)["retry_kind"] for k in kinds]
    assert sorted(decoded) == ["execution", "mq_delivery", "review_cycle"]


def test_record_retry_scheduled_rejects_invalid_kind(session, project):
    p, st, _ = project
    run, _ = ensure_story_workflow_run(session, story_id=st.id, project_id=p.id)
    with pytest.raises(InvalidWorkflowEvent, match="retry_kind must be one of"):
        # retry_kind must be in {execution, review_cycle, mq_delivery}
        record_retry_scheduled(
            session,
            workflow_run_id=run.id,
            retry_kind="bogus_kind",
            attempt=1,
            reason="x",
        )
