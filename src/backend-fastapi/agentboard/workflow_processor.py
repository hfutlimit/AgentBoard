"""AgentBoard workflow assignment processor (Epic 122 S1 M3 + PR-4).

Before PR-4 the Python processor and the .NET ``WorkflowMqConsumerService``
competed for the same ``agentboard.workflow.broadcast`` queue: whoever
consumed an event handled it, so ``task.ready_for_review`` was picked up by
Python for reviewer assignment AND by .NET for review execution. Two
different actions racing on the same event caused intermittent happy-path
failures and out-of-order flows.

PR-4 split event ownership:

- ``task.ready_for_review`` (broadcast) is now owned by .NET, but .NET
  does not act on it either (it is a pre-assignment event with no
  reviewer). FastAPI additionally publishes
  ``task.review_assignment_needed`` on the internal route (added in
  PR-4) and Python exclusively consumes that on internal_queue.
- After Python picks a reviewer it publishes ``task.review_requested``
  to the agent-directed queue (route="agent"); .NET performs the actual
  review from that message.
- Legacy directed events such as ``EVENT_TASK_REVIEW_REQUESTED`` are no
  longer consumed by Python (internal_queue subscription removed). The
  review loop itself lives in .NET + FastAPI REST endpoints; Python only
  performs the "assignment" step.

Design principle unchanged: **messages only notify, state is always
re-read from the database.** The processor carries no state in the
internal event; it queries REST before triggering assignment, so message
replays or losses never produce duplicate rounds or missed items.

When MQ is not configured (``AGENTBOARD_MQ_URL`` empty) the processor
falls back to DB polling: periodically scan ``in_review`` tasks without
a reviewer and trigger assignment. Correctness is unchanged.

Run:

    python -m agentboard.workflow_processor --mq     # MQ internal mode
    python -m agentboard.workflow_processor --loop   # polling daemon
    python -m agentboard.workflow_processor --once   # single pass
"""
from __future__ import annotations

import argparse
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from .core.infrastructure import messaging as mq
from .mq import (
    # PR-4: internal route event whitelist (the only events Python cares about)
    EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED,
    WORKFLOW_DEFAULT_NAMESPACE,
    WorkflowMessage,
    WorkflowTopology,
)

EVENT_TICKET_REQUESTED = mq.EVENT_TICKET_REQUESTED

log = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        log.warning("env var %s=%r is not an int, falling back to default %s",
                    name, raw, default)
        return default


@dataclass
class WorkflowConsumerConfig:
    """Assignment processor runtime parameters (env-overridable)."""

    api_url: str = "http://127.0.0.1:58124"
    # Service-account abk_ key (or login token); identity for REST calls.
    token: str | None = None
    # Poll interval (seconds) - fallback scan cadence when MQ is absent.
    poll_interval: float = 10.0
    # Max stories per round so one processor cannot hog forever.
    batch_size: int = 20
    http_timeout: float = 30.0
    # Message bus (M2 generalized workflow topology).
    mq: "mq.MQConfig" = field(default_factory=lambda: mq.MQConfig())
    namespace: str = WORKFLOW_DEFAULT_NAMESPACE

    @classmethod
    def from_env(cls) -> "WorkflowConsumerConfig":
        return cls(
            mq=mq.MQConfig.from_env(),
            namespace=os.getenv("AGENTBOARD_WORKFLOW_NAMESPACE",
                                WORKFLOW_DEFAULT_NAMESPACE),
            api_url=os.getenv("AGENTBOARD_API_URL", cls.api_url).rstrip("/"),
            token=os.getenv("AGENTBOARD_WORKER_TOKEN")
            or os.getenv("AGENTBOARD_MCP_TOKEN"),
            poll_interval=float(_env_int("AGENTBOARD_WORKFLOW_WORKER_INTERVAL", 10)),
            batch_size=_env_int("AGENTBOARD_WORKFLOW_WORKER_BATCH", 20),
        )


