"""OutboxPublisher: background task that drains ``workflow_outbox``.

Lives as a FastAPI ``lifespan`` background task (see ``api.py``).
Spawned in ``lifespan`` startup, cancelled in shutdown. The
publisher is intentionally simple:

* scan ``workflow_outbox`` for ``published_at IS NULL`` rows
  (oldest first, bounded by ``max_attempts``)
* call ``publish_workflow_event(...)`` for each row
* on success: ``mark_published``
* on transient failure (MQ unreachable, broker timeout, etc.):
  ``mark_retry`` — row stays in the unpublished batch
* on permanent failure (poison message, JSON shape, etc.):
  ``mark_dead`` — row is removed from the live batch and surfaced
  via ``/health`` for the operator to deal with

We **never** raise out of the loop on a single-row failure. A
poison row is a poison row, not a reason to stop draining the queue.

Concurrency
-----------
A single FastAPI process owns one publisher loop. If you scale to
multiple API processes, the ``fetch_unpublished(limit=N)`` query
must include a per-process claim window (``SELECT ... FOR UPDATE
SKIP LOCKED`` on MariaDB / SQLite ≥ 3.36) so two publishers do not
double-drain the same rows. For the current single-process
deployment this is not needed; the SQL is conservative and the
loop is idempotent (publish is best-effort, consumers re-read
state from the DB). When multi-process scaling lands, switch the
``fetch_unpublished`` query to a claim window — see comment in
``OutboxRepository.fetch_unpublished``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from ...core.common.models import utc_now
from ...core.infrastructure import messaging as mq
from ...core.infrastructure import database as _db
from ...core.infrastructure.outbox import (
    OutboxRepository,
    WorkflowOutbox,
)

log = logging.getLogger("agentboard.core.infrastructure.outbox_publisher")

# Tuning knobs (small on purpose — over-tuning hides real bugs).
BATCH_SIZE = 100                  # rows per scan
IDLE_SLEEP_SECONDS = 0.5          # when the table is empty
TICK_SECONDS = 0.2                # main loop tick


class OutboxPublisher:
    """Single-process publisher loop.

    Public surface is just ``start()`` / ``stop()``. The lifespan
    hook in ``api.py`` calls them; tests can construct a publisher
    directly to drive a few ticks.
    """

    def __init__(self, *, batch_size: int = BATCH_SIZE,
                 idle_sleep: float = IDLE_SLEEP_SECONDS) -> None:
        self._batch = batch_size
        self._idle_sleep = idle_sleep
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._repo = OutboxRepository()

    # ---- lifecycle --------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Spawn the publisher task on the given event loop."""
        if self._task is not None:
            return
        self._stop_event = asyncio.Event()
        # ``asyncio.run_coroutine_threadsafe`` would be the right call
        # if the API were multi-threaded; with uvicorn's default
        # single-process asyncio loop we can use create_task directly.
        self._task = loop.create_task(self._run(), name="outbox-publisher")

    async def stop(self) -> None:
        if self._task is None:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            log.warning("outbox publisher did not stop in 5s; cancelling")
            self._task.cancel()
        self._task = None
        self._stop_event = None

    # ---- main loop --------------------------------------------------------

    async def _run(self) -> None:
        assert self._stop_event is not None
        log.info("outbox publisher started (batch=%d, idle_sleep=%.2fs)",
                 self._batch, self._idle_sleep)
        # Yield once so the FastAPI startup event can complete before
        # we start hammering the DB.
        await asyncio.sleep(0)
        while not self._stop_event.is_set():
            try:
                drained = await asyncio.to_thread(self._drain_once)
            except Exception as e:  # pragma: no cover - defensive
                # We do not want a programmer error in the loop to
                # silently kill the publisher. Log loudly and back off.
                log.exception("outbox publisher tick raised: %s", e)
                drained = 0
            if drained == 0:
                # Empty batch: idle. ``stop_event.wait`` lets us wake
                # up immediately on shutdown.
                try:
                    await asyncio.wait_for(self._stop_event.wait(),
                                           timeout=self._idle_sleep)
                except asyncio.TimeoutError:
                    pass
            else:
                # Drained at least one row: yield to the event loop so
                # we don't hog a thread, then tick again.
                await asyncio.sleep(TICK_SECONDS)

    def _drain_once(self) -> int:
        """Drain one batch. Runs in a thread (via ``to_thread``)."""
        from ...features.scheduling.worker_work_relay import drain_once
        try:
            drain_once()
        except Exception:
            log.exception("worker-owned relay deferred; continuing legacy event outbox")
        # Each tick gets its own session. This is intentional: the
        # main session is owned by the FastAPI request thread and we
        # never want to share it with a background thread.
        #
        # We use ``_db.SessionLocal()`` rather than constructing a
        # fresh sessionmaker per tick because the test suite mutates
        # ``_db.SessionLocal`` itself (``monkeypatch.setattr`` in
        # ``tests/test_workflow_outbox.py``) to redirect to an
        # in-memory engine. The production lifetime of the publisher
        # is bounded by the FastAPI lifespan so the import-time
        # ``SessionLocal`` reference is stable in production. See
        # commit ``e1d46f2`` for the SSE-stream equivalent and why
        # per-tick sessionmaker construction was *not* chosen here.
        s = _db.SessionLocal()
        try:
            rows = self._repo.fetch_unpublished(s, limit=self._batch)
            if not rows:
                return 0
            for row in rows:
                if self._publish_one(s, row):
                    log.debug("outbox: row %d drained successfully", row.id)
                    self._repo.mark_published(s, row.id)
                # on failure ``_publish_one`` already updated the row
                # (mark_retry / mark_dead) so we do nothing here.
            s.commit()
            return len(rows)
        except Exception as e:  # pragma: no cover - defensive
            log.exception("outbox batch drain raised: %s", e)
            s.rollback()
            return 0
        finally:
            s.close()

    def _publish_one(self, s: Session, row: WorkflowOutbox) -> bool:
        """Try to publish a single outbox row. Returns True on success."""
        try:
            ok = mq.publish_workflow_event(
                row.event,
                row.entity_type,
                row.entity_id,
                ref_id=row.ref_id,
                agent_id=row.agent_id,
            )
            log.debug("outbox: row %d publish_workflow_event returned %s", row.id, ok)
        except Exception as e:
            # ``publish_workflow_event`` is documented to swallow all
            # exceptions and return False, but a future refactor
            # could let one through. Be defensive: treat as transient.
            self._repo.mark_retry(s, row.id, error=f"unexpected: {e}")
            return False

        if ok:
            return True

        # ``publish_workflow_event`` returned False. Decide whether
        # to retry (transient) or declare dead (poison). We use
        # ``attempts`` as the discriminator: once the backoff
        # schedule has been exhausted (8 attempts in the default
        # schedule) the row is "permanently failing" and we mark
        # it dead so the live batch skips it and operators can see
        # the count on ``/health``.
        attempts = (row.attempts or 0) + 1
        if attempts >= self._repo.max_attempts:
            self._repo.mark_dead(s, row.id, error="publish returned False (max attempts reached)")
            log.error("outbox row %d (event=%s entity=%s/%s) marked dead after %d failed publishes",
                      row.id, row.event, row.entity_type, row.entity_id, attempts)
        else:
            self._repo.mark_retry(s, row.id, error="publish returned False")
            log.warning("outbox row %d (event=%s entity=%s/%s) retry %d/%d",
                        row.id, row.event, row.entity_type, row.entity_id,
                        attempts, self._repo.max_attempts)
        return False


