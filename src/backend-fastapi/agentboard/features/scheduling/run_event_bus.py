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


class RunEventSubscription:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[dict] = asyncio.Queue()


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

    Suitable for development and the current single-replica deployments.
    Replace with a broker-backed bus once multiple FastAPI replicas
    need to share live event traffic.
    """

    def __init__(self) -> None:
        self._subs: dict[int, set[RunEventSubscription]] = {}
        self._lock = threading.Lock()

    def subscribe(self, run_id: int) -> RunEventSubscription:
        subscription = RunEventSubscription(asyncio.get_running_loop())
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
        with self._lock:
            subs = list(self._subs.get(run_id, []))
        for subscription in subs:
            try:
                subscription.loop.call_soon_threadsafe(
                    subscription.queue.put_nowait, dict(event),
                )
            except RuntimeError:
                # Loop already closed — drop the subscriber.
                self.unsubscribe(run_id, subscription)
