#!/usr/bin/env bash
# Bring up the full AgentBoard stack: FastAPI + .NET BFF + MCP + Web + MariaDB.
#
# Stage 0 default: both api (FastAPI) and api-dotnet (.NET 10) are reachable.
# This is the warm-up script for the dual-stack BFF development workflow.

set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f config/docker/docker-compose.yml -f config/docker/docker-compose.dev.yml)

echo "=== Pulling base images ==="
"${COMPOSE[@]}" pull db

echo "=== Building custom images (api, api-dotnet, web) ==="
"${COMPOSE[@]}" build

echo "=== Starting stack (api, api-dotnet, web, mcp, db) ==="
"${COMPOSE[@]}" up -d

echo
echo "=== Health check ==="
sleep 5
echo "FastAPI /api/health:"
curl -s -o /dev/null -w "  HTTP %{http_code}\n" http://localhost:18000/api/health
echo ".NET BFF /api/health:"
curl -s -o /dev/null -w "  HTTP %{http_code}\n" http://localhost:18000/api/health || true
echo "Web /:"
curl -s -o /dev/null -w "  HTTP %{http_code}\n" http://localhost:28080/

echo
echo "=== Service endpoints ==="
cat <<EOF
  FastAPI:    http://localhost:18000/api/health
  FastAPI:    http://localhost:18000/docs
  .NET BFF:   http://localhost:18000/api/health
  .NET BFF:   http://localhost:18000/openapi/v1.json
  Web:        http://localhost:28080/
  MCP:        http://localhost:18001/mcp
  MariaDB:    127.0.0.1:13306
EOF

echo
echo "=== Next ==="
echo "  - Verify the contract: python scripts/schema-drift-check.py"
echo "  - Tail logs:           ${COMPOSE[*]} logs -f api api-dotnet"
echo "  - Tear down:           scripts/dev-down.sh"
