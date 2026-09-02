"""Worker-side ephemeral agent registry cache (Phase 1, change
`agent-ephemeral-2026-09`).

Per-process, in-memory. Holds the live agent set as reported by workers
via WebSocket HELLO/DELTA/PING frames (P3 will add the WebSocket
client; this module is server-side cache only — it does **not**
initiate or accept any network I/O).

Design contract (P1, see proposal.md):

- **Source of truth** for the live agent set is the **worker host**,
  not this cache. The cache is reconstructable from the next HELLO
  frame.
- The cache is **transient**: a FastAPI process restart loses it. The
  next 30 seconds of dispatch may return 503 (`Retry-After: 30`)
  while workers reconnect and re-send HELLO. (E option in proposal.)
- A `staleness_sweep()` step marks entries offline if no PING within
  `STALENESS_SECONDS` (default 60s). The sweep is called by the admin
  reader before snapshotting; it's also exposed so P2 dispatch can
  schedule it on a timer.
- Keys are `(worker_id, agent_id)` — the same composite that
  `agent_instances` uses for its unique constraint (P1 leaves the
  table intact for legacy reads; new code never writes to it).
- Pinned dispatch (P5) and capability matching (open question in
  proposal) are deferred; this module only exposes
  `pick_eligible(pinned=None)` returning a `(worker_id, agent_id)`
  tuple or `None`.

Activation: this module is loaded unconditionally; it is **only
consulted** when the `AGENTBOARD_EPHEMERAL_AGENTS=1` env flag is set.
Default off (G2 in proposal — new path is opt-in to preserve the
blast radius).
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger("agentboard.agent_registry_cache")

# Defaults — overridable via env to allow ops to tighten or loosen
# without a code change.
#
# AGENTBOARD_EPHEMERAL_STALENESS_SECONDS — how long (in seconds) the
# cache will keep an entry marked `online` after the last HELLO/DELTA/
# PING touched it. The WSS client sends PING every 15s so 60s is
# comfortable. The HTTP fallback path (P3.1) only fires when the
# worker has an explicit change, so ops running a WSS-blocked
# deployment (IIS ARR not forwarding WebSocket upgrade, nginx without
# `proxy_set_header Upgrade`, etc.) should raise this to e.g. 300s
# to avoid the cache going empty between restarts.
STALENESS_SECONDS_DEFAULT = 60.0
SWEEP_INTERVAL_SECONDS_DEFAULT = 10.0


def _env_staleness_seconds() -> float:
    """Read `AGENTBOARD_EPHEMERAL_STALENESS_SECONDS` from the process
    env. Falls back to `STALENESS_SECONDS_DEFAULT` if unset or
    malformed. Non-positive values disable the sweep entirely (entries
    never go stale — useful for a fixed set of long-lived workers
    whose PING cadence cannot be tightened)."""
    raw = os.environ.get("AGENTBOARD_EPHEMERAL_STALENESS_SECONDS", "").strip()
    if not raw:
        return STALENESS_SECONDS_DEFAULT
    try:
        val = float(raw)
    except ValueError:
        log.warning(
            "agent_registry_cache: invalid AGENTBOARD_EPHEMERAL_STALENESS_SECONDS=%r "
            "— falling back to %.0fs",
            raw, STALENESS_SECONDS_DEFAULT,
        )
        return STALENESS_SECONDS_DEFAULT
    return val


def ephemeral_agents_enabled() -> bool:
    """G2 opt-in flag. Reads on every call so tests / ops can toggle
    without re-importing."""
    return os.environ.get("AGENTBOARD_EPHEMERAL_AGENTS", "").strip() in (
        "1", "true", "yes", "on",
    )


@dataclass
class AgentCacheEntry:
    """One row in the live agent set, indexed by (worker_id, agent_id).

    Fields mirror the public shape the admin UI / worker portal expect
    (cli_command is per-worker; model + enabled are the operator's
    intent and come from the worker).
    """
    worker_id: str
    agent_id: str
    cli_command: str = ""
    model: str = ""
    enabled: bool = True
    online: bool = True
    roles: list[str] = field(default_factory=lambda: ["developer", "reviewer"])
    last_heartbeat: float = field(default_factory=time.time)

    def to_public_dict(self) -> dict[str, Any]:
        """Wire shape — kept stable so admin UI can render without
        caring whether the row came from the cache or the DB.
        """
        return {
            "agent_id": self.agent_id,
            "worker_id": self.worker_id,
            "cli_command": self.cli_command,
            "model": self.model,
            "enabled": self.enabled,
            "online": self.online,
            "roles": ",".join(self.roles),
            "last_heartbeat": self.last_heartbeat,
        }

    def to_owner_dict(self) -> dict[str, Any]:
        """Compatibility with ``AgentInstance.to_owner_dict()`` (DB
        path) so the router can return cache rows from the same
        ``/api/workers/{worker_id}/instances`` endpoint without
        breaking existing consumers.

        Fields not collected by the worker in P1 (``executor_type``,
        ``probe_message``) are returned as ``None`` / empty string
        so the shape stays consistent; later phases can populate them
        via the DELTA frame if needed.
        """
        return {
            "id": None,  # no DB row
            "worker_id": self.worker_id,
            "agent_id": self.agent_id,
            "cli_command": self.cli_command,
            "model": self.model,
            "executor_type": None,
            "enabled": self.enabled,
            "online": self.online,
            "last_heartbeat": self.last_heartbeat,
            "last_probe_at": None,
            "probe_message": "",
            "created_at": self.last_heartbeat,
            "updated_at": self.last_heartbeat,
        }

    def is_fresh(self, *, now: float, staleness_seconds: float) -> bool:
        return (now - self.last_heartbeat) <= staleness_seconds


class AgentRegistryCache:
    """Per-process in-memory live agent set.

    Thread-safe: a single `threading.RLock` guards the dict + the
    staleness sweep. Workers may push from any thread (P3 will use
    the WebSocket reader thread); admin reads happen on the request
    thread.
    """

    def __init__(
        self,
        *,
        staleness_seconds: float | None = None,
        sweep_interval_seconds: float = SWEEP_INTERVAL_SECONDS_DEFAULT,
    ):
        self._lock = threading.RLock()
        # Composite key: (worker_id, agent_id). We use a flat dict
        # keyed by an explicit tuple because we want O(1) add / remove
        # without a tuple-hash decode on every snapshot.
        self._by_pair: dict[tuple[str, str], AgentCacheEntry] = {}
        # None ⇒ read from env (AGENTBOARD_EPHEMERAL_STALENESS_SECONDS)
        # so ops can tune the window without a code change. Tests
        # pass an explicit float to pin the value.
        self._staleness_seconds = float(
            staleness_seconds if staleness_seconds is not None
            else _env_staleness_seconds()
        )
        self._sweep_interval_seconds = float(sweep_interval_seconds)
        self._last_sweep_at: float = time.time()

    # ---------- mutation ----------

    def apply_hello(
        self, worker_id: str, agents: Iterable[dict[str, Any]]
    ) -> int:
        """Full replace of all agents for one worker. Returns the
        number of entries applied.

        Use this on WebSocket connect (or reconnect) — the worker
        tells the server "this is my current state, throw away
        whatever you had for me". Idempotent within a frame.
        """
        applied = 0
        now = time.time()
        with self._lock:
            # Drop any pre-existing entries for this worker; the new
            # HELLO is authoritative.
            self._drop_worker(worker_id, _locked=True)
            for raw in agents:
                entry = self._entry_from_frame(worker_id, raw, now=now)
                if entry is None:
                    continue
                self._by_pair[(worker_id, entry.agent_id)] = entry
                applied += 1
        return applied

    def apply_delta(
        self,
        worker_id: str,
        *,
        add_or_update: Iterable[dict[str, Any]] = (),
        remove: Iterable[str] = (),
    ) -> tuple[int, int]:
        """Incremental update. Returns (added_or_updated, removed).

        Use this when the worker changes a single agent at a time
        (operator edit, CLI restart, OAuth re-login). Idempotent.
        """
        added = 0
        removed = 0
        now = time.time()
        with self._lock:
            for agent_id in remove:
                if self._by_pair.pop((worker_id, agent_id), None) is not None:
                    removed += 1
            for raw in add_or_update:
                entry = self._entry_from_frame(worker_id, raw, now=now)
                if entry is None:
                    continue
                self._by_pair[(worker_id, entry.agent_id)] = entry
                added += 1
        return added, removed

    def record_ping(self, worker_id: str, agent_ids: Iterable[str]) -> int:
        """Update the heartbeat timestamp for the given
        (worker, agent) pairs without changing other fields. Returns
        the number of entries whose timestamp was refreshed.
        """
        now = time.time()
        touched = 0
        with self._lock:
            for agent_id in agent_ids:
                entry = self._by_pair.get((worker_id, agent_id))
                if entry is None:
                    # PING for an entry we don't know about. Drop it
                    # silently — the next HELLO will re-register.
                    continue
                entry.last_heartbeat = now
                entry.online = True
                touched += 1
        return touched

    def drop_worker(self, worker_id: str) -> int:
        """Force-remove all entries for a worker. Called when the
        worker's WebSocket connection closes for good (auth revoke,
        explicit teardown). Idempotent.
        """
        with self._lock:
            return self._drop_worker(worker_id, _locked=True)

    def _drop_worker(self, worker_id: str, *, _locked: bool) -> int:
        if not _locked:
            with self._lock:
                return self._drop_worker(worker_id, _locked=True)
        removed = 0
        for pair in list(self._by_pair.keys()):
            if pair[0] == worker_id:
                self._by_pair.pop(pair, None)
                removed += 1
        return removed

    @staticmethod
    def _entry_from_frame(
        worker_id: str, raw: dict[str, Any], *, now: float
    ) -> AgentCacheEntry | None:
        agent_id = str(raw.get("agent_id", "")).strip()
        if not agent_id:
            # Frame without an agent_id is malformed; ignore it but
            # do not raise — a single bad row should not break the
            # whole HELLO frame.
            log.warning("apply_hello: skipping frame without agent_id (worker=%s)", worker_id)
            return None
        return AgentCacheEntry(
            worker_id=worker_id,
            agent_id=agent_id,
            cli_command=str(raw.get("cli_command", "")),
            model=str(raw.get("model", "")),
            enabled=bool(raw.get("enabled", True)),
            online=bool(raw.get("online", True)),
            roles=list(raw.get("roles") or ["developer", "reviewer"]),
            last_heartbeat=now,
        )

    # ---------- staleness ----------

    def sweep_stale(self, *, now: float | None = None) -> int:
        """Mark entries whose last_heartbeat is older than
        `self._staleness_seconds` as offline. Returns the number of
        entries flipped to offline.

        Idempotent; cheap (O(n) over the dict). The cache also
        throttles this call to `sweep_interval_seconds` so callers
        can invoke it on every admin read without worrying about
        hot-loop cost.

        If `staleness_seconds <= 0` the sweep is skipped entirely and
        all entries are treated as permanently fresh. This is the
        escape hatch for HTTP-only-fallback workers behind a
        WSS-hostile proxy whose cache should not age out.
        """
        now = now if now is not None else time.time()
        if self._staleness_seconds <= 0:
            return 0
        with self._lock:
            if (now - self._last_sweep_at) < self._sweep_interval_seconds:
                return 0
            self._last_sweep_at = now
            flipped = 0
            for entry in self._by_pair.values():
                if entry.online and not entry.is_fresh(
                    now=now, staleness_seconds=self._staleness_seconds,
                ):
                    entry.online = False
                    flipped += 1
        if flipped:
            log.info("agent_registry_cache: marked %d entries offline (stale > %.0fs)",
                     flipped, self._staleness_seconds)
        return flipped

    def set_staleness_seconds(self, value: float) -> None:
        with self._lock:
            self._staleness_seconds = float(value)
            # Force the next sweep to run promptly.
            self._last_sweep_at = 0.0

    # ---------- read ----------

    def snapshot(self, *, only_online: bool = True) -> list[dict[str, Any]]:
        """Return a list of public dicts for the admin UI. Runs a
        throttled staleness sweep first so stale rows do not appear
        as online.

        The `only_online` flag lets P2 dispatch see the full set if
        it needs to distinguish "known but offline" from "never
        seen". The admin UI uses `only_online=True` (the default).
        """
        self.sweep_stale()
        with self._lock:
            return [
                entry.to_public_dict()
                for entry in self._by_pair.values()
                if (not only_online) or entry.online
            ]

    def pick_eligible(
        self,
        *,
        pinned: str | None = None,
        only_online: bool = True,
    ) -> tuple[str, str] | None:
        """Return ``(worker_id, agent_id)`` or ``None``.

        If ``pinned`` is provided AND a matching entry exists (and
        optionally online), return it directly. Otherwise pick a
        random eligible entry from the cache.

        Random selection is the default per decision D in the
        proposal. Capability-aware matching is an open question
        (P2+); for P1 we only support pinning.
        """
        self.sweep_stale()
        with self._lock:
            if pinned:
                # Look up the pinned entry. We don't know which
                # worker it lives on, so scan. P5's UI pin will
                # carry a (worker, agent) composite.
                for (w, a), entry in self._by_pair.items():
                    if a == pinned and entry.enabled and (
                        not only_online or entry.online
                    ):
                        return (w, a)
                return None
            eligible = [
                (w, a)
                for (w, a), entry in self._by_pair.items()
                if entry.enabled and (not only_online or entry.online)
            ]
        if not eligible:
            return None
        return random.choice(eligible)

    def get(self, worker_id: str, agent_id: str) -> AgentCacheEntry | None:
        with self._lock:
            return self._by_pair.get((worker_id, agent_id))

    def by_worker(self, worker_id: str) -> list[AgentCacheEntry]:
        """Return all entries for one worker (any online state)."""
        with self._lock:
            return [
                entry for (w, _), entry in self._by_pair.items()
                if w == worker_id
            ]

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_pair)


# Module-level singleton. The router reads from this; tests construct
# their own instance via `AgentRegistryCache()` and pass it in.
_default_cache: AgentRegistryCache | None = None
_default_cache_lock = threading.Lock()


def get_default_cache() -> AgentRegistryCache:
    """Return the process-wide cache. Lazy-initialized; safe to call
    from any thread. The router uses this; tests should construct
    their own.
    """
    global _default_cache
    if _default_cache is None:
        with _default_cache_lock:
            if _default_cache is None:
                _default_cache = AgentRegistryCache()
    return _default_cache


def reset_default_cache() -> None:
    """Test helper: drop the module-level cache. Not for production
    use — the cache is meant to be process-wide.
    """
    global _default_cache
    with _default_cache_lock:
        _default_cache = None
