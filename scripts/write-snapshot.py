#!/usr/bin/env python3
"""One-off helper: normalize openapi-v3.json encoding and refresh the .sha256.

PowerShell's default file writer inserts CRLF line endings, which makes the
raw-bytes hash disagree with what Python reads back. This script writes
UTF-8 (no BOM, LF newlines) so the .sha256 the commit pins matches the
.bytes the script reads."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO / "dotnet" / "contracts" / "openapi-v3.json"
SHA = REPO / "dotnet" / "contracts" / "openapi-v3.sha256"


def main() -> int:
    if not SNAPSHOT.exists():
        print(f"error: {SNAPSHOT} not found", file=sys.stderr)
        return 2
    # Read, round-trip through json for stable key order, write with LF + no BOM.
    obj = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    data = (json.dumps(obj, indent=2) + "\n").encode("utf-8")
    SNAPSHOT.write_bytes(data)
    h = hashlib.sha256(data).hexdigest()
    SHA.write_text(f"{h}  openapi-v3.json\n", encoding="utf-8", newline="\n")
    print(f"wrote {SNAPSHOT} ({len(data)} bytes)")
    print(f"wrote {SHA} ({h})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
