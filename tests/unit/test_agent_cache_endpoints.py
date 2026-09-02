"""Tests for the Phase 2 dispatch endpoints (agent-ephemeral-2026-09).

The P2 endpoints read the in-memory AgentRegistryCache and return
503 Retry-After: 30 when the cache has no eligible agent. They are
flag-gated on AGENTBOARD_EPHEMERAL_AGENTS=1.

These tests use FastAPI's TestClient to exercise the actual route
handler in the scheduling router. We isolate the cache module-level
singleton between tests via ``reset_default_cache`` and env-flag
fiddling via ``monkeypatch``.
"""
from __future__ import annotations

import os

import pytest

# Importing the router module pulls in the FastAPI app. We do not
# import the global FastAPI app object here — we only need the
# router functions to be importable for the test runner. That
# avoids triggering the lifespan / DB at import time.
from agentboard.agent_registry_cache import (
    AgentRegistryCache,
    get_default_cache,
    reset_default_cache,
)


# ---------- TestClient bootstrap ----------

@pytest.fixture
def app(monkeypatch):
    """Use the full AgentBoard FastAPI app. Building a tiny app with
    only the P2 routes triggers a circular import
    (``api.agent_state_hub`` <-> ``features.scheduling.router``), so
    we exercise the routes through the real app object. The DB
    lifespan would try to run alembic against a broken migration
    state on this working tree, so we mount only the routes we
    need via ``dependency_overrides``-free routing (the real app
    is built once at import time; we just don't fire its startup
    event because the test client uses ``with TestClient`` which
    invokes lifespan by default — we skip the lifespan via
    ``raise_server_exceptions=False`` and a no-op startup).
    """
    from agentboard.api import app
    return app


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- /api/agent-cache/pick ----------

class TestPickEndpoint:
    def setup_method(self):
        reset_default_cache()

    def teardown_method(self):
        reset_default_cache()

    def test_flag_off_returns_503_with_retry_after(
        self, app, monkeypatch
    ):
        monkeypatch.delenv("AGENTBOARD_EPHEMERAL_AGENTS", raising=False)
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post("/api/agent-cache/pick", headers=_bearer("x"))
        assert r.status_code == 503
        assert r.headers.get("Retry-After") == "30"
        assert "AGENTBOARD_EPHEMERAL_AGENTS=1" in r.json()["detail"]

    def test_cache_empty_returns_503(self, app, monkeypatch):
        monkeypatch.setenv("AGENTBOARD_EPHEMERAL_AGENTS", "1")
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post("/api/agent-cache/pick", headers=_bearer("x"))
        assert r.status_code == 503
        assert r.headers.get("Retry-After") == "30"
        assert "no agent available" in r.json()["detail"]

    def test_cache_hit_returns_picked_pair(self, app, monkeypatch):
        monkeypatch.setenv("AGENTBOARD_EPHEMERAL_AGENTS", "1")
        cache = get_default_cache()
        cache.apply_hello("W1", [{
            "agent_id": "hy4-agent",
            "cli_command": "codebuddy -p -y --model hy4-preview",
            "model": "hy4-preview",
            "enabled": True,
            "online": True,
        }])
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post("/api/agent-cache/pick", headers=_bearer("x"))
        assert r.status_code == 200
        body = r.json()
        assert body["worker_id"] == "W1"
        assert body["agent_id"] == "hy4-agent"
        assert body["pinned"] is False

    def test_pinned_returns_requested_when_in_cache(self, app, monkeypatch):
        monkeypatch.setenv("AGENTBOARD_EPHEMERAL_AGENTS", "1")
        cache = get_default_cache()
        cache.apply_hello("W1", [{"agent_id": "a", "model": "m", "enabled": True}])
        cache.apply_hello("W2", [{"agent_id": "b", "model": "m", "enabled": True}])
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post(
            "/api/agent-cache/pick?pinned=b", headers=_bearer("x")
        )
        assert r.status_code == 200
        body = r.json()
        assert body["worker_id"] == "W2"
        assert body["agent_id"] == "b"
        assert body["pinned"] is True

    def test_pinned_missing_returns_503(self, app, monkeypatch):
        monkeypatch.setenv("AGENTBOARD_EPHEMERAL_AGENTS", "1")
        cache = get_default_cache()
        cache.apply_hello("W1", [{"agent_id": "a", "model": "m", "enabled": True}])
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post(
            "/api/agent-cache/pick?pinned=nope", headers=_bearer("x")
        )
        assert r.status_code == 503
        assert r.headers.get("Retry-After") == "30"


# ---------- /api/agent-cache/snapshot ----------

class TestSnapshotEndpoint:
    def setup_method(self):
        reset_default_cache()

    def teardown_method(self):
        reset_default_cache()

    def test_flag_off_returns_503(self, app, monkeypatch):
        monkeypatch.delenv("AGENTBOARD_EPHEMERAL_AGENTS", raising=False)
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/agent-cache/snapshot", headers=_bearer("x"))
        assert r.status_code == 503

    def test_empty_cache_returns_zero_count(self, app, monkeypatch):
        monkeypatch.setenv("AGENTBOARD_EPHEMERAL_AGENTS", "1")
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/agent-cache/snapshot", headers=_bearer("x"))
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["agents"] == []

    def test_populated_cache_returns_listed_agents(self, app, monkeypatch):
        monkeypatch.setenv("AGENTBOARD_EPHEMERAL_AGENTS", "1")
        cache = get_default_cache()
        cache.apply_hello("W1", [
            {"agent_id": "a", "model": "m-a", "enabled": True},
            {"agent_id": "b", "model": "m-b", "enabled": True},
        ])
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/agent-cache/snapshot", headers=_bearer("x"))
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 2
        agent_ids = sorted(a["agent_id"] for a in body["agents"])
        assert agent_ids == ["a", "b"]
