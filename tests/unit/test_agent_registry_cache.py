"""Unit tests for AgentRegistryCache (Phase 1, change
`agent-ephemeral-2026-09`).

Covers:
  - apply_hello full-replace semantics
  - apply_delta add/update/remove
  - record_ping heartbeat refresh
  - drop_worker idempotency
  - staleness sweep (offline flip after threshold)
  - snapshot ordering and only_online filter
  - pick_eligible (random, pinned, empty)
  - malformed frame tolerance
  - thread safety (concurrent apply_hello + apply_delta)
  - module-level get_default_cache / reset_default_cache
"""
from __future__ import annotations

import threading
import time
import pytest

from agentboard.agent_registry_cache import (
    AgentCacheEntry,
    AgentRegistryCache,
    get_default_cache,
    reset_default_cache,
    ephemeral_agents_enabled,
)


# ---------- helpers ----------

def _frame(agent_id: str, **overrides) -> dict:
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


# ---------- mutation ----------

class TestApplyHello:
    def test_first_hello_populates_cache(self):
        c = AgentRegistryCache()
        n = c.apply_hello("W1", [_frame("a"), _frame("b")])
        assert n == 2
        assert len(c) == 2

    def test_second_hello_drops_previous_entries_for_same_worker(self):
        c = AgentRegistryCache()
        c.apply_hello("W1", [_frame("a"), _frame("b")])
        n = c.apply_hello("W1", [_frame("a"), _frame("c")])
        assert n == 2
        assert len(c) == 2
        assert c.get("W1", "a") is not None
        assert c.get("W1", "b") is None
        assert c.get("W1", "c") is not None

    def test_hello_for_different_worker_does_not_evict(self):
        c = AgentRegistryCache()
        c.apply_hello("W1", [_frame("a")])
        c.apply_hello("W2", [_frame("a")])
        assert c.get("W1", "a") is not None
        assert c.get("W2", "a") is not None
        assert len(c) == 2

    def test_malformed_frame_without_agent_id_is_skipped(self):
        c = AgentRegistryCache()
        n = c.apply_hello("W1", [
            {"cli_command": "x", "model": "y"},  # no agent_id
            _frame("a"),
            {"agent_id": "  "},  # whitespace-only also blank
        ])
        assert n == 1
        assert c.get("W1", "a") is not None
        assert len(c) == 1

    def test_hello_records_now_as_last_heartbeat(self):
        c = AgentRegistryCache()
        before = time.time()
        c.apply_hello("W1", [_frame("a")])
        entry = c.get("W1", "a")
        assert entry is not None
        assert before <= entry.last_heartbeat <= time.time()


class TestApplyDelta:
    def test_add_new_entry(self):
        c = AgentRegistryCache()
        added, removed = c.apply_delta("W1", add_or_update=[_frame("a")])
        assert added == 1 and removed == 0
        assert c.get("W1", "a") is not None

    def test_update_existing_entry(self):
        c = AgentRegistryCache()
        c.apply_hello("W1", [_frame("a", model="old")])
        c.apply_delta("W1", add_or_update=[_frame("a", model="new")])
        assert c.get("W1", "a").model == "new"

    def test_remove_existing(self):
        c = AgentRegistryCache()
        c.apply_hello("W1", [_frame("a"), _frame("b")])
        added, removed = c.apply_delta("W1", remove=["a"])
        assert added == 0 and removed == 1
        assert c.get("W1", "a") is None
        assert c.get("W1", "b") is not None

    def test_remove_nonexistent_is_noop(self):
        c = AgentRegistryCache()
        added, removed = c.apply_delta("W1", remove=["nope"])
        assert added == 0 and removed == 0


class TestRecordPing:
    def test_ping_refreshes_heartbeat(self):
        c = AgentRegistryCache()
        c.apply_hello("W1", [_frame("a")])
        # Force the entry's last_heartbeat into the past
        c.get("W1", "a").last_heartbeat = time.time() - 100
        c.record_ping("W1", ["a"])
        assert c.get("W1", "a").last_heartbeat > time.time() - 5

    def test_ping_flips_offline_back_to_online(self):
        c = AgentRegistryCache()
        c.apply_hello("W1", [_frame("a", online=False)])
        c.record_ping("W1", ["a"])
        assert c.get("W1", "a").online is True

    def test_ping_unknown_pair_is_silent(self):
        c = AgentRegistryCache()
        # No exception, no side effect
        c.record_ping("W1", ["nope"])
        assert c.get("W1", "nope") is None


class TestDropWorker:
    def test_drop_removes_all(self):
        c = AgentRegistryCache()
        c.apply_hello("W1", [_frame("a"), _frame("b")])
        c.apply_hello("W2", [_frame("c")])
        n = c.drop_worker("W1")
        assert n == 2
        assert c.get("W1", "a") is None
        assert c.get("W1", "b") is None
        assert c.get("W2", "c") is not None

    def test_drop_unknown_worker_is_zero(self):
        c = AgentRegistryCache()
        assert c.drop_worker("never") == 0


# ---------- staleness ----------

