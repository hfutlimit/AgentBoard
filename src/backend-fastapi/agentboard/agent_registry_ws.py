"""WebSocket endpoint for worker → server agent registry push
(Phase 3, change `agent-ephemeral-2026-09`).

This module is the server-side contract for the worker's
WebSocket client (P3). It does not itself initiate any network
I/O — FastAPI's WebSocket route is invoked by the ASGI server
when a worker opens a connection to ``ws://host/api/agents/ws``.

Wire protocol (JSON frames, all fields required unless noted):

  client → server (initial):
    {"type": "HELLO",
     "worker_id": "TF-JASONZHONG",
     "agents": [
        {"agent_id": "hy4-agent", "cli_command": "codebuddy -p -y --model hy4-preview",
         "model": "hy4-preview", "enabled": true, "online": true,
         "roles": ["developer", "reviewer"]},
        ...]}

  client → server (incremental):
    {"type": "DELTA",
     "add_or_update": [...same shape as HELLO agents...],
     "remove": ["agent_id_a", "agent_id_b"]}

  client → server (liveness, every 15s by convention):
    {"type": "PING", "agent_ids": ["a", "b"]}

  client → server (graceful close — server drops the worker from cache):
    {"type": "BYE"}

  server → client (ack):
    {"type": "ACK",
     "for": "HELLO" | "DELTA" | "PING",
     "applied": int,        # HELLO
     "added": int, "removed": int,  # DELTA
     "touched": int,       # PING
     "worker_id": "..."     # echo on HELLO so client can verify
    }

  server → client (error):
    {"type": "NACK", "for": <frame type>, "error": "..."}

Auth: token in ``?token=abk_...`` query param (Authorization
header is awkward for WSS upgrade). Auth is checked on accept;
unauthorized clients get 1008 close.

Disconnection: server drops the worker's cache entries on any
close (clean BYE, abnormal close, network drop). Workers reconnect
with HELLO on the next event loop tick.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Header, Query, WebSocket, WebSocketDisconnect

from .agent_registry_cache import (
    ephemeral_agents_enabled,
    get_default_cache,
)
from .api_helpers import _caller_uid_admin, _auth_is_required

log = logging.getLogger("agentboard.agent_registry_ws")

router = APIRouter()


# Cap on a single frame's payload. Frames larger than this are
# rejected (server logs and closes). Workers with > 4 MB of agent
# state are doing something wrong; a sane per-machine config is
# tens to low hundreds of entries (~20 KB total).
MAX_FRAME_BYTES = 4 * 1024 * 1024


@router.websocket("/api/agents/ws")
async def agent_registry_websocket(
    websocket: WebSocket,
    token: str = Query(..., min_length=1, max_length=512,
                       description="Worker API key (abk_...) used for auth"),
):
    """WebSocket entry point for worker → server agent registry push.

    State machine:

      connect (with ?token=) → accept or close 1008
        ↓
      HELLO (worker_id, agents[])                → apply_hello + ACK
        ↓
      DELTA (add_or_update[], remove[])           → apply_delta + ACK
        ↓
      PING  (agent_ids[])                          → record_ping + ACK
        ↓
      BYE  (no body)                               → drop_worker + close
        ↓
      (any close / disconnect)                     → drop_worker

    The ``worker_id`` is locked in by the first HELLO frame and
    used for all subsequent DELTA / PING / BYE frames. DELTA
    before HELLO returns NACK.
    """
    # 1. Auth — token in query param.
    uid, _ = _caller_uid_admin(f"Bearer {token}", s=None)
    if _auth_is_required() and not uid:
        # Production / staging: require a valid credential.
        await websocket.close(code=1008, reason="unauthorized")
        return
    # Dev mode (AGENTBOARD_REQUIRE_AUTH not set): accept any token
    # (including missing) so local workers can register without
    # provisioning credentials first.

    # 2. Feature flag.
    if not ephemeral_agents_enabled():
        await websocket.close(code=1008, reason="ephemeral_agents_disabled")
        return

    # 3. Accept.
    await websocket.accept()
    cache = get_default_cache()
    worker_id: str | None = None

    try:
        while True:
            # Receive raw text, then parse. We don't use
            # receive_json() because we want to enforce the
            # size cap before the JSON parser gets it.
            raw = await websocket.receive_text()
            if len(raw) > MAX_FRAME_BYTES:
                await websocket.send_json({
                    "type": "NACK", "for": "?",
                    "error": f"frame too large: {len(raw)} > {MAX_FRAME_BYTES}",
                })
                continue
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError as e:
                await websocket.send_json({
                    "type": "NACK", "for": "?", "error": f"invalid json: {e}",
                })
                continue
            if not isinstance(frame, dict):
                await websocket.send_json({
                    "type": "NACK", "for": "?",
                    "error": "frame must be a JSON object",
                })
                continue
            kind = frame.get("type")
            ack = await _handle_frame(kind, frame, cache, worker_id)
            if "worker_id" in ack and ack.get("type") == "ACK" and kind == "HELLO":
                worker_id = ack["worker_id"]
            await websocket.send_json(ack)
            if kind == "BYE":
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:  # last-resort guard; never let the loop die
        log.exception("agent_registry_ws: unhandled error in loop: %s", e)
    finally:
        if worker_id is not None:
            dropped = cache.drop_worker(worker_id)
            if dropped:
                log.info("agent_registry_ws: dropped worker %s on close (%d entries)",
                         worker_id, dropped)


async def _handle_frame(
    kind: str | None,
    frame: dict[str, Any],
    cache,
    worker_id: str | None,
) -> dict[str, Any]:
    """Dispatch one frame to the cache. Returns the ack/nack payload
    that the caller sends back to the worker. Pure function
    (modulo cache mutation) — easy to unit-test in isolation.
    """
    if kind is None:
        return {"type": "NACK", "for": "?", "error": "missing 'type' field"}

    if kind == "HELLO":
        wid = _require_str(frame, "worker_id")
        if not wid:
            return {"type": "NACK", "for": "HELLO", "error": "missing worker_id"}
        agents = frame.get("agents", [])
        if not isinstance(agents, list):
            return {"type": "NACK", "for": "HELLO",
                    "error": "'agents' must be a list"}
        applied = cache.apply_hello(wid, agents)
        return {"type": "ACK", "for": "HELLO", "applied": applied,
                "worker_id": wid}

    if kind == "DELTA":
        if not worker_id:
            return {"type": "NACK", "for": "DELTA",
                    "error": "DELTA before HELLO (no worker_id bound)"}
        adds = frame.get("add_or_update", [])
        removes = frame.get("remove", [])
        if not isinstance(adds, list):
            return {"type": "NACK", "for": "DELTA",
                    "error": "'add_or_update' must be a list"}
        if not isinstance(removes, list):
            return {"type": "NACK", "for": "DELTA",
                    "error": "'remove' must be a list"}
        added, removed = cache.apply_delta(
            worker_id, add_or_update=adds, remove=removes,
        )
        return {"type": "ACK", "for": "DELTA",
                "added": added, "removed": removed}

    if kind == "PING":
        if not worker_id:
            return {"type": "NACK", "for": "PING",
                    "error": "PING before HELLO (no worker_id bound)"}
        ids = frame.get("agent_ids", [])
        if not isinstance(ids, list):
            return {"type": "NACK", "for": "PING",
                    "error": "'agent_ids' must be a list"}
        touched = cache.record_ping(worker_id, ids)
        return {"type": "ACK", "for": "PING", "touched": touched}

    if kind == "BYE":
        if not worker_id:
            return {"type": "ACK", "for": "BYE", "touched": 0}
        dropped = cache.drop_worker(worker_id)
        return {"type": "ACK", "for": "BYE", "touched": dropped}

    return {"type": "NACK", "for": str(kind),
            "error": f"unknown frame type: {kind}"}


def _require_str(frame: dict[str, Any], key: str) -> str | None:
    val = frame.get(key)
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None
