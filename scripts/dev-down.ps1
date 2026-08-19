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

if ($WithVolumes) {
    Write-Host '=== Stopping stack + removing volumes ==='
    docker compose down --volumes
} else {
    Write-Host '=== Stopping stack (volumes preserved) ==='
    docker compose down
}
