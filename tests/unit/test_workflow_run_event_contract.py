"""WorkflowRun event contract (Active Workflows Overview slice 2, 2026-09-11).

Pinned invariants:
- 17 event types are whitelisted; unknown types raise InvalidWorkflowEvent.
- Each event type has required payload keys; missing keys raise.
- actor_type must be one of user/agent/worker/system.
- retry_kind: execution / review_cycle / mq_delivery. mq_delivery is
  recorded but NOT visible in normal UI.
"""
import pytest

from agentboard.features.workflow_runs.event_contract import (
    EVENT_TYPE_REQUIRED_KEYS,
    InvalidWorkflowEvent,
    RETRY_KINDS,
    RETRY_KINDS_UI_VISIBLE,
    is_retry_kind_ui_visible,
    validate_workflow_event_payload,
)


# ---------- 17 event types ----------

def test_event_type_count_is_19():
    """Pinned: 17 + workflow_reopened (Story reopen) + task_reopened (in-run
    review rejected) are split into TWO distinct events, totalling 19.

    Initial spec said 17 (combined); user 2026-09-11 split them into separate
    types. See openspec/changes/workflow-run-overview-20260911/design.md §3.
    """
    assert len(EVENT_TYPE_REQUIRED_KEYS) == 19


# ---------- Validation: unknown event_type ----------

def test_unknown_event_type_rejected():
    with pytest.raises(InvalidWorkflowEvent, match="unknown workflow event_type"):
        validate_workflow_event_payload(
            "this_is_not_a_real_event", {}, actor_type="system",
        )


# ---------- Validation: bad actor_type ----------

@pytest.mark.parametrize("actor", ["bot", "human", "", None])
def test_bad_actor_type_rejected(actor):
    with pytest.raises(InvalidWorkflowEvent, match="actor_type"):
        validate_workflow_event_payload(
            "workflow_started",
            {"workflow_type": "story", "initial_phase": "design"},
            actor_type=actor,
        )


# ---------- Required payload keys per event type ----------

@pytest.mark.parametrize(
    "event_type,required",
    [(k, v) for k, v in EVENT_TYPE_REQUIRED_KEYS.items()],
)
def test_required_keys_enforced(event_type, required):
    """Missing every required key raises InvalidWorkflowEvent."""
    if not required:
        return  # events with no required keys (e.g. blocked, unblocked)
    with pytest.raises(InvalidWorkflowEvent, match="missing required payload keys"):
        validate_workflow_event_payload(event_type, {}, actor_type="system")


@pytest.mark.parametrize(
    "event_type,payload",
    [
        ("workflow_started", {"workflow_type": "story", "initial_phase": "design"}),
        ("phase_changed", {"from_phase": "design", "to_phase": "development", "reason": "x"}),
        ("task_created", {"task_id": 1, "task_type": "dev", "title": "x"}),
        ("task_assigned", {"task_id": 1, "agent": "codex"}),
        ("task_started", {"task_id": 1, "agent_run_id": 1, "model": "gpt-5"}),
        ("task_submitted", {"task_id": 1, "summary": "done"}),
        ("task_reopened", {"task_id": 1, "reason": "review rejected", "from_status": "in_review"}),
        ("review_requested", {"task_id": 1, "reviewer": "codex", "review_mode": "single"}),
        ("review_completed", {"task_id": 1, "verdict": "approve", "findings": []}),
        ("changes_requested", {"task_id": 1, "reviewer": "codex", "findings_count": 2, "summary": "x"}),
        ("retry_scheduled", {"retry_kind": "execution", "attempt": 1, "reason": "cli exit 1"}),
        ("agent_heartbeat_lost", {"agent_id": "codex", "last_seen_at": "2026-09-11T10:00:00Z", "probe_message": "timeout"}),
        ("worker_lease_expired", {"worker_id": "w1", "lease_expires_at": "2026-09-11T10:00:00Z"}),
        ("blocked", {"reason": "waiting on dependency"}),
        ("unblocked", {"reason": "dependency resolved"}),
        ("workflow_completed", {"summary": "ok", "duration_seconds": 100, "task_count": 3}),
        ("workflow_failed", {"reason": "x", "failed_at_stage": "impl", "last_error": "timeout"}),
        ("workflow_cancelled", {"cancelled_by": "user", "reason": "scope changed"}),
        ("workflow_reopened", {"new_run_id": 2, "reopened_from_run_id": 1, "reason": "story_reopened"}),
    ],
)
def test_valid_payload_passes(event_type, payload):
    validate_workflow_event_payload(event_type, payload, actor_type="system")


# ---------- retry_kind UI visibility ----------

def test_retry_kinds():
    assert RETRY_KINDS == frozenset({"execution", "review_cycle", "mq_delivery"})


def test_mq_delivery_hidden_from_ui():
    """mq_delivery is recorded for diagnostics but not in normal UI."""
    assert is_retry_kind_ui_visible("mq_delivery") is False


def test_execution_and_review_cycle_visible():
    assert is_retry_kind_ui_visible("execution") is True
    assert is_retry_kind_ui_visible("review_cycle") is True


def test_retry_kinds_ui_visible_subset():
    assert RETRY_KINDS_UI_VISIBLE.issubset(RETRY_KINDS)
    assert "mq_delivery" not in RETRY_KINDS_UI_VISIBLE
