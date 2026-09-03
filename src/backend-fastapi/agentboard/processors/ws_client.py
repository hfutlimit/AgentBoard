"""WebSocket client that pushes local agent state to the server
cache (Phase 3 follow-up, change `agent-ephemeral-2026-09`).

This module is the worker-side counterpart to
``agent_registry_ws.py``. It opens a single WSS connection to the
server, sends a HELLO frame on connect, then keeps the connection
alive with periodic PING frames and on-demand DELTA frames
triggered by the worker portal / CLI / any local agent edit.

Design notes:

  - One connection per worker host. We don't reconnect per agent
    change — DELTA frames are cheap.
  - Reconnect: exponential backoff with jitter (1s, 2s, 4s, 8s, 16s
    capped at 30s). Dropped because a tight loop during a network
    blip would be worse than a slow ramp-up.
  - Threading: we don't use asyncio event loops here — the worker
    portal already runs in a sync FastAPI context and the WSS
    client just needs a long-lived background thread. We use the
    `websockets` library (sync client) for that. Async-ifying the
    .NET / Python workers is out of scope for this change.
  - PING cadence: 15s by default. This keeps the cache's staleness
    sweep (60s threshold) comfortable.
"""
from __future__ import annotations

import json
import logging
import queue
import random
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable

log = logging.getLogger("agentboard.processors.ws_client")


# Default cadence — override in ServerWebSocketClient(...) ctor.
DEFAULT_PING_INTERVAL_SEC = 15.0
DEFAULT_RECONNECT_BASE_SEC = 1.0
DEFAULT_RECONNECT_CAP_SEC = 30.0


@dataclass
class _Frame:
    """Internal queue item: a frame to send to the server."""
    type: str
    payload: dict[str, Any]


