"""In-process RunEvent pub/sub bus for AgentRun SSE streaming.

P1-7: the previous ``RunEventHub`` was a module-level singleton kept on
the FastAPI process. That works for a single replica, but breaks the
moment a second FastAPI instance is started — the worker may POST
events to replica A while a browser subscribes via replica B, and the
event never reaches the client. The proper long-term answer is a
RabbitMQ (or any other broker) backed bus, which the application
already has for proposals. To keep the door open without a big-bang
migration, the public surface now lives behind a small
``IRunEventBus`` protocol and the in-process implementation is just
one concrete adapter. The call site (``scheduling/router.py``) now
holds a ``bus`` instance that can later be swapped for an
``MqRunEventBus`` without touching the router logic.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Protocol


DEFAULT_RUN_EVENT_QUEUE_MAXSIZE = 1000
RESYNC_REQUIRED_CONTROL = "resync_required"


class RunEventSubscription:
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        queue_maxsize: int = DEFAULT_RUN_EVENT_QUEUE_MAXSIZE,
    ) -> None:
        if queue_maxsize <= 0:
            raise ValueError("queue_maxsize must be positive")
        self.loop = loop
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_maxsize)
        self._lagged = False

    def enqueue(self, event: dict[str, Any]) -> None:
        """Queue an event on the subscription's owning event loop.

        This method is only called through ``call_soon_threadsafe``. Once a
        slow consumer fills the bounded queue, replace the buffered events
        with a control item. The SSE endpoint closes on that item and the
        browser reconnects with its last delivered event id, allowing the
        durable DB log to fill the gap without unbounded memory growth.
        """
        if self._lagged:
            return
        try:
            self.queue.put_nowait(dict(event))
        except asyncio.QueueFull:
            self._lagged = True
            while True:
                try:
                    self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            self.queue.put_nowait({"_control": RESYNC_REQUIRED_CONTROL})


class IRunEventBus(Protocol):
    """Minimal contract that the SSE stream endpoint relies on.

    Implementations must guarantee ``broadcast`` is safe to call from
    any thread (it may be invoked from the request handler thread that
    persisted the event). The ``subscribe`` / ``unsubscribe`` pair
    always runs on the asyncio loop that owns the streaming response.
    """

    def subscribe(self, run_id: int) -> RunEventSubscription: ...
    def unsubscribe(self, run_id: int, subscription: RunEventSubscription) -> None: ...
    def broadcast(self, run_id: int, event: dict) -> None: ...


class InProcessRunEventBus:
    """Single-process implementation. State lives in memory; broadcasts
    are dispatched to each subscriber's loop via ``call_soon_threadsafe``.

    Concurrency contract:
    - ``_lock`` guards the ``_subs`` dictionary exclusively. It is held
      only for the duration of dict read / write, never across an
      ``asyncio`` cross-loop call. That avoids the (hypothetical)
      re-entrant deadlock a future refactor could introduce if it
      started calling ``unsubscribe`` from inside a ``with self._lock``
      block while a subscriber's loop was already closed.
    - ``broadcast`` collects "dead" subscribers during the dispatch
      phase (lock not held) and re-acquires the lock once at the end
      to evict them. The dead-set is only used to skip unsubscribes
      that have no observable effect.
    """

    def __init__(self, *, queue_maxsize: int = DEFAULT_RUN_EVENT_QUEUE_MAXSIZE) -> None:
        self._subs: dict[int, set[RunEventSubscription]] = {}
        self._lock = threading.Lock()
        self._queue_maxsize = queue_maxsize

    def subscribe(self, run_id: int) -> RunEventSubscription:
        subscription = RunEventSubscription(
            asyncio.get_running_loop(), queue_maxsize=self._queue_maxsize,
        )
        with self._lock:
            if run_id not in self._subs:
                self._subs[run_id] = set()
            self._subs[run_id].add(subscription)
        return subscription

    def unsubscribe(self, run_id: int, subscription: RunEventSubscription) -> None:
        with self._lock:
            if run_id in self._subs:
                self._subs[run_id].discard(subscription)
                if not self._subs[run_id]:
                    del self._subs[run_id]

    def broadcast(self, run_id: int, event: dict) -> None:
        # Phase 1: snapshot the subscriber set under the lock. After
        # this block, the dict is free to mutate and the snapshot is
        # stable.
        with self._lock:
            subs = list(self._subs.get(run_id, []))

        # Phase 2: dispatch to each subscriber's loop. Any RuntimeError
        # means the loop is closed and the subscriber is no longer
        # reachable; collect them so phase 3 can drop them.
        dead: list[RunEventSubscription] = []
        for subscription in subs:
            try:
                subscription.loop.call_soon_threadsafe(
                    subscription.enqueue, dict(event),
                )
            except RuntimeError:
                dead.append(subscription)

        # Phase 3: drop the dead subscribers. The lock is brief and
        # only held across dict edits, never across a call_soon.
        if dead:
            with self._lock:
                bucket = self._subs.get(run_id)
                if bucket is not None:
                    for subscription in dead:
                        bucket.discard(subscription)
                    if not bucket:
                        del self._subs[run_id]
