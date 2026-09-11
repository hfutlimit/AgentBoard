"""WorkflowRun service helpers (slice 1, 2026-09-11).

Slice 1 ships the building blocks:

- ``create_workflow_run`` — instantiate a new run (status=queued).
- ``transition_workflow_run_status`` — apply WORKFLOW_RUN_TRANSITIONS; raise
  IllegalWorkflowTransition on illegal moves. ``failed`` is strictly terminal.
- ``transition_workflow_run_phase`` — apply WORKFLOW_PHASE_TRANSITIONS;
  raise IllegalPhaseTransition on illegal moves. No "phase = anything".

Slice 2 will add ``emit_workflow_event`` which writes to ``workflow_run_events``
and bumps ``last_activity_at`` / ``version`` atomically inside the same
transaction as the state transition.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...core.common.models import utc_now
from .models import WorkflowRun
from .state_machine import (
    IllegalPhaseTransition,
    IllegalWorkflowTransition,
    assert_transition_phase,
    assert_transition_status,
)


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
        # v1 narrow check; loosen when other workflow types land.
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
) -> WorkflowRun:
    """Apply WORKFLOW_PHASE_TRANSITIONS to ``run``.

    Phase changes do NOT change the run's status — they are an orthogonal
    axis. Slice 2 will also emit a ``phase_changed`` event with
    ``from_phase`` / ``to_phase`` / ``reason``.
    """
    assert_transition_phase(run.phase, to_phase)
    run.phase = to_phase
    run.version = (run.version or 1) + 1
    s.add(run)
    s.flush()
    return run


def touch_workflow_run(s: Session, run: WorkflowRun) -> WorkflowRun:
    """Bump ``last_activity_at`` + ``version`` without changing status/phase.

    Used by ``emit_workflow_event`` (slice 2) to keep the run "warm" so
    the active-workflows panel can show elapsed time correctly.
    """
    run.last_activity_at = utc_now()
    run.version = (run.version or 1) + 1
    s.add(run)
    return run


__all__ = [
    "create_workflow_run",
    "transition_workflow_run_status",
    "transition_workflow_run_phase",
    "touch_workflow_run",
    # re-export exception types so callers can import from a single place
    "IllegalWorkflowTransition",
    "IllegalPhaseTransition",
]
