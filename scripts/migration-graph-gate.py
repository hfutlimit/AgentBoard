#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Alembic migration graph gate.

Why this exists
---------------
`alembic upgrade head` fails with ``Multiple head revisions are present`` the
moment two branches land without a merge node. That already happened once
(2026-09-12): the ``workflow_runs`` slice-1 migration branched off
``z8a9b0c1d2e3`` while production sat on ``d9e0f1a2b3c4`` (ghost worker
cleanup), so ``init_db()`` — and therefore every service start — exploded.
It was fixed by adding an empty merge revision ``n2o3p4q5r6s7``.

``init_db()`` catches that class of breakage *implicitly* (it runs
``alembic upgrade head``), but the failure surfaces as a startup crash with an
unrelated-looking traceback. This gate turns it into a named, early,
single-line failure that says exactly which revisions are heads.

What it checks
--------------
1. exactly one head (``--max-heads``, default 1)
2. exactly one base/root revision — two roots means a disconnected subgraph
3. every revision in the versions directory is reachable from the head
   (catches orphan subgraphs that still "look" like one head)
4. no dangling ``down_revision`` (points at a revision that does not exist)

Exit codes (CI semantics):
    0  - graph is healthy
    1  - graph is broken (multiple heads / disconnected / dangling pointer)
    2  - infrastructure error (alembic missing, versions dir unreadable)

Usage:
    python scripts/migration-graph-gate.py
    python scripts/migration-graph-gate.py --report migration-graph-report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "src" / "backend-fastapi"
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
VERSIONS_DIR = BACKEND_DIR / "migrations" / "versions"


def load_script_directory():
    """Build an Alembic ``ScriptDirectory`` without touching a database.

    ``env.py`` is only needed for offline/online migrations; the revision graph
    lives entirely in the versions directory, so no engine is created here.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return ScriptDirectory.from_config(cfg)


def analyse(script) -> dict:
    """Collect the graph facts this gate asserts on."""
    heads = list(script.get_heads())
    bases = list(script.get_bases())

    all_revisions = {rev.revision for rev in script.walk_revisions()}

    # Walk forward from each head to find what the graph can actually reach.
    reachable: set[str] = set()
    for head in heads:
        for rev in script.walk_revisions(base="base", head=head):
            reachable.add(rev.revision)

    dangling: list[dict] = []
    for rev in script.walk_revisions():
        down = rev.down_revision
        parents = list(down) if isinstance(down, (tuple, list)) else ([down] if down else [])
        for parent in parents:
            if parent not in all_revisions:
                dangling.append({"revision": rev.revision, "missing_down_revision": parent})

    unreachable = sorted(all_revisions - reachable)

    def label(rev_id: str) -> dict:
        try:
            return {"revision": rev_id, "file": script.get_revision(rev_id).path}
        except Exception:  # pragma: no cover - defensive only
            return {"revision": rev_id, "file": None}

    return {
        "revision_count": len(all_revisions),
        "heads": [label(h) for h in sorted(heads)],
        "bases": [label(b) for b in sorted(bases)],
        "head_count": len(heads),
        "base_count": len(bases),
        "unreachable_from_head": unreachable,
        "dangling_down_revisions": dangling,
    }


def evaluate(facts: dict, max_heads: int) -> list[str]:
    """Return the list of violations (empty == healthy)."""
    problems: list[str] = []
    if facts["head_count"] > max_heads:
        names = ", ".join(h["revision"] for h in facts["heads"])
        problems.append(
            f"{facts['head_count']} alembic heads present ({names}); "
            "'alembic upgrade head' will fail with 'Multiple head revisions "
            "are present'. Add a merge revision referencing every head."
        )
    if facts["base_count"] != 1:
        names = ", ".join(b["revision"] for b in facts["bases"])
        problems.append(
            f"expected exactly 1 base revision, found {facts['base_count']} ({names}); "
            "the migration graph is disconnected into separate subgraphs."
        )
    if facts["unreachable_from_head"]:
        problems.append(
            f"{len(facts['unreachable_from_head'])} revision(s) unreachable from the "
            f"head: {facts['unreachable_from_head']}; a migration was written but "
            "never linked into the chain, so it will silently never run."
        )
    if facts["dangling_down_revisions"]:
        pairs = ", ".join(
            f"{d['revision']} -> {d['missing_down_revision']}"
            for d in facts["dangling_down_revisions"]
        )
        problems.append(
            f"{len(facts['dangling_down_revisions'])} dangling down_revision "
            f"reference(s): {pairs}. This usually means a rebase/cherry-pick "
            "dropped or renamed a migration."
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alembic migration graph gate")
    parser.add_argument(
        "--max-heads", type=int, default=1,
        help="maximum number of heads allowed (default: 1)",
    )
    parser.add_argument(
        "--report", type=Path, default=None,
        help="write a JSON report to this path (CI artifact)",
    )
    args = parser.parse_args(argv)

    if not ALEMBIC_INI.is_file():
        print(f"[migration-graph] alembic.ini not found at {ALEMBIC_INI}", file=sys.stderr)
        return 2
    if not VERSIONS_DIR.is_dir():
        print(f"[migration-graph] versions dir not found at {VERSIONS_DIR}", file=sys.stderr)
        return 2

    try:
        script = load_script_directory()
        facts = analyse(script)
    except ImportError as exc:
        print(
            f"[migration-graph] alembic is not importable ({exc}); "
            "run: python -m pip install -r src/backend-fastapi/requirements.txt",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        # A malformed revision file raises inside ScriptDirectory; treat it as a
        # broken graph rather than an infrastructure error so CI reports the
        # real culprit in the message.
        print(f"[migration-graph] failed to build the revision graph: {exc}", file=sys.stderr)
        if args.report:
            args.report.write_text(
                json.dumps({"status": "broken", "error": str(exc)}, indent=2),
                encoding="utf-8",
            )
        return 1

    problems = evaluate(facts, args.max_heads)
    facts["status"] = "broken" if problems else "ok"
    facts["problems"] = problems

    if args.report:
        args.report.write_text(json.dumps(facts, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        f"[migration-graph] revisions={facts['revision_count']} "
        f"bases={facts['base_count']} heads={facts['head_count']}"
    )
    for head in facts["heads"]:
        print(f"[migration-graph]   head {head['revision']}  <- {head['file']}")

    if problems:
        print()
        for problem in problems:
            print(f"[migration-graph] FAIL: {problem}")
        return 1

    print("[migration-graph] OK: single connected head, no dangling references")
    return 0


if __name__ == "__main__":
    sys.exit(main())
