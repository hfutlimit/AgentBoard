#!/usr/bin/env python3
"""One-off cleanup for ghost WorkerWork rows.

Story 434 / 2026-09-09：8 个 worker_work 记录(Work 18/21/24/26/28/29/32/97)
带空 entity_type 和 entity_id=0，relay 会把它们反复投给消费者，而这类行永远
claim 不动（claim 用存储列重建 Offer，Pydantic 直接拒绝 → 500），真实工作被排在
后面饿死。本脚本独立连接 AgentBoard 的 DB（读 .env 里的 AGENTBOARD_DB_URL）清理
这些脏记录。

判定谓词与后端共用同一份真源（features/scheduling/worker_work.py 的
GHOST_SELECT_SQL / GHOST_DELETE_SQL），不再在脚本里复写一份 SQL。

Usage:
    python scripts/cleanup_ghost_work.py                 # 只统计（默认 dry-run）
    python scripts/cleanup_ghost_work.py --apply \\
        --confirm delete-ghost-rows                      # 真的删
    python scripts/cleanup_ghost_work.py --json         # JSON 输出
    python scripts/cleanup_ghost_work.py --limit 5000   # 调整上限（1..10000）

注意：生产用 MariaDB，本脚本使用 SQLAlchemy 的 expand 绑定参数，SQLite/MariaDB
都能跑；alembic 数据迁移 d9e0f1a2b3c4 已在 upgrade head 时做过同样的清理，本脚本
用于不便跑迁移的临时排障。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 让脚本在仓库根目录直接可跑
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "backend-fastapi"))


def _load_env() -> None:
    """把 .env 里的 AGENTBOARD_DB_URL 注入到 os.environ。"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="真正删除；缺省时只统计（dry-run）")
    parser.add_argument("--confirm", default=None, metavar="PHRASE",
                        help='--apply 时必须传 "delete-ghost-rows"')
    parser.add_argument("--limit", type=int, default=1000,
                        help="单次最多处理多少行 (默认 1000,范围 1..10000)")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON 而不是人类可读文本")
    args = parser.parse_args()

    # Load .env first: importing agentboard builds its engine from
    # AGENTBOARD_DB_URL at import time, and without the file loaded that would
    # fall back to the default URL and create a stray ./agentboard.db.
    _load_env()

    # Mirror the endpoint's Field(ge=1, le=10000) so an operator typo cannot turn
    # this into an unbounded sweep or a no-op negative LIMIT.
    if not 1 <= args.limit <= 10000:
        print("ERROR: --limit must be between 1 and 10000", file=sys.stderr)
        return 2
    from agentboard.features.scheduling.worker_work import (
        GHOST_CLEANUP_CONFIRM, GHOST_DELETE_SQL, GHOST_SELECT_SQL)

    if args.apply and args.confirm != GHOST_CLEANUP_CONFIRM:
        print(f'ERROR: --apply requires --confirm "{GHOST_CLEANUP_CONFIRM}"',
              file=sys.stderr)
        return 2

    from sqlalchemy import bindparam, create_engine, text

    # Single source of truth for what counts as a ghost row, shared with the
    # claim guard, the relay filter, and the admin endpoint.
    db_url = os.environ.get("AGENTBOARD_DB_URL")
    if not db_url:
        print("ERROR: AGENTBOARD_DB_URL not set; check .env or environment",
              file=sys.stderr)
        return 2

    # SQLite 需要 check_same_thread=False
    connect_args = (
        {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    )
    engine = create_engine(db_url, connect_args=connect_args, future=True)
    dry_run = not args.apply

    with engine.begin() as conn:
        rows = conn.execute(text(GHOST_SELECT_SQL), {"limit": args.limit}).mappings().all()
        ids = [int(r["id"]) for r in rows]
        samples = [
            {"id": int(r["id"]), "project_id": int(r["project_id"]),
             "entity_type": r["entity_type"], "entity_id": r["entity_id"],
             "kind": r["kind"], "state": r["state"]}
            for r in rows[:20]
        ]
        # source_work_id is a plain Integer (no FK), so report the dangling
        # discussions a delete would leave behind.
        linked_discussions = 0
        deleted = 0
        if ids:
            linked = text(
                "SELECT COUNT(*) AS n FROM worker_discussions "
                "WHERE source_work_id IN :ids").bindparams(
                bindparam("ids", expanding=True))
            try:
                linked_discussions = int(conn.execute(linked, {"ids": ids}).scalar() or 0)
            except Exception:
                linked_discussions = -1  # table absent on a pre-discussion database
        if ids and not dry_run:
            del_stmt = text(GHOST_DELETE_SQL).bindparams(bindparam("ids", expanding=True))
            result = conn.execute(del_stmt, {"ids": ids})
            deleted = int(result.rowcount or 0)

    payload = {
        "matched": len(ids),
        "deleted": deleted,
        "dry_run": dry_run,
        "limit": args.limit,
        "linked_discussions": linked_discussions,
        "sample": samples,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        mode = "DRY-RUN" if dry_run else "APPLIED"
        print(f"[{mode}] matched={len(ids)} deleted={deleted} limit={args.limit} "
              f"linked_discussions={linked_discussions}")
        for s in samples:
            print(f"  id={s['id']:>5} project={s['project_id']:>3} "
                  f"entity_type={s['entity_type']!r:<14} "
                  f"entity_id={s['entity_id']} kind={s['kind']:<14} "
                  f"state={s['state']}")
        if len(ids) > len(samples):
            print(f"  ... ({len(ids) - len(samples)} more)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
