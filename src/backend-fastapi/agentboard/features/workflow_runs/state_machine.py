"""WorkflowRun state machine + phase transition graph (slice 1, 2026-09-11).

Two strict graphs, both enforced in the service layer (and validated by tests):

- ``WORKFLOW_RUN_TRANSITIONS`` — terminal-vs-running status moves. ``failed`` is
  **strictly terminal**: there is no path ``failed -> running``. Recoverable
  execution failures keep the WorkflowRun in ``running`` while a new
  AgentRun attempt is created; only retry exhaustion flips the run to
  ``failed``. Retrying a terminal run = new run + ``reopened_from_run_id``.

- ``WORKFLOW_PHASE_TRANSITIONS`` — explicit, narrow graph. We do NOT allow
  arbitrary ``phase = anything``. Story v1 permits
  ``design -> development -> qa`` and ``qa -> development`` (rework). New
  transitions must be added explicitly to the graph (e.g. a future
  ``rework_requested`` event targeting ``design``).
"""
from __future__ import annotations

from .models import WORKFLOW_RUN_TERMINAL_STATUSES

# Status state machine.
# Terminal states (``completed``/``failed``/``cancelled``) have empty targets.
# ``failed`` deliberately NOT retryable — retry must create a new run.
WORKFLOW_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued":    frozenset({"running", "cancelled", "failed"}),
    "running":   frozenset({"waiting", "blocked", "completed", "failed", "cancelled"}),
    "waiting":   frozenset({"running", "blocked", "cancelled", "failed"}),
    "blocked":   frozenset({"running", "cancelled", "failed"}),
    "completed": frozenset(),
    "failed":    frozenset(),
    "cancelled": frozenset(),
}


class IllegalWorkflowTransition(Exception):
    """Raised when a status transition violates ``WORKFLOW_RUN_TRANSITIONS``."""


def can_transition_status(from_status: str, to_status: str) -> bool:
    return to_status in WORKFLOW_RUN_TRANSITIONS.get(from_status, frozenset())


def assert_transition_status(from_status: str, to_status: str) -> None:
    if not can_transition_status(from_status, to_status):
        raise IllegalWorkflowTransition(
            f"workflow run status {from_status!r} cannot transition to {to_status!r}"
        )


def is_terminal_status(status: str) -> bool:
    return status in WORKFLOW_RUN_TERMINAL_STATUSES


# Phase transition graph — explicit, not "anything goes".
# Story v1: design → development → qa, plus qa → development (rework).
# Adding a new transition (e.g. development → design) requires updating this
# graph AND emitting a new event type (e.g. ``rework_requested``).
WORKFLOW_PHASE_TRANSITIONS: dict[str, frozenset[str]] = {
    "design":      frozenset({"development"}),
    "development": frozenset({"qa"}),
    "qa":          frozenset({"development"}),
}


class IllegalPhaseTransition(Exception):
    """Raised when a phase change is not in ``WORKFLOW_PHASE_TRANSITIONS``."""


def can_transition_phase(from_phase: str | None, to_phase: str) -> bool:
    """Check whether ``from_phase -> to_phase`` is in the graph.

    The initial phase (``from_phase is None``) is only ``design``.
    """
    if from_phase is None:
        return to_phase == "design"
    return to_phase in WORKFLOW_PHASE_TRANSITIONS.get(from_phase, frozenset())


def assert_transition_phase(from_phase: str | None, to_phase: str) -> None:
    if not can_transition_phase(from_phase, to_phase):
        raise IllegalPhaseTransition(
            f"workflow run phase {from_phase!r} cannot transition to {to_phase!r}"
        )


__all__ = [
    "WORKFLOW_RUN_TRANSITIONS",
    "WORKFLOW_PHASE_TRANSITIONS",
    "IllegalWorkflowTransition",
    "IllegalPhaseTransition",
    "can_transition_status",
    "can_transition_phase",
    "assert_transition_status",
    "assert_transition_phase",
    "is_terminal_status",
]
