"""Transactional outbox for workflow events (Sprint 8 / 2026-08-28).

Problem
-------
The current ``POST /api/tasks/{tid}/review`` path is the textbook
"DB success + MQ failure" trap:

1. ``set_status`` commits the new task status (its own transaction).
2. ``create_comment`` commits the review comment (its own transaction).
3. ``publish_workflow_event`` is called best-effort, *outside* the
   router's try block. If the broker is unreachable, the call returns
   ``False`` and the event is lost. The task shows ``done`` in the
   UI but no worker is woken up to advance the next step.
4. The successor-unlock and story-completion events are wrapped in
   their own ``try/except Exception: pass`` blocks — same silent-loss
   pattern.

GPT review 2026-08-26 (commit ``6e5ce0c``) called this out as the
single biggest reliability gap in the agentic workflow. This module
is the targeted fix.

Solution
--------
A new ``workflow_outbox`` table records every workflow event the
system wants to publish, in the **same DB transaction** as the
state change that produced it. A background ``OutboxPublisher`` then
drains unpublished rows to the broker. The router is reduced to::

    with s.begin():
        t = service.review_task(s, ...)              # state change
        outbox.add(s, EVENT_TASK_REVIEWED, "task", t.id, ...)
        for succ in successors:
            outbox.add(s, EVENT_TASK_AVAILABLE, "task", succ.id, ...)

The MQ publisher is the only place that touches RabbitMQ. The
application commit is the contract: either both happen, or neither
does.

Failure modes
-------------
* MQ broker down at HTTP time → row is unpublished, response still
  200, publisher drains later when broker comes back.
* Publisher crashes mid-drain → unpublished rows stay; next
  publisher instance picks them up. ``attempts`` and ``last_error``
  columns make this visible in operator dashboards.
* Poison row (publish raises an unexpected exception) → row marked
  ``dead``, surfaced via a counter on ``/health``.

Out of scope
------------
* Migrating the other ~17 ``publish_workflow_event`` call sites in
  proposals/projects/etc. routers. This change covers the
  ``review_task`` path only; a follow-up change generalizes the
  pattern.
* Replacing ``publish_workflow_event`` itself. It is the publisher's
  transport. Existing callers that bypass the outbox keep working.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Session

from ...core.common.models import Base, utc_now

log = logging.getLogger("agentboard.core.infrastructure.outbox")

MAX_PAYLOAD_BYTES = 16 * 1024  # outbox rows are advisory; cap payload to keep rows small
MAX_ATTEMPTS = 8                # before the row is marked dead and surfaced in /health
RETRY_BACKOFF_SECONDS = (1, 2, 4, 8, 16, 32, 60, 120)  # exponential, capped at 2 min


def _utc_now() -> datetime:
    """Local helper: tz-aware UTC now (works under the same tests that monkey-patch utc_now)."""
    return utc_now() or datetime.now(tz=timezone.utc)


class WorkflowOutbox(Base):
    """SQLAlchemy model for the workflow outbox table.

    Stored in the same database as the rest of AgentBoard (SQLite for
    dev/test, MariaDB for production) so the application commit can
    cover both the state change and the outbox row.
    """

    __tablename__ = "workflow_outbox"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    # Same identifiers as the broker event — no new event-type vocabulary.
    event = Column(String(64), nullable=False, index=True)
    entity_type = Column(String(32), nullable=False, index=True)
    entity_id = Column(BigInteger, nullable=False, index=True)
    ref_id = Column(BigInteger, nullable=True)
    agent_id = Column(String(128), nullable=True)

    # Optional future-proofing payload (e.g. for events that need
    # extra fields beyond what publish_workflow_event accepts).
    payload_json = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # NULL == unpublished; non-NULL == published_at UTC timestamp.
    published_at = Column(DateTime(timezone=True), nullable=True, index=True)
    attempts = Column(Integer, nullable=False, server_default="0")
    last_error = Column(Text, nullable=True)

    __table_args__ = (
        # Hot path: "give me the next batch of unpublished rows, oldest
        # first". Composite index keeps the publisher's scan O(log n)
        # even after a backlog accumulates during a broker outage.
        Index("ix_workflow_outbox_unpublished", "published_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# Repository — small, no DI; callers (router + publisher) hold a session.
# ---------------------------------------------------------------------------


class OutboxRepository:
    """Add / fetch / mark methods for the workflow outbox.

    All methods take an open ``Session`` so the caller controls
    transaction boundaries. The publisher commits each batch
    independently; the router relies on the surrounding
    ``with s.begin():`` to commit the add().
    """

    def __init__(self, max_attempts: int = MAX_ATTEMPTS) -> None:
        self.max_attempts = max_attempts

    def add(
        self,
        s: Session,
        event: str,
        entity_type: str,
        entity_id: int,
        ref_id: int | None = None,
        *,
        agent_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> WorkflowOutbox:
        """Append a row to the outbox. Caller is responsible for
        committing the surrounding transaction.
        """
        payload_text: str | None = None
        if payload is not None:
            payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if len(payload_text.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                raise ValueError(
                    f"outbox payload too large ({len(payload_text)} > {MAX_PAYLOAD_BYTES} bytes); "
                    "split into smaller events or store the heavy data on the entity"
                )
        row = WorkflowOutbox(
            event=event,
            entity_type=entity_type,
            entity_id=entity_id,
            ref_id=ref_id,
            agent_id=agent_id,
            payload_json=payload_text,
        )
        s.add(row)
        s.flush()  # populate row.id without committing
        return row

    def fetch_unpublished(self, s: Session, *, limit: int = 100) -> list[WorkflowOutbox]:
        """Return up to ``limit`` oldest-first unpublished rows.

        "Unpublished" means ``published_at IS NULL`` AND the row has
        not yet exceeded ``max_attempts``. Poison rows past the cap
        are excluded from this batch and surface in
        ``fetch_dead()`` for operator inspection.
        """
        return (
            s.query(WorkflowOutbox)
            .filter(WorkflowOutbox.published_at.is_(None))
            .filter(WorkflowOutbox.attempts < self.max_attempts)
            .order_by(WorkflowOutbox.created_at.asc(), WorkflowOutbox.id.asc())
            .limit(limit)
            .all()
        )

    def fetch_dead(self, s: Session, *, limit: int = 100) -> list[WorkflowOutbox]:
        """Return rows that have been retried more than ``max_attempts`` times."""
        return (
            s.query(WorkflowOutbox)
            .filter(WorkflowOutbox.published_at.is_(None))
            .filter(WorkflowOutbox.attempts >= self.max_attempts)
            .order_by(WorkflowOutbox.created_at.asc(), WorkflowOutbox.id.asc())
            .limit(limit)
            .all()
        )

    def mark_published(self, s: Session, row_id: int) -> None:
        row = s.get(WorkflowOutbox, row_id)
        if row is None:
            return
        row.published_at = _utc_now()
        row.last_error = None
        s.flush()
        log.debug("outbox: row %d marked published at %s", row_id, row.published_at)

    def mark_retry(self, s: Session, row_id: int, *, error: str) -> None:
        row = s.get(WorkflowOutbox, row_id)
        if row is None:
            return
        row.attempts = (row.attempts or 0) + 1
        row.last_error = error[:2000]  # cap the column to keep rows small
        s.flush()

    def mark_dead(self, s: Session, row_id: int, *, error: str) -> None:
        """Mark a row as poisoned: it stops being retried. Operator can
        see it via ``fetch_dead()`` / ``/health`` and decide to delete
        or replay manually.
        """
        row = s.get(WorkflowOutbox, row_id)
        if row is None:
            return
        row.attempts = self.max_attempts  # pin so fetch_unpublished skips it
        row.last_error = f"DEAD: {error}"[:2000]
        s.flush()

    def next_retry_delay(self, attempts: int) -> float:
        """Backoff schedule for retries (in seconds)."""
        if attempts < 0:
            attempts = 0
        idx = min(attempts, len(RETRY_BACKOFF_SECONDS) - 1)
        return RETRY_BACKOFF_SECONDS[idx]