class WorkflowConsumer:
    """Workflow event consumer: review assignment (PR-4 internal_queue)."""

    #: PR-4: whitelist of internal events this processor handles.
    #: Any other internal event is acked and ignored (not an error).
    _INTERNAL_HANDLERS = {
        EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED: "_handle_task_review_assignment_needed",
        EVENT_TICKET_REQUESTED: "_handle_auto_story_materialization",
    }

    def __init__(self, config: WorkflowConsumerConfig,
                 client: httpx.Client | None = None):
        self.config = config
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=config.api_url, timeout=config.http_timeout,
            headers=({"Authorization": f"Bearer {config.token}"} if config.token else {}),
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "WorkflowConsumer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- HTTP ----------

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        return self.client.request(method, path, **kw)

    # ---------- Assignment actions ----------

    def _assign_task_reviewer(self, task_id: int) -> bool:
        """Auto-assign a Task reviewer after ready_for_review (idempotent, slice 2 M2).

        Sprint 12 majority fan-out: when ``AGENTBOARD_REVIEW_MODE=majority``,
        pass ``?count=AGENTBOARD_REVIEW_QUORUM`` to pick N reviewers at once;
        each receives one ``task.review_requested`` (fanned out inside the
        endpoint). ``count`` is clamped server-side (1..9); no re-check here.

        Success / already-assigned -> True. No online reviewer -> warn + True
        (the developer's poll of ``list_tasks?reviewer_id=me`` acts as the
        fallback). Network error -> raise ``MessageRetry`` so the broker
        requeues (Stage 0 fix: returning False would dead-letter the
        message, contradicting the redelivery semantics).
        """
        # Fan out only in majority mode; single-review mode (default /
        # legacy deployments) keeps one-at-a-time behaviour.
        count = 1
        try:
            from .core.application.service import get_review_mode, get_review_quorum
            if get_review_mode() == "majority":
                count = get_review_quorum()
        except Exception as e:  # pragma: no cover - defensive import failure
            log.debug("review_mode probe failed, falling back to count=1: %s", e)
        try:
            r = self._request("POST", f"/api/tasks/{task_id}/assign-reviewer",
                              params={"count": count})
        except Exception as e:
            log.warning("task %s assign-reviewer request failed (network), "
                        "requeueing: %s", task_id, e)
            raise mq.MessageRetry(
                f"task #{task_id} assign-reviewer network error") from None
        if r.status_code in (200, 201):
            t = r.json()
            log.info("task %s assigned reviewer=%s (status=%s, count=%s)",
                     task_id, t.get("reviewer_id"), t.get("status"), count)
            return True
        if r.status_code == 404:
            log.info("task %s not found (possibly deleted), ignoring", task_id)
            return True
        # 422 (no online reviewer / not in_review) - transient, polling
        # acts as the fallback.
        log.warning("task %s assign-reviewer unsuccessful (HTTP %s): %s",
                    task_id, r.status_code, r.text[:200])
        return True

    def _broadcast_available_tasks(self, story_id: int) -> bool:
        """Deprecated since PR-4 (used to broadcast task.available on story.confirmed).

        Kept for old references (defense); behaviour is a no-op: internal_queue
        no longer receives story.confirmed, and story orchestration falls back
        to the Proposal Processor polling loop (fetch confirmed stories and
        launch agents) instead of relying on this processor's notification.
        Story-level review was retired on 2026-08-09.
        """
        log.debug("_broadcast_available_tasks(story_id=%s) deprecated (PR-4)",
                  story_id)
        return True

    def handle_message(self, msg: WorkflowMessage) -> bool:
        """PR-4: handle one internal orchestration message.

        Only events on the internal whitelist are processed; anything else
        is acked and ignored. Returning False hands the message to the
        dead-letter queue (redelivery semantics are left to polling).
        """
        event = msg.event
        handler = self._INTERNAL_HANDLERS.get(event)
        if handler is None:
            log.info("event %s (entity=%s#%s) not on the internal whitelist, "
                     "acking and ignoring", event, msg.entity_type, msg.entity_id)
            return True
        log.info("event %s (entity=%s#%s ref_id=%s correlation_id=%s): "
                 "routing to %s",
                 event, msg.entity_type, msg.entity_id, msg.ref_id,
                 msg.correlation_id, handler)
        method = getattr(self, handler, None)
        if method is None:
            log.error("internal handler %s has no matching method "
                      "(whitelist mismatch)", handler)
            return False
        return method(msg)

    def _handle_task_review_assignment_needed(self, msg: WorkflowMessage) -> bool:
        """Task entered in_review (PR-4 internal event) -> assign a reviewer.

        Flow:
          1. POST ``/api/tasks/{tid}/assign-reviewer`` (CAS-safe, idempotent)
          2. After the server picks reviewers it publishes
             ``task.review_requested`` to the agent-directed queue
             (route=agent); .NET performs the actual review.

        The old "assign reviewer on task.ready_for_review broadcast" duty
        fully moved here to avoid racing .NET on the same queue.
        """
        task_id = msg.entity_id
        log.info("event task.review_assignment_needed (task=%s assignee=%s): "
                 "auto-assigning Task reviewer",
                 task_id, msg.ref_id)
        return self._assign_task_reviewer(task_id)

    def _execute_auto_story_request(self, request_id: int) -> bool:
        """Execute a deterministic AUTO story request; no CLI agent decides the type."""
        try:
            r = self._request(
                "POST", f"/api/ticket-requests/{request_id}/execute",
            )
        except Exception as e:
            raise mq.MessageRetry(
                f"auto_story request #{request_id} network error: {e}",
            ) from None
        if r.status_code in (200, 201):
            log.info("auto_story request #%s materialized", request_id)
            return True
        if r.status_code in (404, 409):
            # 404=request deleted; 409=claimed by another consumer. Both
            # are safe to ack.
            log.info("auto_story request #%s already handled or gone "
                     "(HTTP %s)", request_id, r.status_code)
            return True
        log.warning("auto_story request #%s failed HTTP %s: %s",
                    request_id, r.status_code, r.text[:300])
        return True

    def _handle_auto_story_materialization(self, msg: WorkflowMessage) -> bool:
        if not msg.ref_id:
            log.error("proposal.ticket_requested without ref_id; cannot "
                      "resolve request")
            return False
        return self._execute_auto_story_request(int(msg.ref_id))

    # ---------- Polling mode (fallback without MQ) ----------

    def run_poll_once(self) -> int:
        """Scan one round and trigger assignment: unassigned in_review tasks
        plus review-timeout reassignment. Returns the number handled.

        Story-level review retired on 2026-08-09: no longer scans backlog
        stories for reviewer assignment.
        """
        assigned = 0
        # MQ fallback: only take over new deterministic auto_story
        # requests; the four manual ticket types stay with the Proposal
        # Agent worker.
        try:
            r = self.client.get(
                "/api/admin/ticket-requests/pending",
                params={"limit": max(1, self.config.batch_size)},
            )
            r.raise_for_status()
            rows = r.json() or []
            for req in rows:
                if req.get("type") != "auto_story":
                    continue
                if self._execute_auto_story_request(int(req["id"])):
                    assigned += 1
        except Exception as e:
            log.warning("poll: executing auto_story requests failed: %s", e)
        # Slice 2 M2 fallback: scan unassigned in_review tasks and
        # auto-assign reviewers.
        try:
            r = self.client.get("/api/tasks", params={
                "status": "in_review", "limit": max(1, self.config.batch_size),
            })
            r.raise_for_status()
            data = r.json()
            task_items = data.get("items", []) if isinstance(data, dict) else (data or [])
            for t in task_items:
                if t.get("reviewer_id") is not None:
                    continue  # already assigned (idempotent skip)
                if self._assign_task_reviewer(t["id"]):
                    assigned += 1
        except Exception as e:
            log.warning("poll: fetching in_review tasks failed: %s", e)
        # Slice 3 M2: timeout reassignment scan (best-effort; idempotent,
        # CAS-arbitrated server-side).
        try:
            r = self._request("POST", "/api/review-stats/reassign-timeout",
                              json={"timeout_minutes": 30, "max_per_run": 20})
            if r.status_code in (200, 201):
                log.info("timeout reassignment scan: %s", r.json())
            else:
                log.warning("timeout reassignment scan unsuccessful "
                            "(HTTP %s): %s", r.status_code, r.text[:200])
        except Exception as e:
            log.warning("timeout reassignment request failed (network, "
                        "retrying next round): %s", e)
        if assigned:
            log.info("poll round assigned %s review task(s)", assigned)
        return assigned

    def run_forever(self, stop: threading.Event | None = None,
                    interval: float | None = None) -> int:
        """Polling daemon loop (used when MQ is not configured)."""
        stop = stop or threading.Event()
        interval = interval if interval is not None else self.config.poll_interval
        cycles = 0
        while not stop.wait(interval):
            cycles += 1
            try:
                self.run_poll_once()
            except Exception:
                log.exception("polling cycle error, retrying next cycle")
        return cycles

    # ---------- MQ mode ----------

    def run_mq_forever(self, stop: threading.Event | None = None,
                       max_messages: int | None = None,
                       idle_timeout: float | None = None,
                       broker: Any | None = None) -> dict:
        """PR-4: consume internal_queue (not the .NET broadcast_queue).

        Earlier versions subscribed to ``topology.broadcast_queue`` and
        raced .NET's ``WorkflowMqConsumerService`` for the same queue -
        the root cause of P0-1.

        Fix: this processor only listens on ``internal_queue`` (the
        orchestration event route added in PR-4). .NET keeps the
        ``broadcast_queue`` plus agent-directed queues. The two are fully
        decoupled; event ownership is unique.

        Falls back to polling automatically when MQ is not configured, so
        a not-yet-ready deployment does not lose functionality.
        """
        if not self.config.mq.enabled:
            log.warning("AGENTBOARD_MQ_URL not configured (or pika "
                        "unavailable), falling back to polling mode")
            cycles = self.run_forever(stop=stop)
            return {"mode": "poll", "cycles": cycles}

        stop = stop or threading.Event()
        broker = broker or mq.PikaWorkflowBroker(self.config.mq, self.config.namespace)
        topology = WorkflowTopology(self.config.namespace)
        broker.declare_topology()
        log.info("workflow assignment processor started in MQ mode: "
                 "ns=%s queue=%s api=%s",
                 self.config.namespace, topology.internal_queue,
                 self.config.api_url)
        try:
            stats = broker.consume(
                topology.internal_queue, self.handle_message,
                max_messages=max_messages, idle_timeout=idle_timeout, stop=stop,
            )
        finally:
            try:
                broker.close()
            except Exception:  # pragma: no cover
                pass
        stats["mode"] = "mq"
        log.info("workflow assignment processor exited (MQ mode): %s", stats)
        return stats


