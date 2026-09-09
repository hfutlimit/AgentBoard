#!/usr/bin/env python3
"""One-off cleanup for ghost WorkerWork rows.

Story 434 / 2026-09-09:8 个 worker_work 记录(Work 18/21/24/26/28/29/32/97)
带空 entity_type 和 entity_id=0,导致 .NET worker 持续 claim 失败(server 返回
403/404 被 .NET 翻译成统一的 "owner mismatch" 日志)。这个脚本独立连接到
AgentBoard 的 DB(读 .env 里的 AGENTBOARD_DB_URL),直接清理这些脏记录。

Usage:
    python scripts/cleanup_ghost_work.py --dry-run     # 只统计
    python scripts/cleanup_ghost_work.py               # 真的删
    python scripts/cleanup_ghost_work.py --json        # JSON 输出
    python scripts/cleanup_ghost_work.py --limit 5000  # 调整上限

注意:生产用 MariaDB,本脚本使用 SQLAlchemy 的 expand 绑定参数,SQLite/MariaDB
都能跑;不需要额外的迁移或部署步骤。
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
    parser.add_argument("--dry-run", action="store_true",
                        help="只统计,不删除 (默认 False)")
    parser.add_argument("--limit", type=int, default=1000,
                        help="单次最多处理多少行 (默认 1000,最大 10000)")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON 而不是人类可读文本")
    args = parser.parse_args()

    _load_env()

    from sqlalchemy import bindparam, create_engine, text

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

    select_stmt = text(
        "SELECT id, project_id, entity_type, entity_id, kind, state, created_at "
        "FROM worker_work "
        "WHERE entity_id IS NULL OR entity_id <= 0 "
        "   OR entity_type IS NULL OR entity_type = '' "
        "   OR entity_type NOT IN ('proposal', 'task') "
        "ORDER BY id ASC LIMIT :limit"
    )

    with engine.begin() as conn:
        rows = conn.execute(select_stmt, {"limit": args.limit}).mappings().all()
        ids = [int(r["id"]) for r in rows]
        samples = [
            {"id": int(r["id"]), "project_id": int(r["project_id"]),
             "entity_type": r["entity_type"], "entity_id": r["entity_id"],
             "kind": r["kind"], "state": r["state"]}
            for r in rows[:20]
        ]
        deleted = 0
        if ids and not args.dry_run:
            del_stmt = text("DELETE FROM worker_work WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            )
            result = conn.execute(del_stmt, {"ids": ids})
            deleted = int(result.rowcount or 0)

    payload = {
        "matched": len(ids),
        "deleted": deleted,
        "dry_run": args.dry_run,
        "limit": args.limit,
        "sample": samples,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        mode = "DRY-RUN" if args.dry_run else "APPLIED"
        print(f"[{mode}] matched={len(ids)} deleted={deleted} limit={args.limit}")
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
