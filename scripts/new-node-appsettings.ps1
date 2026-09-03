# SPDX-License-Identifier: MIT
<#
.SYNOPSIS
    Generate appsettings.Production.json for an AgentBoard Node install.

.DESCRIPTION
    Reads the tracked production template
    (src/nodes/AgentBoard.Node/appsettings.Production.json), applies the
    per-machine values, mirrors the canonical "Node" section into the legacy
    "Worker" section, and writes the result.

    BOTH install paths go through this script:

      * scripts/install-node.ps1          (fully automated install)
      * the manual `dotnet publish` + `sc.exe create` path in
        src/nodes/AgentBoard.Node/README.md

    Why the mirroring lives here instead of a hand-maintained "Worker" block
    in the template:

      * Program.cs binds "Worker" first as the baseline and then layers
        "Node" on top, overriding only keys that are ACTUALLY PRESENT in the
        Node section. If the template carried both sections with full default
        values, the shipped Node defaults would drown out whatever an operator
        configured under Worker - the exact regression fixed in e097107.
      * A rollback to the previous binary (which only understands "Worker")
        must read the same configuration, not silently fall back to shipped
        defaults. Generating both from one source is what guarantees that.

    The script is idempotent: re-running it with the same parameters rewrites
    the file from the template, so no value accumulates across runs.

.PARAMETER WorkerId
    Stable per-machine node id (e.g. "prod-pc-01"). Surfaces in /health as
    worker_id and is what the server routes follow-up work with.

.PARAMETER AmqpUri
    RabbitMQ URI, e.g. amqp://user:pass@broker.example.com:5672/%2F.

.PARAMETER PortalApiKey
    Long random secret for the operations portal. Generated if not supplied.

.PARAMETER RepoRoot
    Path to the AgentBoard repository. Defaults to this script's parent.

.PARAMETER Template
    Explicit template path. Defaults to
    $RepoRoot\src\nodes\AgentBoard.Node\appsettings.Production.json.

.PARAMETER OutFile
    Where to write the result. Defaults to .\appsettings.Production.json.

.EXAMPLE
    pwsh -File scripts\new-node-appsettings.ps1 -WorkerId "prod-pc-01" `
        -AmqpUri "amqp://agentboard:***@broker.example.com:5672/%2F" `
        -OutFile C:\AgentBoard\Node\appsettings.Production.json

.EXAMPLE
    # Manual publish path: generate next to the published binaries, then
    # hand-edit the remaining placeholders before sc.exe create.
    pwsh -File scripts\new-node-appsettings.ps1 -OutFile C:\AgentBoard\Node\appsettings.Production.json
#>
[CmdletBinding()]
param(
    [string]$WorkerId = $env:AGENTBOARD_WORKER_ID,
    [string]$AmqpUri = $env:AGENTBOARD_MQ_URL,
    [string]$PortalApiKey = $env:AGENTBOARD_PORTAL_API_KEY,
    [string]$RepoRoot,
    [string]$Template,
    [string]$OutFile = (Join-Path (Get-Location) 'appsettings.Production.json')
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}
if ([string]::IsNullOrWhiteSpace($Template)) {
    $Template = Join-Path $RepoRoot 'src\nodes\AgentBoard.Node\appsettings.Production.json'
}
if (-not (Test-Path $Template)) {
    throw "Production template not found at $Template"
}

if ([string]::IsNullOrWhiteSpace($PortalApiKey)) {
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $PortalApiKey = ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
    Write-Host "Portal API key: generated a random 48-byte value." -ForegroundColor Yellow
}

# Parse structurally so quotes, backslashes and `$` in operator-supplied
# values stay valid JSON (same rule as the installer).
$config = (Get-Content $Template -Raw -Encoding UTF8) | ConvertFrom-Json

if (-not $config.Node) {
    throw "Template $Template has no 'Node' section - cannot generate a Node config."
}

# P7b: "Node" is the canonical section the current binary reads.
if (-not [string]::IsNullOrWhiteSpace($WorkerId)) {
    $config.Node.Id = $WorkerId
}
if (-not [string]::IsNullOrWhiteSpace($AmqpUri)) {
    $config.RabbitMq.Uri = $AmqpUri
}
if ($config.Portal) {
    $config.Portal.ApiKey = $PortalApiKey
}

# Mirror the resolved Node section into the legacy Worker section. Deep copy
# via JSON round-trip so the two cannot share object references.
$workerMirror = ($config.Node | ConvertTo-Json -Depth 20 | ConvertFrom-Json)
if ($config.PSObject.Properties.Name -contains 'Worker') {
    $config.Worker = $workerMirror
} else {
    $config | Add-Member -MemberType NoteProperty -Name 'Worker' -Value $workerMirror
}

$outDir = Split-Path -Parent $OutFile
if ($outDir -and -not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}

[System.IO.File]::WriteAllText(
    $OutFile,
    ($config | ConvertTo-Json -Depth 20),
    [System.Text.UTF8Encoding]::new($false))

Write-Host "Wrote $OutFile" -ForegroundColor Green
if ([string]::IsNullOrWhiteSpace($WorkerId) -or [string]::IsNullOrWhiteSpace($AmqpUri)) {
    Write-Host "Remaining REPLACE-WITH-* placeholders still need values before the node will register." -ForegroundColor Yellow
}