# ===================== CLI =====================

def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentboard.workflow_processor",
        description="AgentBoard workflow assignment processor (Epic 122 S1 M3)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="run one polling round and exit")
    group.add_argument("--loop", action="store_true", help="polling daemon (default)")
    group.add_argument("--mq", action="store_true",
                       help="MQ consumption mode (auto-fallback to polling "
                            "when AGENTBOARD_MQ_URL is unset)")
    parser.add_argument("--mq-url", default=None, help="override AGENTBOARD_MQ_URL")
    parser.add_argument("--api-url", default=None, help="override AGENTBOARD_API_URL")
    parser.add_argument("--interval", type=float, default=None, help="poll interval (seconds)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = WorkflowConsumerConfig.from_env()
    if args.api_url:
        cfg.api_url = args.api_url.rstrip("/")
    if args.interval is not None:
        cfg.poll_interval = args.interval
    if args.mq_url:
        cfg.mq = mq.MQConfig(url=args.mq_url, enabled=True)

    with WorkflowConsumer(cfg) as consumer:
        if args.mq:
            stats = consumer.run_mq_forever()
            log.info("workflow assignment processor exit stats: %s", stats)
        elif args.once:
            n = consumer.run_poll_once()
            log.info("single round done, handled %s item(s)", n)
        else:  # --loop (default)
            cycles = consumer.run_forever()
            log.info("polling exited after %s cycle(s)", cycles)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
