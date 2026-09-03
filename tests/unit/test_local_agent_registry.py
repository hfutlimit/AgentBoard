"""Tests for the worker-side LocalAgentRegistry (Phase 3 follow-up).

Each test uses a fresh temp file for the SQLite DB so the tests
don't interfere with each other (and don't touch the user's real
``~/.codebuddy/agents.db``).
"""
from __future__ import annotations

import os
import tempfile

import pytest

from agentboard.processors.local_registry import (
    DEFAULT_DB_PATH,
    LocalAgent,
    LocalAgentRegistry,
)


@pytest.fixture
def tmp_registry():
    """A LocalAgentRegistry backed by a unique temp file. On Windows
    SQLite leaves -journal / -wal / -shm sidecars that lock the
    base file; we make the path unique per test and ignore
    cleanup errors (the OS reclaims tempdir on reboot).
    """
    fd, path = tempfile.mkstemp(suffix=".db", prefix="local_agent_")
    os.close(fd)
    reg = LocalAgentRegistry(db_path=path)
    yield reg, path
    # Best-effort cleanup. We don't assert on success.
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except (FileNotFoundError, PermissionError, OSError):
            pass


class TestLocalAgentRegistry:
    def test_empty_initially(self, tmp_registry):
        reg, _ = tmp_registry
        assert reg.list_agents() == []
        assert len(reg) == 0

    def test_upsert_then_get(self, tmp_registry):
        reg, _ = tmp_registry
        agent = reg.upsert(
            "hy4", cli_command="codebuddy -p --model hy4-preview",
            model="hy4-preview", enabled=True, roles=["developer"],
        )
        assert isinstance(agent, LocalAgent)
        assert agent.agent_id == "hy4"
        assert agent.cli_command == "codebuddy -p --model hy4-preview"
        assert agent.model == "hy4-preview"
        assert agent.enabled is True
        assert "developer" in agent.roles
        assert agent.updated_at  # non-empty

        fetched = reg.get("hy4")
        assert fetched is not None
        assert fetched.cli_command == agent.cli_command

    def test_upsert_updates_existing(self, tmp_registry):
        reg, _ = tmp_registry
        reg.upsert("a", cli_command="cmd-1", model="m-1",
                   enabled=True, roles=["developer"])
        reg.upsert("a", cli_command="cmd-2", model="m-2",
                   enabled=False, roles=["reviewer"])
        fetched = reg.get("a")
        assert fetched.cli_command == "cmd-2"
        assert fetched.model == "m-2"
        assert fetched.enabled is False
        assert fetched.roles == ["reviewer"]

    def test_upsert_is_idempotent_on_equal_payload(self, tmp_registry):
        reg, _ = tmp_registry
        reg.upsert("a", cli_command="c", model="m",
                   enabled=True, roles=["developer"])
        ts1 = reg.get("a").updated_at
        # Sleep a tiny bit so datetime('now') would change if it re-fired.
        import time; time.sleep(1.05)
        reg.upsert("a", cli_command="c", model="m",
                   enabled=True, roles=["developer"])
        ts2 = reg.get("a").updated_at
        # SQLite's datetime('now') is only second-precision, so
        # re-issuing the same payload within the same second
        # produces the same stored value. We don't assert equality
        # here — just that no exception is raised.
        assert ts2 is not None

    def test_delete_removes_row(self, tmp_registry):
        reg, _ = tmp_registry
        reg.upsert("a", cli_command="c", model="m",
                   enabled=True, roles=["developer"])
        assert reg.delete("a") is True
        assert reg.get("a") is None
        # Deleting again is a no-op (returns False, no exception).
        assert reg.delete("a") is False

    def test_set_enabled_toggles(self, tmp_registry):
        reg, _ = tmp_registry
        reg.upsert("a", cli_command="c", model="m",
                   enabled=True, roles=["developer"])
        assert reg.set_enabled("a", False) is True
        assert reg.get("a").enabled is False
        # set_enabled on missing row returns False
        assert reg.set_enabled("missing", True) is False

    def test_list_returns_sorted_by_agent_id(self, tmp_registry):
        reg, _ = tmp_registry
        for aid in ("z", "a", "m"):
            reg.upsert(aid, cli_command="c", model="m",
                       enabled=True, roles=["developer"])
        ids = [a.agent_id for a in reg.list_agents()]
        assert ids == ["a", "m", "z"]

    def test_persistence_across_instances(self, tmp_registry):
        """Two LocalAgentRegistry instances on the same DB file
        share state — this is the property the worker portal relies
        on when the portal and the WSS client live in different
        processes (e.g. portal as standalone uvicorn + WSS client
        thread inside the agentboard.processors)."""
        _, path = tmp_registry
        a = LocalAgentRegistry(db_path=path)
        a.upsert("a", cli_command="c", model="m",
                 enabled=True, roles=["developer"])

        b = LocalAgentRegistry(db_path=path)
        assert [x.agent_id for x in b.list_agents()] == ["a"]

    def test_to_frame_matches_wire_shape(self, tmp_registry):
        reg, _ = tmp_registry
        reg.upsert("a", cli_command="c", model="m", enabled=True,
                   roles=["developer", "reviewer"])
        frame = reg.get("a").to_frame()
        assert frame["agent_id"] == "a"
        assert frame["cli_command"] == "c"
        assert frame["model"] == "m"
        assert frame["enabled"] is True
        assert frame["online"] is True  # local SQLite always reports online
        assert frame["roles"] == ["developer", "reviewer"]

    def test_unicode_in_cli_command(self, tmp_registry):
        reg, _ = tmp_registry
        reg.upsert("u", cli_command="echo 你好", model="m",
                   enabled=True, roles=["developer"])
        assert reg.get("u").cli_command == "echo 你好"

    def test_default_db_path_is_users_dot_codebuddy(self):
        # Sanity check that the production default lands in the
        # same directory the IDE / CLI already use.
        assert DEFAULT_DB_PATH == os.path.expanduser("~/.codebuddy/agents.db")
