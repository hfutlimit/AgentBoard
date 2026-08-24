#!/usr/bin/env bash
# Tear down the AgentBoard stack (api, api-dotnet, web, mcp, db).
#
# Pass --volumes / -v to also drop the MariaDB and .NET BFF SQLite
# volumes; otherwise the data survives the down/up cycle.

set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f config/docker/docker-compose.yml -f config/docker/docker-compose.dev.yml)

if [[ "${1:-}" == "-v" || "${1:-}" == "--volumes" ]]; then
    echo "=== Stopping stack + removing volumes ==="
    "${COMPOSE[@]}" down --volumes
else
    echo "=== Stopping stack (volumes preserved) ==="
    "${COMPOSE[@]}" down
fi
