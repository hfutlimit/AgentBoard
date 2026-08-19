#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Compare the live FastAPI /openapi.json against the committed
dotnet/contracts/openapi-v3.json snapshot.

Exit codes:
    0  - 0 drift (snapshots match)
    1  - drift detected (live differs from committed)
    2  - infrastructure error (FastAPI down, file missing, etc.)

The script tolerates a missing live FastAPI by only running when
--check-live is passed, so the same script can be used in two modes:

    * default: just hash-check the committed file
    * --check-live: also pull the live document and diff it
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
CONTRACTS_DIR = REPO_ROOT / "dotnet" / "contracts"
SNAPSHOT_PATH = CONTRACTS_DIR / "openapi-v3.json"
SHA_PATH = CONTRACTS_DIR / "openapi-v3.sha256"


def hash_doc(text: str) -> str:
    """SHA-256 over the raw document bytes (matches PowerShell Get-FileHash)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_live(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fastapi-url",
        default="http://127.0.0.1:18000",
        help="Base URL of the FastAPI service (env: AGENTBOARD_FASTAPI_URL)",
    )
    parser.add_argument(
        "--check-live",
        action="store_true",
        help="Also fetch the live /openapi.json and compare against the committed snapshot.",
    )
    args = parser.parse_args()

    if not SNAPSHOT_PATH.exists():
        print(f"error: {SNAPSHOT_PATH} not found.", file=sys.stderr)
        print("Run scripts/sync-openapi.ps1 to produce it.", file=sys.stderr)
        return 2

    snapshot_text = SNAPSHOT_PATH.read_text(encoding="utf-8")
    snapshot_hash = hash_doc(snapshot_text)

    if SHA_PATH.exists():
        pinned = SHA_PATH.read_text(encoding="utf-8").split()[0].lower()
        if pinned != snapshot_hash:
            print(
                f"error: pinned hash {pinned} does not match snapshot hash {snapshot_hash}.",
                file=sys.stderr,
            )
            print("Either re-run sync-openapi.ps1 or update the .sha256 if the change is intentional.", file=sys.stderr)
            return 1
        print(f"pinned hash matches snapshot ({snapshot_hash[:12]}...)")

    if not args.check_live:
        print("drift check skipped (no --check-live); snapshot is consistent.")
        return 0

    live_url = f"{args.fastapi_url.rstrip('/')}/openapi.json"
    print(f"Fetching live schema from {live_url} ...")
    try:
        live_text = fetch_live(live_url)
    except (urllib.error.URLError, ConnectionError) as exc:
        print(f"error: cannot reach FastAPI at {live_url}: {exc}", file=sys.stderr)
        return 2

    live_hash = hash_doc(live_text)
    if live_hash == snapshot_hash:
        print("no drift — live FastAPI matches the committed snapshot.")
        return 0

    # Drift detected. Show a unified-style summary of where the documents differ.
    snap_obj = json.loads(snapshot_text)
    live_obj = json.loads(live_text)
    snap_paths = {p for p in _walk(snap_obj)}
    live_paths = {p for p in _walk(live_obj)}
    added = sorted(live_paths - snap_paths)
    removed = sorted(snap_paths - live_paths)
    print("drift detected:")
    for p in added:
        print(f"  + {p}")
    for p in removed:
        print(f"  - {p}")
    print(
        "Run scripts/sync-openapi.ps1 to refresh the snapshot, then review and commit.",
        file=sys.stderr,
    )
    return 1


def _walk(obj, prefix="$"):
    """Yield every dotted path in a JSON document."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{prefix}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{prefix}[{i}]")
    else:
        yield prefix


if __name__ == "__main__":
    raise SystemExit(main())
