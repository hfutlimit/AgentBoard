#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Compare the live FastAPI /openapi.json against the committed
src/backend-dotnet/contracts/openapi-v3.json snapshot.

Two modes:
    * default      — just hash-check the committed snapshot against the
                     pinned .sha256 (the contract-freeze guard).
    * --check-live — also pull the live /openapi.json and diff it against the
                     committed snapshot, emitting a structured report.

Exit codes (CI semantics):
    0  - 0 drift (snapshots match / approved drift only)
    1  - drift detected (live differs from committed, and not all approved)
    2  - infrastructure error (FastAPI down, file missing, etc.)

Fail-closed policy:
    * `--check-live` against an unreachable FastAPI returns exit 2 -> CI fails.
    * Any drift NOT covered by `--approved-drift` returns exit 1 -> CI fails.
    * Intentional breaking changes are approved explicitly by committing an
      `approved-drift` JSON (see docs/contracts/contract-freeze.md) and
      refreshing the snapshot via scripts/sync-openapi.ps1 in the same PR.

Artifacts:
    * `--report PATH` writes a JSON report (semantic summary + full path-level
      diff) suitable for upload as a CI artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "src" / "backend-dotnet" / "contracts"
SNAPSHOT_PATH = CONTRACTS_DIR / "openapi-v3.json"
SHA_PATH = CONTRACTS_DIR / "openapi-v3.sha256"


def hash_file(path: Path) -> str:
    """SHA-256 over raw bytes (matches PowerShell Get-FileHash on Windows)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_live(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def walk(obj, prefix="$"):
    """Yield every dotted leaf path and value in a JSON document."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{prefix}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def load_approved(path: str | None) -> tuple[set[str], set[str], set[str]]:
    """Return approved added, removed, and changed leaf-path sets."""
    if not path:
        return set(), set(), set()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    added = {str(p) for p in data.get("added", [])}
    removed = {str(p) for p in data.get("removed", [])}
    changed = {str(p) for p in data.get("changed", [])}
    return added, removed, changed


def write_report(path: str | None, report: dict) -> None:
    if not path:
        return
    Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote drift report -> {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fastapi-url",
        default="http://127.0.0.1:18000",
        help="Base URL of the FastAPI service (env: AGENTBOARD_FASTAPI_URL).",
    )
    parser.add_argument(
        "--check-live",
        action="store_true",
        help="Also fetch the live /openapi.json and compare against the committed snapshot.",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Path to write a JSON drift report (CI artifact).",
    )
    parser.add_argument(
        "--approved-drift",
        default=None,
        help="JSON file listing explicitly-approved path diffs (breaking-change approval).",
    )
    args = parser.parse_args()

    report: dict = {"mode": "live" if args.check_live else "hash"}

    if not SNAPSHOT_PATH.exists():
        msg = f"error: {SNAPSHOT_PATH} not found. Run scripts/sync-openapi.ps1 to produce it."
        print(msg, file=sys.stderr)
        report.update(exitCode=2, summary=msg)
        write_report(args.report, report)
        return 2

    snapshot_text = SNAPSHOT_PATH.read_text(encoding="utf-8")
    snapshot_hash = hash_file(SNAPSHOT_PATH)
    report["snapshotHash"] = snapshot_hash

    pinned_match = True
    if SHA_PATH.exists():
        pinned = SHA_PATH.read_text(encoding="utf-8").split()[0].lower()
        pinned_match = pinned == snapshot_hash
        report["pinnedHashMatch"] = pinned_match
        if not pinned_match:
            print(
                f"error: pinned hash {pinned} does not match snapshot hash {snapshot_hash}.",
                file=sys.stderr,
            )
            print("Either re-run sync-openapi.ps1 or update the .sha256 if intentional.",
                  file=sys.stderr)
            report.update(exitCode=1, summary="pinned hash does not match snapshot")
            write_report(args.report, report)
            return 1
        print(f"pinned hash matches snapshot ({snapshot_hash[:12]}...)")

    if not args.check_live:
        print("drift check skipped (no --check-live); snapshot is consistent.")
        report.update(exitCode=0, summary="snapshot consistent (hash mode)")
        write_report(args.report, report)
        return 0

    # --- Live drift mode ---
    approved_added, approved_removed, approved_changed = load_approved(
        args.approved_drift
    )
    if args.approved_drift:
        report["approvedDriftFile"] = args.approved_drift

    live_url = f"{args.fastapi_url.rstrip('/')}/openapi.json"
    print(f"Fetching live schema from {live_url} ...")
    try:
        live_text = fetch_live(live_url)
    except (urllib.error.URLError, ConnectionError) as exc:
        msg = f"error: cannot reach FastAPI at {live_url}: {exc}"
        print(msg, file=sys.stderr)
        report.update(liveReachable=False, exitCode=2,
                      summary="FastAPI unreachable (infrastructure error)")
        write_report(args.report, report)
        return 2

    report["liveReachable"] = True
    snap_obj = json.loads(snapshot_text)
    live_obj = json.loads(live_text)
    if live_obj == snap_obj:
        print("no drift — live FastAPI matches the committed snapshot.")
        report.update(drift=False, exitCode=0, summary="no drift (live matches snapshot)")
        write_report(args.report, report)
        return 0

    snap_values = dict(walk(snap_obj))
    live_values = dict(walk(live_obj))
    snap_paths = set(snap_values)
    live_paths = set(live_values)
    added = sorted(live_paths - snap_paths)
    removed = sorted(snap_paths - live_paths)
    changed = sorted(
        p for p in snap_paths & live_paths if snap_values[p] != live_values[p]
    )

    unapproved_added = [p for p in added if p not in approved_added]
    unapproved_removed = [p for p in removed if p not in approved_removed]
    unapproved_changed = [p for p in changed if p not in approved_changed]

    report.update(
        drift=True,
        added=added,
        removed=removed,
        changed=changed,
        unapprovedAdded=unapproved_added,
        unapprovedRemoved=unapproved_removed,
        unapprovedChanged=unapproved_changed,
    )

    print("drift detected:")
    for p in added:
        tag = " [approved]" if p in approved_added else ""
        print(f"  + {p}{tag}")
    for p in removed:
        tag = " [approved]" if p in approved_removed else ""
        print(f"  - {p}{tag}")
    for p in changed:
        tag = " [approved]" if p in approved_changed else ""
        print(f"  ~ {p}{tag}")

    if unapproved_added or unapproved_removed or unapproved_changed:
        summary = (f"drift detected: {len(unapproved_added)} unapproved added, "
                   f"{len(unapproved_removed)} unapproved removed, "
                   f"{len(unapproved_changed)} unapproved changed")
        print("Run scripts/sync-openapi.ps1 to refresh the snapshot, then review + commit.",
              file=sys.stderr)
        if args.approved_drift:
            print("Approved diffs were supplied but unapproved diffs remain — fail-closed.",
                  file=sys.stderr)
        report.update(exitCode=1, summary=summary)
        write_report(args.report, report)
        return 1

    # All drift was explicitly approved.
    print("all drift covered by --approved-drift; passing (approved breaking change).")
    report.update(exitCode=0,
                  summary="drift present but fully covered by approved-drift allowlist")
    write_report(args.report, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
