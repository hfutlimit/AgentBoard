# SPDX-License-Identifier: MIT
<#
.SYNOPSIS
    Tear down the AgentBoard stack (api, api-dotnet, web, mcp, db).

.PARAMETER WithVolumes
    Also drop the MariaDB and .NET BFF SQLite volumes. Without this flag
    the data survives the down/up cycle.
#>
[CmdletBinding()]
param(
    [switch]$WithVolumes
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
$compose = @('-f', 'config/docker/docker-compose.yml', '-f', 'config/docker/docker-compose.dev.yml')

if ($WithVolumes) {
    Write-Host '=== Stopping stack + removing volumes ==='
    docker compose @compose down --volumes
} else {
    Write-Host '=== Stopping stack (volumes preserved) ==='
    docker compose @compose down
}
