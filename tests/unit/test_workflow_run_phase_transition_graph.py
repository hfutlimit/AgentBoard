"""WorkflowRun phase transition graph (Active Workflows Overview slice 1, 2026-09-11).

Pinned invariants:
- The graph is EXPLICIT and NARROW. We never allow ``phase = anything`` —
  every phase change must be in ``WORKFLOW_PHASE_TRANSITIONS``.
- Story v1 permits: ``design -> development -> qa`` plus ``qa -> development``
  (rework when QA finds a defect).
- ``development -> design`` is NOT permitted in v1. If a future change needs
  it, the correct approach is to add a new ``rework_requested`` event and
  a new graph edge explicitly — do NOT mutate this file's allowed set.
"""
import os

os.environ.setdefault("AGENTBOARD_DB_URL", "sqlite:///./_test_workflow_runs_phase_graph_tmp.db")

import pytest

from agentboard.features.workflow_runs.state_machine import (
    IllegalPhaseTransition,
    WORKFLOW_PHASE_TRANSITIONS,
    assert_transition_phase,
    can_transition_phase,
)


# ---------- Initial phase ----------

def test_initial_phase_is_design():
    """from_phase=None is only legal when to_phase='design' (initial entry)."""
    assert can_transition_phase(None, "design")
    assert_transition_phase(None, "design")
    assert not can_transition_phase(None, "development")
    assert not can_transition_phase(None, "qa")


# ---------- Legal transitions (Story v1) ----------

@pytest.mark.parametrize(
    "from_phase,to_phase",
    [
        ("design", "development"),
        ("development", "qa"),
        ("qa", "development"),  # rework
    ],
)
def test_legal_phase_transitions(from_phase, to_phase):
    assert can_transition_phase(from_phase, to_phase)
    assert_transition_phase(from_phase, to_phase)


# ---------- Illegal mutations (the whole point) ----------

@pytest.mark.parametrize(
    "from_phase,to_phase",
    [
        # Skipping a phase.
        ("design", "qa"),
        # Loops on the same phase.
        ("design", "design"),
        ("development", "development"),
        ("qa", "qa"),
        # Backward moves that are not in the explicit graph.
        ("development", "design"),
        # Forward moves the graph doesn't allow.
        ("qa", "design"),
        # Random phases (catches "anything goes" regressions).
        ("design", "shipping"),
        ("development", "rollout"),
    ],
)
def test_illegal_phase_transitions(from_phase, to_phase):
    assert not can_transition_phase(from_phase, to_phase)
    with pytest.raises(IllegalPhaseTransition):
        assert_transition_phase(from_phase, to_phase)


def test_graph_explicit_not_arbitrary_pinned_invariant():
    """Pinned: phase transitions are EXPLICIT, not 'anything goes'.

    This test asserts the shape of the graph itself. If a future change
    wants to add an edge, this test should be updated EXPLICITLY with the
    new edge and a comment explaining why.
    """
    assert WORKFLOW_PHASE_TRANSITIONS == {
        "design":      frozenset({"development"}),
        "development": frozenset({"qa"}),
        "qa":          frozenset({"development"}),
    }


def test_graph_is_irreflexive():
    """No phase transitions to itself (would defeat the purpose of a graph)."""
    for from_phase, targets in WORKFLOW_PHASE_TRANSITIONS.items():
        assert from_phase not in targets, (
            f"phase {from_phase!r} self-loops; remove it from the graph"
        )


def test_graph_known_phases_only():
    """No unknown phase strings leak into the graph."""
    known = {"design", "development", "qa"}
    for from_phase, targets in WORKFLOW_PHASE_TRANSITIONS.items():
        assert from_phase in known
        for target in targets:
            assert target in known
