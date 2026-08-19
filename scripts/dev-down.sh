#!/usr/bin/env bash
# Tear down the AgentBoard stack (api, api-dotnet, web, mcp, db).
#
# Pass --volumes / -v to also drop the MariaDB and .NET BFF SQLite
# volumes; otherwise the data survives the down/up cycle.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${1:-}" == "-v" || "${1:-}" == "--volumes" ]]; then
    echo "=== Stopping stack + removing volumes ==="
    docker compose down --volumes
else
    echo "=== Stopping stack (volumes preserved) ==="
    docker compose down
fi
