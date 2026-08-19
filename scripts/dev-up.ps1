# SPDX-License-Identifier: MIT
<#
.SYNOPSIS
    Bring up the full AgentBoard stack: FastAPI + .NET BFF + MCP + Web + MariaDB.

.DESCRIPTION
    Stage 0 default: both api (FastAPI) and api-dotnet (.NET 10) are
    reachable in parallel. This is the warm-up script for the dual-stack
    BFF development workflow.

.PARAMETER WithDotnet
    Include the api-dotnet container in the stack. Defaults to $true. In
    the dev compose override, api-dotnet is given the `dotnet` profile
    and is a no-op (the host runs `dotnet watch run` instead).
#>
[CmdletBinding()]
param(
    [switch]$WithDotnet = $true
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

Write-Host '=== Pulling base images ==='
docker compose pull db

Write-Host '=== Building custom images (api, api-dotnet, web) ==='
docker compose build

Write-Host '=== Starting stack ==='
if ($WithDotnet) {
    docker compose --profile dotnet up -d
} else {
    docker compose up -d
}

Start-Sleep -Seconds 5
Write-Host ''
Write-Host '=== Health check ==='
foreach ($pair in @(
    @{ Name = 'FastAPI  /api/health';  Url = 'http://localhost:18000/api/health' },
    @{ Name = '.NET BFF  /api/health';  Url = 'http://localhost:18000/api/health' },
    @{ Name = 'Web       /';           Url = 'http://localhost:28080/' }
)) {
    try {
        $r = Invoke-WebRequest -Uri $pair.Url -UseBasicParsing -TimeoutSec 5
        Write-Host ("  {0,-22} HTTP {1}" -f $pair.Name, $r.StatusCode)
    } catch {
        Write-Host ("  {0,-22} (unreachable: {1})" -f $pair.Name, $_.Exception.Message)
    }
}

Write-Host ''
Write-Host '=== Service endpoints ==='
@'
  FastAPI:    http://localhost:18000/api/health
  FastAPI:    http://localhost:18000/docs
  .NET BFF:   http://localhost:18000/api/health
  .NET BFF:   http://localhost:18000/openapi/v1.json
  Web:        http://localhost:28080/
  MCP:        http://localhost:18001/mcp
  MariaDB:    127.0.0.1:13306
'@ | Write-Host

Write-Host '=== Next ==='
Write-Host '  - Verify the contract:  python scripts/schema-drift-check.py'
Write-Host '  - Tail logs:            docker compose logs -f api api-dotnet'
Write-Host '  - Tear down:            scripts/dev-down.ps1'
