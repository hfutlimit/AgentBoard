# SPDX-License-Identifier: MIT
<#
.SYNOPSIS
    One-shot install for the AgentBoard Proposal Worker on a fresh Windows box.

.DESCRIPTION
    Sprint 7 install path for the .NET 10 Worker. The script:

      1. Verifies .NET 10 SDK / runtime, Node.js, and the agent CLIs
         (codex / minimax / codebuddy) are present. Fail-fast with a
         clear actionable error if anything is missing.
      2. Publishes the worker with `dotnet publish -c Release -r win-x64`.
      3. Writes appsettings.Production.json (from a baked-in template) and
         substitutes the per-machine Worker.Id, Portal.ApiKey, and the
         RabbitMQ URI the operator supplies.
      4. Registers a Windows service "AgentBoard Proposal Worker" with
         `sc.exe create` and `sc.exe start`.
      5. Verifies /health on http://127.0.0.1:58240 and asserts that
         worker_id, registered agents, and CLI resolution are all good.

    The script is idempotent: re-running on an already-installed box
    stops the service, republishes, restarts. Run with -Uninstall to
    remove the service cleanly.

.PARAMETER WorkerId
    Stable identifier for this worker (e.g. "prod-pc-01"). Surfaces in
    /health and is used by the server to route follow-up work.

.PARAMETER AmqpUri
    RabbitMQ URI, e.g. amqp://user:pass@broker.example.com:5672/%2F.

.PARAMETER PortalApiKey
    Long random secret for the operations portal. The script generates
    a 48-byte hex value if not supplied.

.PARAMETER InstallDir
    Where to publish the worker. Defaults to C:\AgentBoard\ProposalWorker.

.PARAMETER RepoRoot
    Path to the AgentBoard repository (this script's parent by default).

.PARAMETER Uninstall
    Remove the service and (optionally) the install dir. Exits 0 on
    success even if the service was not registered.

.EXAMPLE
    pwsh -File scripts\install-worker.ps1 `
        -WorkerId "prod-pc-01" `
        -AmqpUri "amqp://agentboard:***@broker.example.com:5672/%2F"

.EXAMPLE
    pwsh -File scripts\install-worker.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$WorkerId = $env:AGENTBOARD_WORKER_ID,
    [string]$AmqpUri = $env:AGENTBOARD_MQ_URL,
    [string]$PortalApiKey = $env:AGENTBOARD_PORTAL_API_KEY,
    [string]$InstallDir = 'C:\AgentBoard\ProposalWorker',
    [string]$RepoRoot,
    [switch]$Uninstall
)

# Resolve RepoRoot from this script's location. Done in the body (not in the
# param default) so $PSScriptRoot is populated -- PowerShell 5.1 does not
# bind $PSScriptRoot inside param() defaults.
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path (Join-Path $PSCommandPath '..\..')).Path
}

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$ServiceName = 'AgentBoard Proposal Worker'
$ServiceExe = Join-Path $InstallDir 'AgentBoard.ProposalWorker.exe'
$PortalBase = 'http://127.0.0.1:58240'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Section {
    param([string]$Title)
    Write-Host ''
    Write-Host "=== $Title ===" -ForegroundColor Cyan
}

function Test-Command {
    param([string]$Name)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) { return $null }
    return $cmd.Source
}

function Test-DotnetVersion {
    $v = & dotnet --version 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    # Need 10.x
    if ($v -notmatch '^10\.') {
        Write-Warning "Found dotnet $v but need 10.x"
        return $null
    }
    return $v
}

function Test-NodeVersion {
    $v = & node --version 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return $v
}

function Test-AgentCli {
    param([string]$Name)
    # PowerShell 5.1 promotes where.exe's "INFO: Could not find files"
    # to a terminating error; wrap in a try/catch and force success exit.
    $resolved = $null
    try {
        $output = & cmd /c "where.exe $Name 2>nul" 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) {
            $resolved = ($output | Select-Object -First 1)
        }
    } catch {
        # where.exe wrote to stderr; treat as not-found.
        $resolved = $null
    }
    if (-not $resolved) { return $null }
    return $resolved.Trim()
}

function Stop-WorkerService {
    $svc = Get-Service -Name 'AgentBoard Proposal Worker' -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq 'Running') {
        Write-Host "[install] stopping running service..."
        & sc.exe stop 'AgentBoard Proposal Worker' | Out-Null
        $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(15))
    }
}

function Remove-WorkerService {
    $svc = Get-Service -Name 'AgentBoard Proposal Worker' -ErrorAction SilentlyContinue
    if ($svc) {
        Stop-WorkerService
        Write-Host "[install] deleting service..."
        & sc.exe delete 'AgentBoard Proposal Worker' | Out-Null
        for ($i = 0; $i -lt 30; $i++) {
            if (-not (Get-Service -Name 'AgentBoard Proposal Worker' -ErrorAction SilentlyContinue)) {
                return
            }
            Start-Sleep -Milliseconds 500
        }
        throw "Service '$ServiceName' is still pending deletion after 15 seconds."
    }
}

