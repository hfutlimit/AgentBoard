#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
NuGet vulnerability audit gate for the .NET BFF.

Runs `dotnet list package --vulnerable --include-transitive`, extracts every
advisory (GHSA id), and compares it against the curated allowlist in
`dotnet/security-allowlist.json`.

Exit codes (CI semantics):
    0  - no vulnerabilities, OR every reported advisory is on the allowlist
    1  - at least one reported advisory is NOT on the allowlist (gate fails)
    2  - infrastructure error (dotnet missing, allowlist missing/broken)

This is the explicit "close the security gate" mechanism for the known
NU1902 / NU1903 advisories that have no upstream fix: the build stays green
via `WarningsNotAsErrors`, but CI will turn red the moment a NEW (un-reviewed)
advisory appears, forcing an owner to either upgrade the package or accept it
in the allowlist with a justification + review-by date.

Usage:
    python scripts/nuget-audit-gate.py
    python scripts/nuget-audit-gate.py --allowlist dotnet/security-allowlist.json \
        --solution dotnet/AgentBoard.slnx --report security-audit-report.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALLOWLIST = REPO_ROOT / "dotnet" / "security-allowlist.json"
DEFAULT_SOLUTION = REPO_ROOT / "dotnet" / "AgentBoard.slnx"

GHSA_RE = re.compile(r"GHSA-[0-9A-Za-z]{4}-[0-9A-Za-z]{4}-[0-9A-Za-z]{4}", re.IGNORECASE)


def run_dotnet(solution: Path) -> tuple[int, str]:
    """Run the vulnerable-package listing and return (rc, stdout+stderr)."""
    proc = subprocess.run(
        [
            "dotnet",
            "list",
            str(solution),
            "package",
            "--vulnerable",
            "--include-transitive",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return proc.returncode, proc.stdout + "\n" + proc.stderr


def extract_ghsa(text: str) -> set[str]:
    return {m.group(0).upper() for m in GHSA_RE.finditer(text)}


def load_allowlist(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {a["ghsa"].upper() for a in data.get("advisories", [])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST),
                        help="Path to the curated security allowlist JSON.")
    parser.add_argument("--solution", default=str(DEFAULT_SOLUTION),
                        help="Solution to audit.")
    parser.add_argument("--report", default=None,
                        help="Optional path to write a JSON audit report.")
    args = parser.parse_args()

    allowlist_path = Path(args.allowlist)
    if not allowlist_path.exists():
        print(f"error: allowlist not found: {allowlist_path}", file=sys.stderr)
        return 2

    solution_path = Path(args.solution)
    if not solution_path.exists():
        print(f"error: solution not found: {solution_path}", file=sys.stderr)
        return 2

    try:
        allowed = load_allowlist(allowlist_path)
    except (json.JSONDecodeError, KeyError) as exc:
        print(f"error: cannot parse allowlist {allowlist_path}: {exc}", file=sys.stderr)
        return 2

    rc, output = run_dotnet(solution_path)
    # `dotnet list --vulnerable` returns 0 regardless of findings; we rely on
    # parsing the advisory list, not the exit code. A non-zero rc here means
    # dotnet itself failed (restore error, missing SDK) -> infrastructure error.
    if rc != 0:
        print("error: `dotnet list package --vulnerable` failed (rc=%d)." % rc, file=sys.stderr)
        print(output, file=sys.stderr)
        return 2

    found = extract_ghsa(output)
    unknown = sorted(found - allowed)
    known = sorted(found & allowed)

    report = {
        "status": "passed" if not unknown else "failed",
        "reportedCount": len(found),
        "acceptedCount": len(known),
        "unknownCount": len(unknown),
        "accepted": known,
        "unknown": unknown,
    }

    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote audit report -> {args.report}")

    if not found:
        print("no vulnerable packages reported — gate passed.")
        return 0

    if unknown:
        print(f"SECURITY GATE FAILED: {len(unknown)} un-reviewed advisory(ies):")
        for g in unknown:
            print(f"  - {g}")
        print("Action: upgrade the package, or review + add it to "
              f"{allowlist_path.name} with a justification and review-by date.",
              file=sys.stderr)
        return 1

    print(f"gate passed: {len(known)} reported advisory(ies) all on the allowlist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