# ---- synchronous helper for tests / ad-hoc drains ----------------------


def drain_once_blocking() -> dict[str, Any]:
    """One synchronous drain pass. Returns counters for tests/CLI.

    Behavior mirrors ``OutboxPublisher._drain_once``: on a
    successful publish, the row is marked published *in the same
    session* before commit, so a successful drain guarantees
    ``fetch_unpublished`` will not return those rows on the next
    pass. Tests and CLI tools use this to drive a deterministic
    drain without spawning the background loop.

    Uses ``_db.SessionLocal()`` so callers (and tests via
    ``monkeypatch.setattr``) can redirect the session factory
    without touching the publisher. The production lifetime of
    ``SessionLocal`` is bounded by FastAPI's lifespan so an
    import-time snapshot is acceptable here. Multi-process scaling
    must revisit this — see ``OutboxPublisher`` docstring.
    """
    s = _db.SessionLocal()
    try:
        publisher = OutboxPublisher()
        rows = publisher._repo.fetch_unpublished(s, limit=BATCH_SIZE)
        if not rows:
            return {"drained": 0, "failed": 0, "dead": 0}
        drained = 0
        failed = 0
        for row in rows:
            ok = publisher._publish_one(s, row)
            if ok:
                publisher._repo.mark_published(s, row.id)
                drained += 1
            else:
                failed += 1
        s.commit()
        return {"drained": drained, "failed": failed, "dead": 0}
    finally:
        s.close()