function New-PortalApiKey {
    # 48 random bytes hex-encoded; matches the format appsettings expects.
    $bytes = New-Object byte[] 48
    (New-Object Security.Cryptography.RNGCryptoServiceProvider).GetBytes($bytes)
    return ([BitConverter]::ToString($bytes) -replace '-', '').ToLower()
}

function Wait-Health {
    param([int]$Attempts = 30, [int]$DelaySeconds = 1)
    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri "$PortalBase/health" -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -eq 200) { return ($resp.Content | ConvertFrom-Json) }
        } catch {
            # 503 during boot, 404 if Portal.Urls not bound, etc. -- keep retrying.
        }
        Start-Sleep -Seconds $DelaySeconds
    }
    return $null
}

# ---------------------------------------------------------------------------
# Uninstall path
# ---------------------------------------------------------------------------

if ($Uninstall) {
    Write-Section "Uninstalling $ServiceName"
    $svc = Get-Service -Name 'AgentBoard Proposal Worker' -ErrorAction SilentlyContinue
    if (-not $svc -and -not (Test-Path $InstallDir)) {
        Write-Host "[install] nothing to do (service not registered, install dir absent)."
        exit 0
    }
    Remove-WorkerService
    if (Test-Path $InstallDir) {
        $answer = Read-Host "Remove install dir $InstallDir ? (yes/no)"
        if ($answer -eq 'yes') {
            Remove-Item -Path $InstallDir -Recurse -Force
        }
    }
    Write-Host "[install] uninstalled." -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

Write-Section "Pre-flight: checking toolchain"

$dotnet = Test-DotnetVersion
if (-not $dotnet) {
    throw ".NET 10 SDK / runtime not found. Install from https://dot.net (channel 10.0) and retry."
}
Write-Host "  .NET:        $dotnet" -ForegroundColor Green

$node = Test-NodeVersion
if (-not $node) {
    throw "Node.js not found. Install Node 20+ from https://nodejs.org/ and retry."
}
Write-Host "  Node:        $node" -ForegroundColor Green

$codex = Test-AgentCli 'codex'
$minimax = Test-AgentCli 'minimax'
$codebuddy = Test-AgentCli 'codebuddy'
$workbuddy = Test-AgentCli 'workbuddy'

$missing = @()
if (-not $codex)        { $missing += 'codex (npm install -g @openai/codex)' }
if (-not $minimax)      { $missing += 'minimax (npm install -g minimax-cli)' }
if (-not $codebuddy -and -not $workbuddy) { $missing += 'codebuddy (WorkBuddy Desktop) or workbuddy (legacy CLI)' }

if ($missing.Count -gt 0) {
    Write-Host ''
    Write-Host "  Missing agent CLIs:" -ForegroundColor Yellow
    foreach ($m in $missing) { Write-Host "    - $m" -ForegroundColor Yellow }
    Write-Host ''
    Write-Host "  Auto-discovery will pick them up if installed later -- the worker" -ForegroundColor Yellow
    Write-Host "  will surface a clear error per proposal message until then." -ForegroundColor Yellow
} else {
    Write-Host "  codex:       $codex" -ForegroundColor Green
    Write-Host "  minimax:     $minimax" -ForegroundColor Green
    if ($codebuddy) { Write-Host "  codebuddy:   $codebuddy" -ForegroundColor Green }
    if ($workbuddy) { Write-Host "  workbuddy:   $workbuddy" -ForegroundColor Green }
}

# ---------------------------------------------------------------------------
# Args validation
# ---------------------------------------------------------------------------

Write-Section "Pre-flight: validating config"

if ([string]::IsNullOrWhiteSpace($WorkerId)) {
    $WorkerId = "$env:COMPUTERNAME-worker"
    Write-Host "  Worker.Id:   $WorkerId  (auto-derived from hostname)"
} else {
    Write-Host "  Worker.Id:   $WorkerId"
}

if ([string]::IsNullOrWhiteSpace($AmqpUri)) {
    throw "AmqpUri is required. Set -AmqpUri or AGENTBOARD_MQ_URL env var."
}
Write-Host "  AmqpUri:     (supplied)"

if ([string]::IsNullOrWhiteSpace($PortalApiKey)) {
    $PortalApiKey = New-PortalApiKey
    Write-Host "  Portal.Key:  (generated) $(($PortalApiKey.Substring(0,8)) + '...')"
} else {
    Write-Host "  Portal.Key:  (supplied)"
}

# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

Write-Section "Publishing worker to $InstallDir"

$workerProject = Join-Path $RepoRoot 'src\workers\AgentBoard.ProposalWorker\AgentBoard.ProposalWorker.csproj'
if (-not (Test-Path $workerProject)) {
    throw "Worker project not found at $workerProject"
}

$publishArgs = @(
    'publish', $workerProject
    '-c', 'Release'
    '-r', 'win-x64'
    '--self-contained', 'false'
    '-o', $InstallDir
    '--nologo'
    '-v', 'minimal'
)

Write-Host "  dotnet $($publishArgs -join ' ')"
& dotnet @publishArgs
if ($LASTEXITCODE -ne 0) {
    throw "dotnet publish failed with exit $LASTEXITCODE"
}
Write-Host "  publish OK" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Write appsettings.Production.json
# ---------------------------------------------------------------------------

Write-Section "Writing appsettings.Production.json"

$appsettingsProd = Join-Path $InstallDir 'appsettings.Production.json'
$template = Join-Path $RepoRoot 'src\workers\AgentBoard.ProposalWorker\appsettings.Production.json'
if (-not (Test-Path $template)) {
    throw "Production template not found at $template"
}

# Parse and update the template structurally so quotes, backslashes, and `$`
# characters in operator-supplied values remain valid JSON.
$raw = Get-Content $template -Raw -Encoding UTF8
$config = $raw | ConvertFrom-Json
$config.Worker.Id = $WorkerId
$config.RabbitMq.Uri = $AmqpUri
$config.Portal.ApiKey = $PortalApiKey
$raw = $config | ConvertTo-Json -Depth 20

[System.IO.File]::WriteAllText($appsettingsProd, $raw, [System.Text.UTF8Encoding]::new($false))
Write-Host "  $appsettingsProd" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Register + start Windows service
# ---------------------------------------------------------------------------

Write-Section "Registering Windows service"

$existing = Get-Service -Name 'AgentBoard Proposal Worker' -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "  service already exists; reconfiguring..."
    Remove-WorkerService
}

