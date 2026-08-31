#!/usr/bin/env python3
"""Dry-run/apply backfill for AgentInstance.executor_type.

The migration performs a conservative CLI-command backfill.  This utility is
the operator-facing follow-up for rows that need legacy Agent.roles inference.
It never treats roles as workload eligibility; it only recovers the physical
executor used by an already-registered Worker instance.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter

from sqlalchemy import create_engine, text


EXECUTOR_TYPES = ("codex", "workbuddy", "minimax", "fake")


def _infer(cli_command: str | None, roles_raw: str | None) -> tuple[str | None, str]:
    command = (cli_command or "").lower()
    for executor_type in EXECUTOR_TYPES:
        if executor_type in command:
            return executor_type, "cli_command"
    try:
        roles = json.loads(roles_raw or "[]")
    except (TypeError, json.JSONDecodeError):
        roles = []
    normalized = {str(value).strip().lower() for value in roles}
    for executor_type in EXECUTOR_TYPES:
        if executor_type in normalized:
            return executor_type, "legacy_role"
    return None, "unresolved"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("AGENTBOARD_DB_URL", ""),
        help="SQLAlchemy URL; defaults to AGENTBOARD_DB_URL.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist inferred values. Without this flag the command is read-only.",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or AGENTBOARD_DB_URL is required")

    engine = create_engine(args.database_url)
    inferred: list[tuple[int, str, str]] = []
    unresolved: list[int] = []
    with engine.begin() as connection:
        rows = connection.execute(text("""
            SELECT ai.id, ai.cli_command, a.roles
            FROM agent_instances AS ai
            LEFT JOIN agents AS a ON a.agent_id = ai.agent_id
            WHERE ai.executor_type IS NULL OR ai.executor_type = ''
            ORDER BY ai.id
        """)).mappings().all()
        for row in rows:
            executor_type, source = _infer(row["cli_command"], row["roles"])
            if executor_type is None:
                unresolved.append(int(row["id"]))
                continue
            inferred.append((int(row["id"]), executor_type, source))
        if args.apply:
            for instance_id, executor_type, _source in inferred:
                connection.execute(text("""
                    UPDATE agent_instances
                    SET executor_type = :executor_type
                    WHERE id = :instance_id
                      AND (executor_type IS NULL OR executor_type = '')
                """), {
                    "instance_id": instance_id,
                    "executor_type": executor_type,
                })

    source_counts = Counter(source for _, _, source in inferred)
    print(f"mode={'apply' if args.apply else 'dry-run'}")
    print(f"candidates={len(inferred) + len(unresolved)}")
    print(f"inferred={len(inferred)} by_source={dict(sorted(source_counts.items()))}")
    print(f"unresolved={len(unresolved)} ids={unresolved}")
    return 0 if not unresolved else 2


if __name__ == "__main__":
    raise SystemExit(main())
