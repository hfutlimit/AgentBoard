"""Tests for the Phase 3 WebSocket endpoint (agent-ephemeral-2026-09).

The WebSocket handler is exercised through the real
``agentboard.api:app`` (TestClient) so we hit the real route +
``_handle_frame`` + cache. The auth check uses a token format that
``_caller_uid_admin`` accepts without a real DB lookup. We rely on
``_auth_is_required()`` returning False in dev mode (no auth env set)
to keep the test simple; the production auth path is exercised in
integration tests.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agentboard.agent_registry_cache import (
    ephemeral_agents_enabled,
    get_default_cache,
    reset_default_cache,
)


# ---------- helpers ----------

def _hello_frame(worker_id: str, agents: list[dict]) -> dict:
    return {"type": "HELLO", "worker_id": worker_id, "agents": agents}


def _agent_frame(agent_id: str, **overrides) -> dict:
    base = {
        "agent_id": agent_id,
        "cli_command": f"codebuddy -p --model {agent_id}",
        "model": agent_id,
        "enabled": True,
        "online": True,
        "roles": ["developer"],
    }
    base.update(overrides)
    return base


def _await(coro):
    """Drive an async coroutine from sync test code."""
    return asyncio.get_event_loop().run_until_complete(coro) \
        if False else asyncio.run(coro)


# ---------- frame dispatcher (no socket) ----------

class TestFrameDispatcher:
    """Pure-function tests for ``_handle_frame`` — no FastAPI."""

    def test_hello_returns_ack_with_applied_count(self):
        from agentboard.agent_registry_ws import _handle_frame
        reset_default_cache()
        try:
            cache = get_default_cache()
            ack = _await(_handle_frame("HELLO", _hello_frame("W1", [
                _agent_frame("a"), _agent_frame("b"),
            ]), cache, None))
            assert ack["type"] == "ACK"
            assert ack["for"] == "HELLO"
            assert ack["applied"] == 2
            assert ack["worker_id"] == "W1"
        finally:
            reset_default_cache()

    def test_delta_before_hello_returns_nack(self):
        from agentboard.agent_registry_ws import _handle_frame
        reset_default_cache()
        try:
            cache = get_default_cache()
            ack = _await(_handle_frame("DELTA", {
                "type": "DELTA", "add_or_update": [_agent_frame("a")],
            }, cache, worker_id=None))
            assert ack["type"] == "NACK"
            assert "before HELLO" in ack["error"]
        finally:
            reset_default_cache()

    def test_delta_after_hello_applies(self):
        from agentboard.agent_registry_ws import _handle_frame
        reset_default_cache()
        try:
            cache = get_default_cache()
            _await(_handle_frame("HELLO", _hello_frame("W1", [_agent_frame("a")]),
                                 cache, None))
            ack = _await(_handle_frame("DELTA", {
                "type": "DELTA",
                "add_or_update": [_agent_frame("b")],
                "remove": ["a"],
            }, cache, worker_id="W1"))
            assert ack["type"] == "ACK"
            assert ack["added"] == 1
            assert ack["removed"] == 1
        finally:
            reset_default_cache()

    def test_ping_refreshes_heartbeat(self):
        from agentboard.agent_registry_ws import _handle_frame
        reset_default_cache()
        try:
            cache = get_default_cache()
            _await(_handle_frame("HELLO", _hello_frame("W1", [_agent_frame("a")]),
                                 cache, None))
            # Backdate a
            cache.get("W1", "a").last_heartbeat = 0.0
            ack = _await(_handle_frame("PING",
                                       {"type": "PING", "agent_ids": ["a"]},
                                       cache, worker_id="W1"))
            assert ack["type"] == "ACK"
            assert ack["touched"] == 1
            assert cache.get("W1", "a").last_heartbeat > 1000  # back to now
        finally:
            reset_default_cache()

    def test_bye_drops_worker(self):
        from agentboard.agent_registry_ws import _handle_frame
        reset_default_cache()
        try:
            cache = get_default_cache()
            _await(_handle_frame("HELLO", _hello_frame("W1",
                                 [_agent_frame("a"), _agent_frame("b")]),
                                 cache, None))
            ack = _await(_handle_frame("BYE", {"type": "BYE"},
                                       cache, worker_id="W1"))
            assert ack["type"] == "ACK"
            assert ack["touched"] == 2
            assert len(cache) == 0
        finally:
            reset_default_cache()

    def test_unknown_type_returns_nack(self):
        from agentboard.agent_registry_ws import _handle_frame
        reset_default_cache()
        try:
            cache = get_default_cache()
            ack = _await(_handle_frame("FOO", {"type": "FOO"}, cache, worker_id="W1"))
            assert ack["type"] == "NACK"
            assert "unknown" in ack["error"]
        finally:
            reset_default_cache()

    def test_malformed_hello_missing_worker_id(self):
        from agentboard.agent_registry_ws import _handle_frame
        reset_default_cache()
        try:
            cache = get_default_cache()
            ack = _await(_handle_frame("HELLO", {"type": "HELLO", "agents": []},
                                       cache, None))
            assert ack["type"] == "NACK"
            assert "worker_id" in ack["error"]
        finally:
            reset_default_cache()


# ---------- WebSocket end-to-end ----------

@pytest.fixture
def app(monkeypatch):
    """Use the full AgentBoard FastAPI app. Set the feature flag so
    the WSS endpoint accepts the connection. Auth is not required in
    dev mode (no AGENTBOARD_REQUIRE_AUTH=1), so the empty Bearer
    token resolves to (None, False) and the handler accepts.
    """
    monkeypatch.setenv("AGENTBOARD_EPHEMERAL_AGENTS", "1")
    from agentboard.api import app
    return app


class TestWebSocketEndToEnd:
    def setup_method(self):
        reset_default_cache()

    def teardown_method(self):
        reset_default_cache()

    def test_hello_then_ping_then_bye(self, app):
        from fastapi.testclient import TestClient
        client = TestClient(app)  # no `with` — skips lifespan/alembic
        with client.websocket_connect(
            "/api/agents/ws?token=test-token"
        ) as ws:
            ws.send_text(json.dumps(_hello_frame("W-WS1", [
                _agent_frame("a"), _agent_frame("b"),
            ])))
            ack = ws.receive_json()
            assert ack["type"] == "ACK"
            assert ack["for"] == "HELLO"
            assert ack["applied"] == 2
            assert ack["worker_id"] == "W-WS1"

            ws.send_text(json.dumps({
                "type": "PING", "agent_ids": ["a", "b"],
            }))
            ack = ws.receive_json()
            assert ack["type"] == "ACK"
            assert ack["for"] == "PING"
            assert ack["touched"] == 2

            ws.send_text(json.dumps({"type": "BYE"}))
            ack = ws.receive_json()
            assert ack["type"] == "ACK"
            assert ack["for"] == "BYE"
            assert ack["touched"] == 2

        # After BYE the cache should be empty.
        assert len(get_default_cache()) == 0

    def test_delta_after_hello(self, app):
        from fastapi.testclient import TestClient
        client = TestClient(app)
        with client.websocket_connect(
            "/api/agents/ws?token=test-token"
        ) as ws:
            ws.send_text(json.dumps(_hello_frame("W-WS2", [
                _agent_frame("a"),
            ])))
            ack = ws.receive_json()
            assert ack["applied"] == 1

            ws.send_text(json.dumps({
                "type": "DELTA",
                "add_or_update": [_agent_frame("b", model="m-b")],
                "remove": ["a"],
            }))
            ack = ws.receive_json()
            assert ack["type"] == "ACK"
            assert ack["for"] == "DELTA"
            assert ack["added"] == 1
            assert ack["removed"] == 1

            # a gone, b present
            cache = get_default_cache()
            assert cache.get("W-WS2", "a") is None
            assert cache.get("W-WS2", "b") is not None
            assert cache.get("W-WS2", "b").model == "m-b"

    def test_malformed_frame_returns_nack_then_continues(self, app):
        from fastapi.testclient import TestClient
        client = TestClient(app)
        with client.websocket_connect(
            "/api/agents/ws?token=test-token"
        ) as ws:
            ws.send_text("not-json")
            ack = ws.receive_json()
            assert ack["type"] == "NACK"
            assert "json" in ack["error"]

            # Send a valid frame; handler should keep going.
            ws.send_text(json.dumps(_hello_frame("W-WS3",
                                                [_agent_frame("a")])))
            ack = ws.receive_json()
            assert ack["type"] == "ACK"
            assert ack["applied"] == 1

    def test_oversized_frame_rejected(self, app):
        from fastapi.testclient import TestClient
        # 4 MB cap. Make a frame just over it.
        big_payload = "x" * (5 * 1024 * 1024)
        client = TestClient(app)
        with client.websocket_connect(
            "/api/agents/ws?token=test-token"
        ) as ws:
            ws.send_text(big_payload)
            ack = ws.receive_json()
            assert ack["type"] == "NACK"
            assert "too large" in ack["error"]

    def test_worker_dropped_on_abnormal_close(self, app):
        from fastapi.testclient import TestClient
        client = TestClient(app)
        with client.websocket_connect(
            "/api/agents/ws?token=test-token"
        ) as ws:
            ws.send_text(json.dumps(_hello_frame("W-WS4", [
                _agent_frame("a"),
            ])))
            ack = ws.receive_json()
            assert ack["applied"] == 1
            # Exit the with block -> abnormal close
        # Server should have dropped the worker
        assert get_default_cache().get("W-WS4", "a") is None


# ---------- flag-off behavior ----------

class TestFlagOff:
    def test_flag_off_rejects_connection_at_upgrade(self, monkeypatch):
        """When the feature flag is off, the server closes the
        WebSocket at the upgrade with code 1008 — no frames are
        processed. This prevents workers from talking to a server
        that isn't paying attention to the cache."""
        monkeypatch.delenv("AGENTBOARD_EPHEMERAL_AGENTS", raising=False)
        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect
        from agentboard.api import app
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                "/api/agents/ws?token=test-token"
            ):
                pass
        # Server's close reason is the second positional arg to
        # WebSocketDisconnect (code=..., reason=...). Starlette's
        # TestClient surfaces it via the exception's args / reason
        # attribute depending on version. We accept either.
        e = exc_info.value
        code = getattr(e, "code", None) or (e.args[0] if e.args else None)
        reason = (getattr(e, "reason", None)
                  or (e.args[1] if len(e.args) > 1 else None)
                  or "")
        assert code == 1008, f"expected 1008, got code={code} args={e.args!r}"
        assert "ephemeral" in reason.lower() or "disabled" in reason.lower()
