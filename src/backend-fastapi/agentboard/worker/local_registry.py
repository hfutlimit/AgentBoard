"""Local SQLite agent registry for the worker host (Phase 3 follow-up
+ Phase 4, change `agent-ephemeral-2026-09`).

This is the **source of truth** for the worker host's agent
configuration. The server-side cache is just a temporary projection
of this state. The WebSocket client (P3 follow-up) pushes any local
change to the server cache; if the worker process restarts, the
local SQLite is the persistent state we re-read on next boot.

Schema (single table):

    CREATE TABLE agents (
        agent_id      TEXT PRIMARY KEY,
        cli_command   TEXT NOT NULL DEFAULT '',
        model         TEXT NOT NULL DEFAULT '',
        enabled       INTEGER NOT NULL DEFAULT 1,
        roles         TEXT NOT NULL DEFAULT '["developer","reviewer"]',
        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );

Why SQLite and not JSON/yaml: SQLite gives us ACID writes from
multiple processes (operator portal, CLI restart, scheduled
scripts) without lock-file races. The cost is one extra dependency
that Python ships with stdlib (`sqlite3`), so no new install.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable


DEFAULT_DB_PATH = os.path.expanduser("~/.codebuddy/agents.db")


@dataclass
class LocalAgent:
    """One row in the local agent registry."""
    agent_id: str
    cli_command: str = ""
    model: str = ""
    enabled: bool = True
    roles: list[str] = field(default_factory=lambda: ["developer", "reviewer"])
    updated_at: str = ""

    def to_frame(self) -> dict[str, Any]:
        """Wire shape — matches what the WSS handler expects on HELLO
        / DELTA frames (server reads ``cli_command`` / ``model`` /
        ``enabled`` / ``online`` / ``roles``).
        """
        return {
            "agent_id": self.agent_id,
            "cli_command": self.cli_command,
            "model": self.model,
            "enabled": bool(self.enabled),
            "online": True,
            "roles": list(self.roles),
        }


class LocalAgentRegistry:
    """SQLite-backed registry. Thread-safe; cheap to instantiate per
    thread (the file is the shared state). For the worker host,
    instantiate once at process start and share.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS agents ("
                "  agent_id    TEXT PRIMARY KEY,"
                "  cli_command TEXT NOT NULL DEFAULT '',"
                "  model       TEXT NOT NULL DEFAULT '',"
                "  enabled     INTEGER NOT NULL DEFAULT 1,"
                "  roles       TEXT NOT NULL DEFAULT '[\"developer\",\"reviewer\"]',"
                "  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))"
                ")"
            )

    # ---------- CRUD ----------

    def list_agents(self) -> list[LocalAgent]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT agent_id, cli_command, model, enabled, roles, updated_at "
                "FROM agents ORDER BY agent_id"
            ).fetchall()
        return [self._row_to_agent(r) for r in rows]

    def get(self, agent_id: str) -> LocalAgent | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT agent_id, cli_command, model, enabled, roles, updated_at "
                "FROM agents WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        return self._row_to_agent(row) if row else None

    def upsert(
        self,
        agent_id: str,
        cli_command: str,
        model: str,
        enabled: bool,
        roles: Iterable[str],
    ) -> LocalAgent:
        """Insert or replace one agent. Returns the resulting row.
        `updated_at` is set server-side via SQLite's `datetime('now')`.
        """
        roles_json = json.dumps(list(roles), ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO agents (agent_id, cli_command, model, enabled, roles, updated_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(agent_id) DO UPDATE SET "
                "  cli_command = excluded.cli_command, "
                "  model       = excluded.model, "
                "  enabled     = excluded.enabled, "
                "  roles       = excluded.roles, "
                "  updated_at  = datetime('now')",
                (agent_id, cli_command, model, 1 if enabled else 0, roles_json),
            )
            row = conn.execute(
                "SELECT agent_id, cli_command, model, enabled, roles, updated_at "
                "FROM agents WHERE agent_id = ?", (agent_id,),
            ).fetchone()
        return self._row_to_agent(row)

    def delete(self, agent_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM agents WHERE agent_id = ?", (agent_id,),
            )
        return cur.rowcount > 0

    def set_enabled(self, agent_id: str, enabled: bool) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE agents SET enabled = ?, updated_at = datetime('now') "
                "WHERE agent_id = ?",
                (1 if enabled else 0, agent_id),
            )
        return cur.rowcount > 0

    # ---------- helpers ----------

    @staticmethod
    def _row_to_agent(row: sqlite3.Row | None) -> LocalAgent:
        if row is None:
            return None  # type: ignore[return-value]
        try:
            roles = json.loads(row["roles"])
            if not isinstance(roles, list):
                roles = ["developer", "reviewer"]
        except (json.JSONDecodeError, TypeError):
            roles = ["developer", "reviewer"]
        return LocalAgent(
            agent_id=row["agent_id"],
            cli_command=row["cli_command"] or "",
            model=row["model"] or "",
            enabled=bool(row["enabled"]),
            roles=roles,
            updated_at=row["updated_at"] or "",
        )

    def __len__(self) -> int:
        with self._lock, self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
