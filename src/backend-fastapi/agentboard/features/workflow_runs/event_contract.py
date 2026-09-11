"""WorkflowRun event contract (slice 2, 2026-09-11).

17 event types (full granularity per spec) + per-type required fields.
The contract is the **source of truth** for what every WorkflowEvent must look
like; ``emit_workflow_event()`` validates payload against this contract before
writing to ``workflow_run_events``.

**Naming distinction** (user-pinned 2026-09-11):
- ``task_reopened`` — in-run review rejected → task done → in_progress
- ``workflow_reopened`` — Story reopen or retry terminal workflow, new run

**retry_kind** is event/diagnostic metadata, **not** a unified workflow retry
counter. ``mq_delivery`` retries stay out of normal UI.
"""
from __future__ import annotations

from typing import Any, Literal

# ---- Status-side enums ----

EventType = Literal[
    # Lifecycle (7)
    "workflow_started",
    "phase_changed",
    "workflow_reopened",
    "workflow_completed",
    "workflow_failed",
    "workflow_cancelled",
    "task_reopened",
    # Task (5)
    "task_created",
    "task_assigned",
    "task_started",
    "task_submitted",
    "review_requested",
    # Review outcome (2)
    "review_completed",
    "changes_requested",
    # Block / unblock (2)
    "blocked",
    "unblocked",
    # Retry (1)
    "retry_scheduled",
    # Operations (2)
    "agent_heartbeat_lost",
    "worker_lease_expired",
]


ActorType = Literal["user", "agent", "worker", "system"]

RetryKind = Literal["execution", "review_cycle", "mq_delivery"]


# ---- Allowed event types + required payload keys ----

# Each entry: set of required keys in payload. Empty set = no payload required.
EVENT_TYPE_REQUIRED_KEYS: dict[str, frozenset[str]] = {
    # Lifecycle
    "workflow_started":      frozenset({"workflow_type", "initial_phase"}),
    "phase_changed":         frozenset({"from_phase", "to_phase", "reason"}),
    "workflow_reopened":     frozenset({"new_run_id", "reopened_from_run_id", "reason"}),
    "workflow_completed":    frozenset({"summary", "duration_seconds", "task_count"}),
    "workflow_failed":       frozenset({"reason", "failed_at_stage", "last_error"}),
    "workflow_cancelled":    frozenset({"cancelled_by", "reason"}),
    "task_reopened":         frozenset({"task_id", "reason", "from_status"}),
    # Task
    "task_created":          frozenset({"task_id", "task_type", "title"}),
    "task_assigned":         frozenset({"task_id", "agent"}),
    "task_started":          frozenset({"task_id", "agent_run_id", "model"}),
    "task_submitted":        frozenset({"task_id", "summary"}),
    "review_requested":      frozenset({"task_id", "reviewer", "review_mode"}),
    # Review outcome
    "review_completed":      frozenset({"task_id", "verdict", "findings"}),
    "changes_requested":     frozenset({"task_id", "reviewer", "findings_count", "summary"}),
    # Block / unblock
    "blocked":               frozenset({"reason"}),
    "unblocked":             frozenset({"reason"}),
    # Retry
    "retry_scheduled":       frozenset({"retry_kind", "attempt", "reason"}),
    # Operations
    "agent_heartbeat_lost":  frozenset({"agent_id", "last_seen_at", "probe_message"}),
    "worker_lease_expired":  frozenset({"worker_id", "lease_expires_at"}),
}


# ---- Validation ----

class InvalidWorkflowEvent(Exception):
    """Raised by ``validate_workflow_event_payload`` on a contract violation."""


def validate_workflow_event_payload(
    event_type: str,
    payload: dict | None,
    *,
    actor_type: str,
) -> None:
    """Validate payload against the event_type's required keys + actor_type.

    Raises ``InvalidWorkflowEvent`` on any contract violation. Empty payload
    is treated as ``{}`` (allowed only when no required keys).

    Also enforces value-level constraints (e.g. ``retry_kind`` membership).
    """
    if event_type not in EVENT_TYPE_REQUIRED_KEYS:
        raise InvalidWorkflowEvent(
            f"unknown workflow event_type {event_type!r}; "
            f"allowed: {sorted(EVENT_TYPE_REQUIRED_KEYS.keys())}"
        )
    if actor_type not in ("user", "agent", "worker", "system"):
        raise InvalidWorkflowEvent(
            f"actor_type must be one of user/agent/worker/system, got {actor_type!r}"
        )
    payload = payload or {}
    required = EVENT_TYPE_REQUIRED_KEYS[event_type]
    missing = required - set(payload.keys())
    if missing:
        raise InvalidWorkflowEvent(
            f"event_type={event_type!r} missing required payload keys: "
            f"{sorted(missing)}"
        )
    # Value-level constraints.
    if event_type == "retry_scheduled":
        retry_kind = payload.get("retry_kind")
        if retry_kind not in RETRY_KINDS:
            raise InvalidWorkflowEvent(
                f"retry_kind must be one of {sorted(RETRY_KINDS)}, got {retry_kind!r}"
            )


# ---- retry_kind helpers ----

RETRY_KINDS: frozenset[str] = frozenset({"execution", "review_cycle", "mq_delivery"})

# Which retry_kinds are visible in the normal UI (Active Workflows card /
# timeline main display). MQ delivery stays in diagnostics only.
RETRY_KINDS_UI_VISIBLE: frozenset[str] = frozenset({"execution", "review_cycle"})


def is_retry_kind_ui_visible(retry_kind: str) -> bool:
    return retry_kind in RETRY_KINDS_UI_VISIBLE


__all__ = [
    "EventType",
    "ActorType",
    "RetryKind",
    "EVENT_TYPE_REQUIRED_KEYS",
    "RETRY_KINDS",
    "RETRY_KINDS_UI_VISIBLE",
    "InvalidWorkflowEvent",
    "validate_workflow_event_payload",
    "is_retry_kind_ui_visible",
]
