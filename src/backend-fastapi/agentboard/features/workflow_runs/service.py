"""WorkflowRun service helpers (slice 1+2, 2026-09-11).

Slice 1 (foundations):
- ``create_workflow_run`` — instantiate a new run (status=queued).
- ``transition_workflow_run_status`` — apply WORKFLOW_RUN_TRANSITIONS; raise
  IllegalWorkflowTransition on illegal moves. ``failed`` is strictly terminal.
- ``transition_workflow_run_phase`` — apply WORKFLOW_PHASE_TRANSITIONS;
  raise IllegalPhaseTransition on illegal moves. No "phase = anything".
- ``touch_workflow_run`` — bump ``last_activity_at`` + ``version`` without
  changing status/phase.

Slice 2 (event emission):
- ``emit_workflow_event`` — write one event row + atomically bump the
  parent run's ``last_activity_at`` and ``version`` in the same session.
- ``ensure_story_workflow_run`` — find-or-create the WorkflowRun for a Story
  (used by every task event to anchor events to a single run).
- ``reopen_story_workflow`` — Story reopen: create a new WorkflowRun with
  ``reopened_from_run_id`` set, status=queued, phase=design. Old run is
  NEVER mutated (terminal states are immutable).
- ``record_retry_scheduled`` — emit ``retry_scheduled`` with retry_kind.

Slice 6 will hook ``emit_workflow_event`` into the SignalR ``workflow.changed``
broadcast; slice 2 only writes to the DB.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ...core.common.models import utc_now
from .event_contract import validate_workflow_event_payload
from .models import WorkflowRun, WorkflowRunEvent
from .state_machine import (
    IllegalPhaseTransition,
    IllegalWorkflowTransition,
    assert_transition_phase,
    assert_transition_status,
)


# ---------- Slice 1 ----------

def create_workflow_run(
    s: Session,
    *,
    project_id: int,
    workflow_type: str,
    story_id: int | None = None,
    schedule_id: int | None = None,
    reopened_from_run_id: int | None = None,
    initial_phase: str | None = None,
) -> WorkflowRun:
    """Insert a new WorkflowRun row in ``queued`` status.

    The application layer is responsible for setting ``workflow_type``; v1
    only writes ``"story"`` but the column has no CHECK (schema extensibility).
    """
    if workflow_type != "story":
        raise ValueError(
            f"workflow_type={workflow_type!r} is not supported in v1; "
            "v1 only accepts 'story'"
        )
    if initial_phase is not None:
        assert_transition_phase(None, initial_phase)

    run = WorkflowRun(
        project_id=project_id,
        workflow_type=workflow_type,
        story_id=story_id,
        schedule_id=schedule_id,
        reopened_from_run_id=reopened_from_run_id,
        status="queued",
        phase=initial_phase,
    )
    s.add(run)
    s.flush()
    return run


def transition_workflow_run_status(
    s: Session,
    run: WorkflowRun,
    *,
    to_status: str,
    set_finished_at: bool = False,
) -> WorkflowRun:
    """Apply WORKFLOW_RUN_TRANSITIONS to ``run``.

    Raises IllegalWorkflowTransition if the move is not legal (e.g.
    ``failed -> running`` is always rejected).
    """
    assert_transition_status(run.status, to_status)
    run.status = to_status
    run.version = (run.version or 1) + 1
    if to_status == "running" and run.started_at is None:
        run.started_at = utc_now()
    if set_finished_at and to_status in ("completed", "failed", "cancelled"):
        run.finished_at = utc_now()
    s.add(run)
    s.flush()
    return run


def transition_workflow_run_phase(
    s: Session,
    run: WorkflowRun,
    *,
    to_phase: str,
    reason: str | None = None,
) -> WorkflowRun:
    """Apply WORKFLOW_PHASE_TRANSITIONS to ``run`` and emit ``phase_changed``.

    Phase changes do NOT change the run's status — they are an orthogonal
    axis. The phase transition itself is validated by the explicit graph in
    ``WORKFLOW_PHASE_TRANSITIONS`` (no "phase = anything" allowed).

    The ``phase_changed`` event is emitted atomically (same session).
    """
    from_phase = run.phase
    assert_transition_phase(from_phase, to_phase)
    run.phase = to_phase
    run.version = (run.version or 1) + 1
    s.add(run)
    s.flush()
    emit_workflow_event(
        s,
        workflow_run_id=run.id,
        event_type="phase_changed",
        actor_type="system",
        phase=to_phase,
        state="running",
        summary=f"Phase {from_phase or 'none'} → {to_phase}",
        payload={
            "from_phase": from_phase,
            "to_phase": to_phase,
            "reason": reason or "",
        },
    )
    return run


def touch_workflow_run(s: Session, run: WorkflowRun) -> WorkflowRun:
    """Bump ``last_activity_at`` + ``version`` without changing status/phase.

    Used by ``emit_workflow_event`` to keep the run "warm" so the active-
    workflows panel can show elapsed time correctly.
    """
    run.last_activity_at = utc_now()
    run.version = (run.version or 1) + 1
    s.add(run)
    return run


# ---------- Slice 2 ----------

def emit_workflow_event(
    s: Session,
    *,
    workflow_run_id: int,
    event_type: str,
    actor_type: str,
    actor_id: int | None = None,
    phase: str | None = None,
    state: str | None = None,
    task_id: int | None = None,
    agent_run_id: int | None = None,
    summary: str | None = None,
    payload: dict | None = None,
) -> WorkflowRunEvent:
    """Write one event row + atomically bump the parent run.

    Validates the payload against ``EVENT_TYPE_REQUIRED_KEYS`` BEFORE writing
    (catches contract drift at the call site, not deep in the event log).

    Updates the run's ``last_activity_at`` and ``version`` so the active-
    workflows panel can show "last activity N seconds ago" correctly and
    the SignalR ``workflow.changed`` push can carry the new version (slice 6).
    """
    validate_workflow_event_payload(
        event_type, payload, actor_type=actor_type,
    )
    payload_json = json.dumps(payload or {}, ensure_ascii=False)

    event = WorkflowRunEvent(
        workflow_run_id=workflow_run_id,
        event_type=event_type,
        phase=phase,
        state=state,
        actor_type=actor_type,
        actor_id=actor_id,
        task_id=task_id,
        agent_run_id=agent_run_id,
        summary=summary,
        payload=payload_json,
    )
    s.add(event)
    s.flush()

    # Atomic parent-run touch (same session, same transaction).
    run = s.get(WorkflowRun, workflow_run_id)
    if run is not None:
        touch_workflow_run(s, run)
        s.flush()
    return event


def ensure_story_workflow_run(
    s: Session,
    *,
    story_id: int,
    project_id: int,
) -> WorkflowRun:
    """Find-or-create the WorkflowRun for a Story.

    v1: every Story has at most ONE *active* (non-terminal) WorkflowRun. If
    the Story already has a non-terminal run, return it. Otherwise create a
    new run with status=queued, phase=design.

    Slice 7 reconciliation will eventually reconcile this against the event
    stream; for now this is a simple "latest active run" lookup.
    """
    existing = (
        s.query(WorkflowRun)
        .filter(WorkflowRun.story_id == story_id)
        .filter(WorkflowRun.status.in_(("queued", "running", "waiting", "blocked")))
        .order_by(WorkflowRun.id.desc())
        .first()
    )
    if existing is not None:
        return existing
    return create_workflow_run(
        s,
        project_id=project_id,
        workflow_type="story",
        story_id=story_id,
        initial_phase="design",
    )


def reopen_story_workflow(
    s: Session,
    *,
    story_id: int,
    project_id: int,
    reason: str = "story_reopened",
) -> WorkflowRun:
    """Story reopen: create a new WorkflowRun linked to the previous one.

    Pinned by user 2026-09-11: the old run is NEVER mutated. If the previous
    run is still active, mark it cancelled first (no concurrent runs), then
    create the new run with ``reopened_from_run_id`` pointing at it.
    """
    previous = (
        s.query(WorkflowRun)
        .filter(WorkflowRun.story_id == story_id)
        .order_by(WorkflowRun.id.desc())
        .first()
    )
    new_run = create_workflow_run(
        s,
        project_id=project_id,
        workflow_type="story",
        story_id=story_id,
        reopened_from_run_id=previous.id if previous is not None else None,
        initial_phase="design",
    )
    # Cross-run lifecycle event (distinct from in-run `task_reopened`).
    emit_workflow_event(
        s,
        workflow_run_id=new_run.id,
        event_type="workflow_reopened",
        actor_type="user",
        summary=f"Reopened from Run #{previous.id if previous else 'N/A'}",
        payload={
            "new_run_id": new_run.id,
            "reopened_from_run_id": previous.id if previous is not None else None,
            "reason": reason,
        },
    )
    return new_run


def record_retry_scheduled(
    s: Session,
    *,
    workflow_run_id: int,
    retry_kind: str,
    attempt: int,
    reason: str,
    actor_type: str = "system",
    actor_id: int | None = None,
    task_id: int | None = None,
    agent_run_id: int | None = None,
    last_error: str | None = None,
) -> WorkflowRunEvent:
    """Emit a ``retry_scheduled`` event with the given retry_kind.

    ``retry_kind`` must be one of ``execution`` / ``review_cycle`` / ``mq_delivery``.
    The latter stays in diagnostics (UI helper in slice 5) but is still recorded
    so audit / reconciliation can see retry pressure.
    """
    payload: dict[str, Any] = {
        "retry_kind": retry_kind,
        "attempt": attempt,
        "reason": reason,
    }
    if last_error is not None:
        payload["last_error"] = last_error
    return emit_workflow_event(
        s,
        workflow_run_id=workflow_run_id,
        event_type="retry_scheduled",
        actor_type=actor_type,
        actor_id=actor_id,
        task_id=task_id,
        agent_run_id=agent_run_id,
        phase=None,
        state="retrying",
        summary=f"Retry ({retry_kind}) attempt {attempt}: {reason}",
        payload=payload,
    )


__all__ = [
    # Slice 1
    "create_workflow_run",
    "transition_workflow_run_status",
    "transition_workflow_run_phase",
    "touch_workflow_run",
    # Slice 2
    "emit_workflow_event",
    "ensure_story_workflow_run",
    "reopen_story_workflow",
    "record_retry_scheduled",
    # re-export exception types so callers can import from a single place
    "IllegalWorkflowTransition",
    "IllegalPhaseTransition",
]
