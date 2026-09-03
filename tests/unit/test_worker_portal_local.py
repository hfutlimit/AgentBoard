"""Tests for the Phase 4 processor_portal local-SQLite path.

We construct processor_portal's ``app`` directly (not via uvicorn)
with the feature flag set. The WSS client is constructed in a
"never connects" mode (server URL points at a closed port; the
client's reconnect loop will run but never succeed, which is
fine for these tests — they only exercise the HTTP endpoints
that read/write the local SQLite).

Each test uses a fresh temp file for the local registry. The
WSS client is given that same DB so enqueue_delta reads the
just-written rows for HELLO/DELTA frames.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile

import pytest


# ---------- env / module-reload bootstrap ----------

@pytest.fixture
def reloaded_processor_portal(monkeypatch):
    """Set the env vars processor_portal needs, point it at a temp
    local registry, then import (or reimport) the module so the
    module-level ``local_registry`` and ``wss_client`` get built
    against the temp path.

    We use a real sqlite temp file (not an in-memory DB) so
    that the LocalAgentRegistry instance the WSS client also
    references is the same one the endpoints mutate. The WSS
    client doesn't talk to the server in these tests — it just
    needs to construct cleanly.
    """
    fd, tmp_db = tempfile.mkstemp(suffix=".db", prefix="portal_local_")
    os.close(fd)
    # Tell LocalAgentRegistry to use this file. LocalAgentRegistry
    # only reads the path from its ctor, not from an env var; we
    # patch the default in the module.
    from agentboard.processors import local_registry as lr_mod
    monkeypatch.setattr(lr_mod, "DEFAULT_DB_PATH", tmp_db)

    monkeypatch.setenv("AGENTBOARD_EPHEMERAL_AGENTS", "1")
    monkeypatch.setenv("AGENTBOARD_API_URL", "http://127.0.0.1:1")  # closed
    monkeypatch.setenv("AGENTBOARD_WORKER_TOKEN", "test-token")
    # Use a temp file for the local SQLite so tests don't touch
    # the operator's real ~/.codebuddy/agents.db.
    monkeypatch.setenv("AGENTBOARD_LOCAL_AGENT_DB", tmp_db)

    # Force a fresh import so module-level wiring runs against
    # the monkeypatched defaults. We must also invalidate
    # already-imported refs in api_helpers that depend on env
    # at import time.
    for mod in list(sys.modules):
        if mod.startswith("agentboard.processor_portal") or mod == "agentboard.processor_portal":
            del sys.modules[mod]

    import agentboard.processor_portal as wp
    yield wp

    # Cleanup
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            os.unlink(tmp_db + suffix)
        except (FileNotFoundError, PermissionError, OSError):
            pass


# ---------- tests ----------

class TestWorkerPortalLocalPath:
    """The P4 (flag-on) path: portal reads/writes local SQLite."""

    def test_list_empty(self, reloaded_processor_portal):
        wp = reloaded_processor_portal
        from fastapi.testclient import TestClient
        client = TestClient(wp.app)
        # The endpoint must succeed without a live server because
        # the local path does not call out to FastAPI.
        r = client.get("/api/agents")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_list_get(self, reloaded_processor_portal):
        wp = reloaded_processor_portal
        from fastapi.testclient import TestClient
        client = TestClient(wp.app)
        body = {
            "agent_id": "hy4-agent",
            "name": "Hy4",
            "roles": ["developer", "reviewer"],
            "cli_type": "codebuddy",
            "model": "hy4-preview",
            "enabled": True,
            "full_access": True,
        }
        r = client.post("/api/agents", json=body)
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["agent_id"] == "hy4-agent"
        assert created["model"] == "hy4-preview"
        assert "codebuddy" in created["cli_command"]
        assert "hy4-preview" in created["cli_command"]
        assert created["enabled"] is True
        assert created["online"] is True

        # List now has one entry
        r2 = client.get("/api/agents")
        assert r2.status_code == 200
        items = r2.json()
        assert len(items) == 1
        assert items[0]["agent_id"] == "hy4-agent"

    def test_update_changes_model(self, reloaded_processor_portal):
        wp = reloaded_processor_portal
        from fastapi.testclient import TestClient
        client = TestClient(wp.app)
        client.post("/api/agents", json={
            "agent_id": "a", "name": "a", "roles": ["developer"],
            "cli_type": "codebuddy", "model": "minimax-m3",
            "enabled": True, "full_access": True,
        })
        # Update model only
        r = client.put("/api/agents/a", json={"model": "minimax-m2.7"})
        assert r.status_code == 200, r.text
        updated = r.json()
        assert updated["model"] == "minimax-m2.7"
        # cli_command is re-rendered to include the new model
        assert "minimax-m2.7" in updated["cli_command"]

    def test_update_missing_returns_404(self, reloaded_processor_portal):
        wp = reloaded_processor_portal
        from fastapi.testclient import TestClient
        client = TestClient(wp.app)
        r = client.put("/api/agents/nope", json={"model": "x"})
        assert r.status_code == 404

    def test_delete_removes(self, reloaded_processor_portal):
        wp = reloaded_processor_portal
        from fastapi.testclient import TestClient
        client = TestClient(wp.app)
        # Create then delete via the HTTP endpoint.
        r = client.post("/api/agents", json={
            "agent_id": "x", "name": "x", "roles": ["developer"],
            "cli_type": "codebuddy", "model": "m", "enabled": True,
            "full_access": True,
        })
        assert r.status_code == 201
        r2 = client.delete("/api/agents/x")
        assert r2.status_code == 200
        assert r2.json()["ok"] is True
        # Verify gone
        r3 = client.get("/api/agents")
        assert r3.json() == []

    def test_delete_missing_returns_404(self, reloaded_processor_portal):
        wp = reloaded_processor_portal
        from fastapi.testclient import TestClient
        client = TestClient(wp.app)
        r = client.delete("/api/agents/nope")
        assert r.status_code == 404