class TestStalenessSweep:
    def test_fresh_entries_remain_online(self):
        c = AgentRegistryCache(staleness_seconds=60.0,
                              sweep_interval_seconds=0.0)
        c.apply_hello("W1", [_frame("a")])
        assert c.snapshot()[0]["online"] is True

    def test_stale_entries_flip_offline(self):
        c = AgentRegistryCache(staleness_seconds=0.1,
                              sweep_interval_seconds=0.0)
        c.apply_hello("W1", [_frame("a")])
        time.sleep(0.2)
        flipped = c.sweep_stale()
        assert flipped == 1
        assert c.snapshot(only_online=True) == []
        assert c.snapshot(only_online=False)[0]["online"] is False

    def test_sweep_throttled(self):
        c = AgentRegistryCache(staleness_seconds=0.1,
                              sweep_interval_seconds=10.0)
        c.apply_hello("W1", [_frame("a")])
        time.sleep(0.2)
        # First call runs the sweep
        c.sweep_stale()
        # Second call is throttled — no extra flips
        c.get("W1", "a").online = True  # pretend a fresh PING landed
        c.get("W1", "a").last_heartbeat = time.time() - 5  # make it stale
        # This call should be throttled and return 0
        assert c.sweep_stale() == 0


# ---------- read ----------

class TestSnapshot:
    def test_returns_public_dict_shape(self):
        c = AgentRegistryCache(staleness_seconds=60.0,
                              sweep_interval_seconds=0.0)
        c.apply_hello("W1", [_frame("a", model="m", roles=["developer", "reviewer"])])
        rows = c.snapshot()
        assert len(rows) == 1
        row = rows[0]
        assert row["agent_id"] == "a"
        assert row["worker_id"] == "W1"
        assert row["cli_command"].startswith("codebuddy")
        assert row["model"] == "m"
        assert row["enabled"] is True
        assert row["online"] is True
        assert "developer" in row["roles"]
        assert "last_heartbeat" in row

    def test_only_online_filter_drops_offline(self):
        c = AgentRegistryCache(staleness_seconds=60.0,
                              sweep_interval_seconds=0.0)
        c.apply_hello("W1", [_frame("online")])
        c.apply_hello("W2", [_frame("offline", online=False)])
        # Don't sleep — keep W1's heartbeat fresh. Force W2 stale by
        # backdating its timestamp; sweep should flip it offline.
        c.get("W2", "offline").last_heartbeat = time.time() - 120
        c.sweep_stale()
        online_only = c.snapshot(only_online=True)
        all_rows = c.snapshot(only_online=False)
        assert len(online_only) == 1
        assert online_only[0]["agent_id"] == "online"
        assert len(all_rows) == 2
        assert {r["agent_id"] for r in all_rows} == {"online", "offline"}


class TestPickEligible:
    def test_empty_cache_returns_none(self):
        c = AgentRegistryCache()
        assert c.pick_eligible() is None

    def test_picks_one_eligible(self):
        c = AgentRegistryCache()
        c.apply_hello("W1", [_frame("a", enabled=True)])
        c.apply_hello("W2", [_frame("b", enabled=False)])
        pick = c.pick_eligible()
        assert pick is not None
        worker, agent = pick
        assert (worker, agent) == ("W1", "a")

    def test_pinned_returns_requested_when_present(self):
        c = AgentRegistryCache()
        c.apply_hello("W1", [_frame("a")])
        c.apply_hello("W2", [_frame("b")])
        pick = c.pick_eligible(pinned="b")
        assert pick == ("W2", "b")

    def test_pinned_returns_none_when_not_in_cache(self):
        c = AgentRegistryCache()
        c.apply_hello("W1", [_frame("a")])
        assert c.pick_eligible(pinned="nope") is None

    def test_pinned_skips_disabled(self):
        c = AgentRegistryCache()
        c.apply_hello("W1", [_frame("a", enabled=False)])
        assert c.pick_eligible(pinned="a") is None


# ---------- thread safety ----------

class TestThreadSafety:
    def test_concurrent_apply_hello_and_delta(self):
        c = AgentRegistryCache()
        errors = []

        def hello_worker(wid: str, agents: list[str]):
            try:
                for _ in range(50):
                    c.apply_hello(wid, [_frame(a) for a in agents])
            except Exception as e:
                errors.append(e)

        def delta_worker(wid: str, agent: str):
            try:
                for _ in range(50):
                    c.apply_delta(wid, add_or_update=[_frame(agent, model="m")])
                    c.record_ping(wid, [agent])
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=hello_worker, args=("W1", ["a", "b"])),
            threading.Thread(target=hello_worker, args=("W2", ["c"])),
            threading.Thread(target=delta_worker, args=("W1", "a")),
            threading.Thread(target=delta_worker, args=("W2", "c")),
        ]
        for t in threads: t.start()
        for t in threads: t.join()
        assert errors == []
        # Cache should have at least 1 entry (a, b, or c — depends on
        # last writer), and every entry that's there must be valid.
        assert len(c) >= 1


# ---------- module-level singleton ----------

class TestDefaultCache:
    def setup_method(self):
        reset_default_cache()

    def teardown_method(self):
        reset_default_cache()

    def test_get_default_cache_returns_singleton(self):
        a = get_default_cache()
        b = get_default_cache()
        assert a is b

    def test_reset_drops_singleton(self):
        a = get_default_cache()
        reset_default_cache()
        b = get_default_cache()
        assert a is not b

    def test_ephemeral_agents_flag(self, monkeypatch):
        monkeypatch.delenv("AGENTBOARD_EPHEMERAL_AGENTS", raising=False)
        assert ephemeral_agents_enabled() is False
        monkeypatch.setenv("AGENTBOARD_EPHEMERAL_AGENTS", "1")
        assert ephemeral_agents_enabled() is True
        monkeypatch.setenv("AGENTBOARD_EPHEMERAL_AGENTS", "true")
        assert ephemeral_agents_enabled() is True
        monkeypatch.setenv("AGENTBOARD_EPHEMERAL_AGENTS", "0")
        assert ephemeral_agents_enabled() is False
