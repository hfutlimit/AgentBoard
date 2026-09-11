"""WorkflowRun status state machine (Active Workflows Overview slice 1, 2026-09-11).

Key invariants pinned by these tests:
- Terminal states (completed / failed / cancelled) have empty transition sets.
- ``failed`` is **strictly terminal**: there is no ``failed -> running`` path.
  This is the single most important rule of the WorkflowRun state machine
  (it keeps ``failed`` dashboards trustworthy and prevents "temporarily
  errored" semantic drift).
- ``cancelled`` is also terminal.
- ``queued`` is the only entry state.
- All non-terminal states can reach ``cancelled`` (user override escape hatch).
- All non-terminal states can reach ``failed`` (catastrophic failure).
"""
import os

os.environ.setdefault("AGENTBOARD_DB_URL", "sqlite:///./_test_workflow_runs_state_machine_tmp.db")

import pytest

from agentboard.features.workflow_runs.state_machine import (
    IllegalWorkflowTransition,
    WORKFLOW_RUN_TRANSITIONS,
    assert_transition_status,
    can_transition_status,
    is_terminal_status,
)


# ---------- Status state machine ----------

@pytest.mark.parametrize(
    "from_status,to_status",
    [
        ("queued", "running"),
        ("queued", "cancelled"),
        ("queued", "failed"),
        ("running", "waiting"),
        ("running", "blocked"),
        ("running", "completed"),
        ("running", "failed"),
        ("running", "cancelled"),
        ("waiting", "running"),
        ("waiting", "blocked"),
        ("waiting", "cancelled"),
        ("waiting", "failed"),
        ("blocked", "running"),
        ("blocked", "cancelled"),
        ("blocked", "failed"),
    ],
)
def test_legal_transitions(from_status, to_status):
    assert can_transition_status(from_status, to_status)
    assert_transition_status(from_status, to_status)  # must not raise


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        # Terminal states cannot go anywhere.
        ("completed", "running"),
        ("completed", "queued"),
        ("completed", "failed"),
        ("completed", "cancelled"),
        # failed is STRICTLY terminal: no retry path.
        ("failed", "running"),
        ("failed", "queued"),
        ("failed", "blocked"),
        ("failed", "cancelled"),
        ("cancelled", "running"),
        ("cancelled", "queued"),
        ("cancelled", "failed"),
        # Backwards moves that don't make sense.
        ("queued", "waiting"),
        ("queued", "blocked"),
        ("queued", "completed"),
        ("running", "queued"),
        ("waiting", "completed"),
    ],
)
def test_illegal_transitions(from_status, to_status):
    assert not can_transition_status(from_status, to_status)
    with pytest.raises(IllegalWorkflowTransition):
        assert_transition_status(from_status, to_status)


def test_terminal_statuses():
    """completed / failed / cancelled are terminal; nothing else is."""
    assert is_terminal_status("completed")
    assert is_terminal_status("failed")
    assert is_terminal_status("cancelled")
    assert not is_terminal_status("queued")
    assert not is_terminal_status("running")
    assert not is_terminal_status("waiting")
    assert not is_terminal_status("blocked")


def test_terminal_transition_sets_are_empty():
    """Terminal states have empty transition sets — invariant."""
    for status in ("completed", "failed", "cancelled"):
        assert WORKFLOW_RUN_TRANSITIONS[status] == frozenset(), (
            f"{status!r} should be terminal with empty transitions, "
            f"got {WORKFLOW_RUN_TRANSITIONS[status]!r}"
        )


def test_failed_is_strictly_terminal_pinned_invariant():
    """Pinned: failed cannot transition to running, ever.

    If this test fails, the project has reintroduced 'temporarily errored'
    semantics for WorkflowRun status, which breaks dashboard trust and
    monitoring. See openspec/changes/workflow-run-overview-20260911/design.md
    §2.1 'Status machine' for the rationale.
    """
    assert "running" not in WORKFLOW_RUN_TRANSITIONS["failed"]
    assert "queued" not in WORKFLOW_RUN_TRANSITIONS["failed"]
    # The only way to retry a failed run is to create a new one with
    # reopened_from_run_id (see slice 2 emit_workflow_event).
    assert WORKFLOW_RUN_TRANSITIONS["failed"] == frozenset()


def test_all_statuses_have_transition_row():
    """Every status in the model has a transition row, even if empty (terminal)."""
    expected = {"queued", "running", "waiting", "blocked", "completed", "failed", "cancelled"}
    assert set(WORKFLOW_RUN_TRANSITIONS.keys()) == expected