class ServerWebSocketClient:
    """Background-thread WSS client that mirrors ``LocalAgentRegistry``
    into the server's ``AgentRegistryCache``.

    The caller is expected to:
      1. Construct one client per process.
      2. Call ``start()`` once (non-blocking).
      3. Call ``push_hello()`` after seeding the local registry on
         startup; subsequent local edits call ``push_delta()``.
      4. Call ``stop()`` on graceful shutdown (sends BYE).
    """

    def __init__(
        self,
        server_url: str,
        token: str,
        worker_id: str,
        registry,  # LocalAgentRegistry — duck-typed; we only call .list_agents()
        *,
        ping_interval_sec: float = DEFAULT_PING_INTERVAL_SEC,
        reconnect_base_sec: float = DEFAULT_RECONNECT_BASE_SEC,
        reconnect_cap_sec: float = DEFAULT_RECONNECT_CAP_SEC,
    ):
        # server_url: "ws://host:port" or "wss://host:port"
        self._server_url = server_url.rstrip("/")
        self._token = token
        self._worker_id = worker_id
        self._registry = registry
        self._ping_interval = float(ping_interval_sec)
        self._reconnect_base = float(reconnect_base_sec)
        self._reconnect_cap = float(reconnect_cap_sec)

        self._queue: queue.Queue[_Frame] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Inflight ack waiting — populated when we send HELLO/DELTA/PING
        # and consumed by the next ACK frame. Used to know whether the
        # last frame was accepted (silent loss is bad). Most frames
        # don't care; HELLO does (we want to know applied count).
        self._last_ack: dict[str, Any] | None = None
        self._last_ack_event = threading.Event()

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"agent-registry-ws[{self._worker_id}]",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        # Send a graceful BYE; the loop picks it up and exits.
        self._enqueue(_Frame("BYE", {}))
        self._stop.set()
        self._thread.join(timeout=10)
        self._thread = None

    def enqueue_delta(
        self,
        add_or_update: Iterable[dict[str, Any]] = (),
        remove: Iterable[str] = (),
    ) -> None:
        """Trigger a DELTA push. Called by the worker portal after
        ``LocalAgentRegistry.upsert()`` / ``.delete()``."""
        adds = list(add_or_update)
        rems = list(remove)
        if not adds and not rems:
            return
        self._enqueue(_Frame("DELTA", {
            "add_or_update": adds, "remove": rems,
        }))

    def enqueue_hello(self) -> None:
        """Force a HELLO push (e.g. on startup, or after a re-seed)."""
        self._enqueue(_Frame("HELLO", {}))

    # ---------- internal ----------

    def _enqueue(self, frame: _Frame) -> None:
        try:
            self._queue.put_nowait(frame)
        except queue.Full:  # pragma: no cover
            log.warning("ws_client queue full; dropping %s", frame.type)

    def _next_outbound(self) -> _Frame | None:
        """Block for a frame, but wake up to PING / stop / reconnect
        events. We use a small timeout and re-check the stop event
        so the thread can exit cleanly.
        """
        try:
            return self._queue.get(timeout=self._ping_interval)
        except queue.Empty:
            return None  # caller decides whether to PING

    def _run_loop(self) -> None:
        """Main loop. Reconnect with exponential backoff until stop()
        is set. Each successful connection: drain the queue,
        send PING every ping_interval, and process incoming ACK/NACK
        frames.
        """
        attempt = 0
        while not self._stop.is_set():
            try:
                self._connect_and_serve()
                # Clean exit (stop() set) — exit loop.
                if self._stop.is_set():
                    break
                attempt = 0  # successful long-lived connection resets
            except Exception as e:
                attempt += 1
                backoff = min(
                    self._reconnect_cap,
                    self._reconnect_base * (2 ** min(attempt, 6)),
                ) * (0.8 + 0.4 * random.random())  # ±20% jitter
                log.warning(
                    "ws_client: connection error (attempt %d): %s. "
                    "Reconnecting in %.1fs", attempt, e, backoff,
                )
                if self._stop.wait(timeout=backoff):
                    break
        log.info("ws_client: loop exit for worker_id=%s", self._worker_id)

    def _connect_and_serve(self) -> None:
        # Lazy import — websockets may not be installed in every
        # deployment (the processor_portal only needs it when
        # AGENTBOARD_EPHEMERAL_AGENTS=1).
        try:
            from websockets.sync.client import connect as _ws_connect
        except ImportError as e:
            raise RuntimeError(
                "ServerWebSocketClient needs the 'websockets' package; "
                "pip install websockets"
            ) from e

        url = f"{self._server_url}/api/agents/ws?token={self._token}"
        # The connection blocks until the server accepts the
        # upgrade. We open with a short ping_interval so dead
        # connections are detected fast.
        log.info("ws_client: connecting to %s", self._server_url)
        ws = _ws_connect(
            url, ping_interval=20, ping_timeout=20, close_timeout=5,
        )
        try:
            # Greet the server with HELLO on every connect.
            self._send_hello(ws)
            self._read_ack(ws, expected_for="HELLO")
            # Main loop: drain queue + heartbeat PING.
            last_ping_at = time.time()
            while not self._stop.is_set():
                frame = self._next_outbound()
                if frame is not None:
                    self._send(ws, frame)
                    if frame.type == "BYE":
                        # Graceful close requested.
                        break
                    self._read_ack(ws, expected_for=frame.type)
                    last_ping_at = time.time()
                    continue
                # No frame in queue for ping_interval seconds — send PING.
                if (time.time() - last_ping_at) >= self._ping_interval:
                    self._send_ping(ws)
                    last_ping_at = time.time()
        finally:
            try:
                ws.close()
            except Exception:  # pragma: no cover
                pass

    # ---------- send/recv helpers ----------

    def _send(self, ws, frame: _Frame) -> None:
        ws.send(json.dumps({
            "type": frame.type,
            **({"worker_id": self._worker_id} if frame.type == "HELLO" else {}),
            **frame.payload,
        }))

    def _send_hello(self, ws) -> None:
        agents = [a.to_frame() for a in self._registry.list_agents()]
        log.info("ws_client: HELLO %d agents", len(agents))
        ws.send(json.dumps({
            "type": "HELLO",
            "worker_id": self._worker_id,
            "agents": agents,
        }))

    def _send_ping(self, ws) -> None:
        agent_ids = [a.agent_id for a in self._registry.list_agents()]
        log.debug("ws_client: PING %d agents", len(agent_ids))
        ws.send(json.dumps({
            "type": "PING",
            "agent_ids": agent_ids,
        }))

    def _read_ack(self, ws, *, expected_for: str) -> None:
        """Read one server frame. We don't strictly need to consume
        the ack for every frame, but the server's loop is request-
        response shaped; if we don't read, the buffer fills up.

        We log NACK as a warning so the operator notices, and we
        store the ack so callers that want detailed stats can grab
        it (e.g. ``client.last_ack``). For HELLO, callers can also
        wait on ``last_ack_event``.
        """
        try:
            raw = ws.recv(timeout=10)
        except Exception as e:
            log.warning("ws_client: recv timeout/error on %s ack: %s",
                        expected_for, e)
            return
        try:
            ack = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("ws_client: non-JSON ack on %s: %r", expected_for, raw[:200])
            return
        if isinstance(ack, dict) and ack.get("type") == "NACK":
            log.warning("ws_client: NACK for %s: %s",
                        expected_for, ack.get("error"))
        self._last_ack = ack
        self._last_ack_event.set()

    @property
    def last_ack(self) -> dict[str, Any] | None:
        return self._last_ack


def default_worker_id() -> str:
    """The worker_id is conventionally ``<hostname>`` for the local
    worker. We don't want to special-case this in the portal; the
    portal can pass its own worker_id.
    """
    return socket.gethostname()