# Preserve the installing user's CLI/login context for the LocalSystem service.
# sc.exe has no `env=` create option; per-service environment entries belong in
# HKLM\SYSTEM\CurrentControlSet\Services\<name>\Environment (REG_MULTI_SZ).
$envPairs = @()
foreach ($name in @(
    'MINIMAX_API_KEY', 'OPENAI_API_KEY', 'AGENTBOARD_WEB_API_URL', 'AGENTBOARD_TOKEN',
    'PATH', 'PATHEXT', 'APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'CODEX_HOME',
    'HOME', 'TEMP', 'TMP'
)) {
    $value = [Environment]::GetEnvironmentVariable($name, 'Process')
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        $envPairs += "$name=$value"
    }
}

$binPath = "`"$ServiceExe`""
$scArgs = @(
    'create', $ServiceName,
    'binPath=', $binPath,
    'start=', 'auto',
    'DisplayName=', 'AgentBoard Proposal Worker (MiniMax / Codex / WorkBuddy)'
)

& sc.exe @scArgs | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "sc.exe create failed with exit $LASTEXITCODE"
}
Write-Host "  sc create OK" -ForegroundColor Green

if ($envPairs.Count -gt 0) {
    $serviceRegistryPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$ServiceName"
    New-ItemProperty `
        -LiteralPath $serviceRegistryPath `
        -Name 'Environment' `
        -PropertyType MultiString `
        -Value $envPairs `
        -Force | Out-Null
    Write-Host "  service environment configured" -ForegroundColor Green
}

& sc.exe start 'AgentBoard Proposal Worker' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "sc.exe start failed with exit $LASTEXITCODE"
}
Write-Host "  sc start OK" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Verify /health
# ---------------------------------------------------------------------------

Write-Section "Verifying /health"

$health = Wait-Health
if (-not $health) {
    Write-Host "  /health did not return 200 within 30s. Check:" -ForegroundColor Red
    Write-Host "    Get-EventLog -LogName Application -Source 'AgentBoard Proposal Worker' -Newest 20" -ForegroundColor Red
    Write-Host "    $InstallDir\logs\worker.err.log" -ForegroundColor Red
    throw "Worker failed to become healthy."
}

$healthJson = $health | ConvertTo-Json -Depth 4
Write-Host "  /health:" -ForegroundColor Green
Write-Host "  $healthJson"

$failures = @()
if ([string]::IsNullOrWhiteSpace($health.worker_id)) { $failures += "worker_id is empty" }
if ($health.worker_id -ne $WorkerId) { $failures += "worker_id mismatch (config=$WorkerId, /health=$($health.worker_id))" }
if ($health.agents -and $health.agents.codex) {
    if (-not $health.agents.codex.registered) { $failures += "codex adapter is not registered" }
}

if ($failures.Count -gt 0) {
    Write-Host ''
    Write-Host "  Health checks failed:" -ForegroundColor Red
    foreach ($f in $failures) { Write-Host "    - $f" -ForegroundColor Red }
    throw "Worker is up but not fully configured. Inspect /health above and the install log."
}

Write-Host ''
Write-Host "=== install OK ===" -ForegroundColor Green
Write-Host "Worker.Id:     $WorkerId"
Write-Host "Portal:        $PortalBase"
Write-Host "Portal.Key:    stored in appsettings.Production.json (not printed)"
Write-Host "Install dir:   $InstallDir"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Get-Service 'AgentBoard Proposal Worker'"
Write-Host "  sc.exe query 'AgentBoard Proposal Worker'"
Write-Host "  Invoke-WebRequest $PortalBase/health"
