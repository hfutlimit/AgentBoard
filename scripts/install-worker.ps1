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
    # Service identity. Defaults to the installing user so the pre-flight
    # `where.exe` probes and the eventual service share the same PATH /
    # profile / npm-global layout. Override only if you have installed the
    # agent CLIs in a system-wide location reachable by the target account.
    #
    # IMPORTANT: this default requires a credential. If the operator runs
    # the script without supplying -ServiceCredential, the script will
    # *prompt* via Get-Credential instead of silently falling back to
    # LocalSystem (which is what the previous version did). The
    # fallback-to-LocalSystem path always produces an identity mismatch
    # with the pre-flight probes (CLIs are in user-only paths) — fix for
    # #7 in the 2026-08-28 review.
    [string]$ServiceAccount,
    [System.Management.Automation.PSCredential]$ServiceCredential,
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

# Captured at pre-flight so we can compare against the post-install service
# identity later. The bug this guards against (#4 in the 2026-08-28 review):
# pre-flight `where.exe` runs as the installing user, but a default sc.exe
# create runs the service as LocalSystem, so CLIs found at pre-flight are
# invisible to the service.
$PreFlightIdentity = whoami

# Service account is the target identity for the registered Windows service.
# Default: the actual current user from `whoami` (handles DOMAIN\user, local
# accounts, and builtin identities correctly; the previous `.\$env:USERNAME`
# forced local-account formatting and broke for domain users). When the
# operator did not pass -ServiceCredential and -ServiceAccount was not
# overridden, prompt for one. The previous version silently fell back to
# LocalSystem in that case, which always produced an identity mismatch with
# the pre-flight probes (#7 in the 2026-08-28 review).
if ([string]::IsNullOrWhiteSpace($ServiceAccount)) {
    $ServiceAccount = $PreFlightIdentity
}
$ResolvedServiceAccount = $ServiceAccount

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

function Test-IsElevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# 2026-08-29 review: the previous sc.exe branch only special-cased
# LocalSystem. If the operator passed -ServiceAccount 'NT AUTHORITY\LocalService'
# or 'NT AUTHORITY\NetworkService', the code entered the credential path
# and tried Get-Credential + Grant-ServiceLogonRight + password= for an
# account that has no password and never needs a custom user right. This
# helper centralises the canonical-form list of built-in service accounts
# so the sc.exe branch and any future check stay in sync.
function Test-IsBuiltinServiceAccount {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Account)

    $lc = $Account.ToLowerInvariant().Trim()
    return $lc -in @(
        'localsystem',
        'nt authority\system',
        '.\localsystem',
        'localservice',
        'nt authority\localservice',
        '.\localservice',
        'networkservice',
        'nt authority\networkservice',
        '.\networkservice'
    )
}

# Grant the Windows right "Log on as a service" (SeServiceLogonRight) to an
# account. Required for any non-builtin service account: sc.exe create does
# NOT verify the right, so a service can register fine and then fail to
# start with error 1069 / 1053 ("service did not respond"). This is the
# fresh-box blocker called out in the 2026-08-29 review: the previous
# installer happily created a service identity that could not log on.
#
# Implementation: LsaOpenPolicy + LsaAddAccountRights via advapi32 P/Invoke.
# Identical to the canonical LSA privilege-grant pattern; no external
# dependency (no `ntrights.exe`, no secedit temp file).
function Grant-ServiceLogonRight {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Account)

    if (-not (Test-IsElevated)) {
        throw "Grant-ServiceLogonRight requires an elevated PowerShell session (Run as Administrator). Re-launch the install from an elevated prompt."
    }

    $ntAccount = New-Object System.Security.Principal.NTAccount($Account)
    $sid = $ntAccount.Translate([System.Security.Principal.SecurityIdentifier])

    if (-not ('AgentBoard.Install.LsaHelpers' -as [type])) {
        $source = @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

namespace AgentBoard.Install {
    internal static class LsaHelpers {
        [StructLayout(LayoutKind.Sequential)]
        internal struct LSA_UNICODE_STRING {
            public ushort Length;
            public ushort MaximumLength;
            public IntPtr Buffer;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct LSA_OBJECT_ATTRIBUTES {
            public int Length;
            public IntPtr RootDirectory;
            public IntPtr ObjectName;
            public int Attributes;
            public IntPtr SecurityDescriptor;
            public IntPtr SecurityQualityOfService;
        }

        internal const int POLICY_CREATE_ACCOUNT = 0x00000010;
        internal const int POLICY_LOOKUP_NAMES   = 0x00000800;

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern uint LsaOpenPolicy(
            IntPtr SystemName,
            ref LSA_OBJECT_ATTRIBUTES ObjectAttributes,
            int DesiredAccess,
            out IntPtr PolicyHandle);

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern uint LsaAddAccountRights(
            IntPtr PolicyHandle,
            IntPtr AccountSid,
            LSA_UNICODE_STRING[] UserRights,
            int CountOfRights);

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern uint LsaClose(IntPtr PolicyHandle);

        public static void GrantServiceLogonRight(IntPtr sidPtr) {
            var oa = new LSA_OBJECT_ATTRIBUTES();
            oa.Length = Marshal.SizeOf(typeof(LSA_OBJECT_ATTRIBUTES));
            IntPtr policy;
            uint status = LsaOpenPolicy(IntPtr.Zero, ref oa, POLICY_CREATE_ACCOUNT | POLICY_LOOKUP_NAMES, out policy);
            if (status != 0) {
                throw new Win32Exception((int)status, "LsaOpenPolicy failed with NTSTATUS 0x" + status.ToString("X8"));
            }
            try {
                const string name = "SeServiceLogonRight";
                var bytes = Encoding.Unicode.GetBytes(name);
                IntPtr buffer = Marshal.AllocHGlobal(bytes.Length);
                try {
                    Marshal.Copy(bytes, 0, buffer, bytes.Length);
                    var rights = new LSA_UNICODE_STRING[1];
                    rights[0].Length = (ushort)bytes.Length;
                    rights[0].MaximumLength = (ushort)bytes.Length;
                    rights[0].Buffer = buffer;
                    status = LsaAddAccountRights(policy, sidPtr, rights, 1);
                    if (status != 0) {
                        throw new Win32Exception((int)status, "LsaAddAccountRights failed with NTSTATUS 0x" + status.ToString("X8"));
                    }
                } finally {
                    Marshal.FreeHGlobal(buffer);
                }
            } finally {
                LsaClose(policy);
            }
        }
    }
}
"@
        Add-Type -TypeDefinition $source -Language CSharp
    }

    $sidBytes = New-Object byte[] $sid.BinaryLength
    $sid.GetBinaryForm($sidBytes, 0)
    $sidPtr = [System.Runtime.InteropServices.Marshal]::AllocHGlobal($sidBytes.Length)
    try {
        [System.Runtime.InteropServices.Marshal]::Copy($sidBytes, 0, $sidPtr, $sidBytes.Length)
        [AgentBoard.Install.LsaHelpers]::GrantServiceLogonRight($sidPtr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::FreeHGlobal($sidPtr)
    }
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

# Administrator elevation is required for sc.exe create (with obj=), the
# SeServiceLogonRight grant, and the per-service registry write. Bail
# immediately if the operator launched from a non-elevated shell so we
# don't leave a half-configured service behind. 2026-08-29 review fix.
if (-not (Test-IsElevated)) {
    throw "This script must be run from an elevated PowerShell session (Run as Administrator). Re-launch from an elevated prompt and retry."
}
Write-Host "  elevated:    yes (UAC)" -ForegroundColor Green

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

# --- Service-account / pre-flight identity consistency (#4) -------------
# If the service will run as a different identity than the installing user,
# the CLIs found at pre-flight may be invisible at runtime. Detect this and
# fail fast with a precise message instead of silently producing a half-broken
# install.
#
# 2026-08-29 review fix: the previous ".\$env:USERNAME" string forced local
# account formatting ("MACHINE\user" → ".\user") and almost never matched
# the actual `whoami` output (e.g. "DOMAIN\jason" vs ".\jason"). Every
# fresh install tripped a false identity-mismatch warning + Continue anyway?
# prompt. Use the actual whoami string we already captured.
$installingUser = $PreFlightIdentity
if ($ResolvedServiceAccount -ne $installingUser -and $ResolvedServiceAccount -ne 'LocalSystem') {
    Write-Host ''
    Write-Host "  WARNING: pre-flight identity ($PreFlightIdentity) differs from" -ForegroundColor Yellow
    Write-Host "           service identity ($ResolvedServiceAccount)." -ForegroundColor Yellow
    Write-Host "           CLIs found above are only reachable by $installingUser." -ForegroundColor Yellow
    Write-Host "           Install the CLIs system-wide, or pass -ServiceAccount '$installingUser'." -ForegroundColor Yellow
    Write-Host ''
    $answer = Read-Host "Continue anyway? (yes/no)"
    if ($answer -ne 'yes') {
        throw "Aborted by operator to avoid identity mismatch."
    }
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
# Stop + remove existing service BEFORE publish
# ---------------------------------------------------------------------------
# 2026-08-29 review fix: the previous flow ran `dotnet publish` first and
# only stopped+removed the existing service after. On a re-install the
# running AgentBoard.ProposalWorker.exe holds a Windows file lock on its
# own binary and the publish either failed outright or left a half-
# overwritten install dir (mixed version). Stop + remove the existing
# service before publish so the install dir is not locked.
# 2026-08-29 review follow-up (round 7): detect the existing
# service EARLY (so the swap knows whether to do a fresh install or
# an upgrade) but do NOT stop it here. The live service can keep
# running while we publish to $StagingDir (a sibling dir, not
# touching $InstallDir). The actual stop+delete+recreate+start is
# moved inside the swap-protected try/finally so any failure during
# upgrade rolls back the binary + restarts the previous version.
Write-Section "Detecting existing service"
$existing = Get-Service -Name 'AgentBoard Proposal Worker' -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "  existing service found: $($existing.Status) — will be stopped atomically during the swap"
} else {
    Write-Host "  no existing service (fresh-box install)" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Publish to a STAGING directory, then atomic-swap into $InstallDir.
# 2026-08-29 review follow-up: the previous flow deleted the service
# before publish but the swap was a `Move-Item staging -> live` with
# no rollback path. If sc.exe create / start / health failed after
# the swap, the worker was permanently offline (live was clobbered
# but the new version was not running). Fix: publish to
# `$StagingDir`, snapshot the live install to `$BackupDir`, swap
# staging into live, preserve the data\ subdirectory (SQLite DB +
# execution logs) from the backup, start the service, verify
# /health, and roll back to the backup on any post-swap failure.
# ---------------------------------------------------------------------------
$StagingDir = "${InstallDir}.publish"
$BackupDir = "${InstallDir}.bak"

# Clean any leftover staging/backup from a previous failed run.
if (Test-Path $StagingDir) { Remove-Item -Path $StagingDir -Recurse -Force }
if (Test-Path $BackupDir) { Remove-Item -Path $BackupDir -Recurse -Force }

Write-Section "Publishing worker to staging ($StagingDir)"

$workerProject = Join-Path $RepoRoot 'src\workers\AgentBoard.ProposalWorker\AgentBoard.ProposalWorker.csproj'
if (-not (Test-Path $workerProject)) {
    throw "Worker project not found at $workerProject"
}

$publishArgs = @(
    'publish', $workerProject
    '-c', 'Release'
    '-r', 'win-x64'
    '--self-contained', 'false'
    '-o', $StagingDir
    '--nologo'
    '-v', 'minimal'
)

Write-Host "  dotnet $($publishArgs -join ' ')"
& dotnet @publishArgs
if ($LASTEXITCODE -ne 0) {
    # Publish failed; no state change. The script throws and exits
    # without touching the live install dir.
    throw "dotnet publish failed with exit $LASTEXITCODE (staging at $StagingDir; live install was not modified)"
}
Write-Host "  publish OK" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Atomic swap: live -> backup, staging -> live, preserve data\
# ---------------------------------------------------------------------------

# 2026-08-29 review follow-up (round 9): the round-8 design set
# $rollbackNeeded = $true BEFORE the first Move-Item, BUT the
# `try { ... } finally { if ($rollbackNeeded) { rollback } }`
# block started AFTER the swap (around line 623). A throw
# during stop / live->backup / staging->live / data-restore
# would exit the script BEFORE entering the try block, so the
# finally block (and Invoke-InstallRollback) never ran. Round-9
# fix: wrap the entire destructive section (stop + swap +
# sc config + env block + sc start + /health) in ONE try/finally
# so any failure inside it triggers rollback. The function
# Invoke-InstallRollback must be defined BEFORE the try block
# since PowerShell resolves function names at call time.
$rollbackNeeded = $true

# 2026-08-29 review follow-up (round 8): the service registration
# is now preserved across upgrades. If a previous service existed
# we just update its binPath to the new location; only fresh
# installs go through the full sc create path (which sets
# obj= / start= / DisplayName= / etc).
function Update-ServiceBinPath {
    param([string]$NewExe)
    $binPath = "`"$NewExe`""
    & sc.exe config $ServiceName binPath= $binPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "sc.exe config binPath failed with exit $LASTEXITCODE"
    }
    Write-Host "  sc config binPath OK -> $binPath" -ForegroundColor Green
}

try {

# 2026-08-29 review follow-up (round 8): the previous design
# stopped+deleted the service AFTER the swap, so the file lock on
# the old binary in $BackupDir was released by the swap itself
# (the file no longer existed in $InstallDir) — but `sc delete` is
# IRREVERSIBLE: if any post-swap step (sc create / sc start /
# /health) failed, the rollback helper would restore the old
# binaries into $InstallDir but the service REGISTRATION no longer
# existed, so `sc start` would fail with "service does not exist".
#
# New flow: stop the existing service BEFORE the swap (releases
# the file lock on the old binary so the Move-Item below is
# unblocked), then update the registration AFTER the swap with
# `sc config binPath=` (which preserves the service account /
# start type / display name from the previous install). Rollback
# just swaps the files back AND rolls binPath back; the service
# registration itself never goes away.
if ($existing) {
    Write-Section "Stopping existing service (releases file lock on old binary)"
    Stop-WorkerService
    Write-Host "  service stopped" -ForegroundColor Green
}

Write-Section "Atomic swap: staging -> live (preserving data\)"

$dataDir = Join-Path $InstallDir 'data'
$hasDataDir = Test-Path $dataDir

if (Test-Path $InstallDir) {
    Write-Host "  snapshotting live install to $BackupDir"
    Move-Item -Path $InstallDir -Destination $BackupDir -Force
}

Write-Host "  moving staging -> live"
Move-Item -Path $StagingDir -Destination $InstallDir -Force

if ($hasDataDir) {
    Write-Host "  restoring data\ from backup (preserves SQLite DB + execution logs)"
    Copy-Item -Path (Join-Path $BackupDir 'data') -Destination (Join-Path $InstallDir 'data') -Recurse -Force
} else {
    Write-Host "  no prior data\ directory; first-time install"
}
Write-Host "  swap OK" -ForegroundColor Green

# Rollback helper used by every post-swap failure point. Restores
# the snapshot directory as the live install, drops the failed
# (current) install, and (if the service registration existed
# before this run) restores the OLD binPath. Safe to call
# multiple times; idempotent.
function Invoke-InstallRollback {
    Write-Host ""
    Write-Host "  !!! Rolling back to previous version !!!" -ForegroundColor Red
    # Stop whatever is running under the service name (the failed
    # new install or the partially-restored old one). The 2-second
    # settle is a defensive sleep for services that need a moment
    # to release file handles after a Stop.
    try { & sc.exe stop $ServiceName 2>$null | Out-Null } catch { }
    Start-Sleep -Seconds 2
    try { & sc.exe stop $ServiceName 2>$null | Out-Null } catch { }
    # Drop the failed-swap install. The backup becomes the new live.
    if (Test-Path $InstallDir) {
        Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $BackupDir) {
        Move-Item -Path $BackupDir -Destination $InstallDir -Force
        Write-Host "  restored from backup" -ForegroundColor Green
    }
    # Restore the OLD binPath on the service registration, so the
    # SCM still points at the restored old binary. This closes the
    # round-7 review gap where the previous rollback only restored
    # files but not the SCM registration (sc delete had been called
    # pre-swap and was irreversible).
    if ($existing -and (Test-Path $ServiceExe)) {
        try {
            Update-ServiceBinPath -NewExe $ServiceExe
        } catch {
            Write-Host "  binPath rollback failed: $_" -ForegroundColor Red
        }
    }
    # Restart the previous (restored) service. Best-effort; if the
    # old service binary was uninstalled cleanly (e.g. fresh
    # install that never had a previous version) this is a no-op
    # and the operator runs the installer again.
    try {
        & sc.exe start $ServiceName 2>$null | Out-Null
    } catch { }
}
# ---------------------------------------------------------------------------

# Everything from the swap through /health is wrapped in
# try/finally so any failure (sc create, sc config, appsettings
# write, sc start, /health) rolls back the swap AND restarts the
# previous version. Note: the publish-to-staging step above
# happens BEFORE this try block, so a publish failure does not
# touch the live service or live binaries at all. $rollbackNeeded
# is set to $true earlier (BEFORE the first Move-Item) so the
# finally block actually runs even if the swap itself throws.
# Round-9 follow-up: the try block now starts BEFORE stop +
# swap + data-restore, not after. The round-8 scope started
# after the swap so a throw during Move-Item live->backup or
# Move-Item staging->live would exit the script before
# entering the try, leaving an inconsistent half-state with
# no rollback triggered.
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

# 2026-08-29 review follow-up (round 8): the previous design
# ALWAYS went through the full sc.exe create path. On an upgrade
# this sc delete + sc create sequence was irreversible — if any
# post-swap step (sc start, /health) failed, the rollback helper
# would restore the old binaries but the service REGISTRATION
# no longer existed, so sc start would fail with "service does
# not exist". Fix: on upgrade, just update binPath with
# `sc config binPath=`. The service account / start type /
# display name from the previous install are all preserved; the
# per-service env block is always re-applied so the operator can
# pick up newly-required env vars (e.g. a new API key) by
# re-running the install. Only fresh installs go through the
# full sc create path (which sets obj= / start= / DisplayName=).

# Always collect the env block (used for both fresh + upgrade paths).
# sc.exe has no `env=` create option; per-service environment entries
# belong in HKLM\SYSTEM\CurrentControlSet\Services\<name>\Environment
# (REG_MULTI_SZ).
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

if ($existing) {
    Write-Host "  upgrade path: preserving service registration, updating binPath only" -ForegroundColor Cyan
    Update-ServiceBinPath -NewExe $ServiceExe
} else {
    # Fresh install — full sc create.
    Write-Host "  fresh install: full sc create path"

    $binPath = "`"$ServiceExe`""
    $scArgs = @(
        'create', $ServiceName,
        'binPath=', $binPath,
        'start=', 'auto',
        'DisplayName=', 'AgentBoard Proposal Worker (MiniMax / Codex / WorkBuddy)'
    )

    # sc.exe needs `obj=` to run as anything other than LocalSystem. Default to
    # the installing user so pre-flight (which also runs as the installing user)
    # and the service see the same PATH / profile. Credentials are passed via
    # `-ServiceCredential` (SecureString); when missing, fall back to LocalSystem
    # only if the operator has installed CLIs in a system-wide location.
    # Three paths through the sc.exe branch:
    #   1. LocalSystem  — no obj=, no password=.
    #   2. Built-in service account (LocalService / NetworkService) — obj=
    #      with an empty password (Microsoft convention for these SIDs).
    #      No user-supplied credential, no SeServiceLogonRight grant (the
    #      account already has the right by virtue of being built-in).
    #   3. Normal user / domain account — Get-Credential for password,
    #      Grant-SeServiceLogonRight, obj= + password=.
    #
    # 2026-08-29 review fix: the previous code only special-cased LocalSystem
    # and entered the credential path for every other account, including the
    # built-in LocalService / NetworkService, which broke the install for
    # those targets.
    if (Test-IsBuiltinServiceAccount -Account $ResolvedServiceAccount) {
        if ($ResolvedServiceAccount -in @('LocalSystem', 'NT AUTHORITY\SYSTEM', '.\LocalSystem')) {
            Write-Host "  Service will run as LocalSystem. Make sure the agent CLIs are" -ForegroundColor Cyan
            Write-Host "  reachable from LocalSystem's PATH (e.g. system-wide npm-global, system PATH)." -ForegroundColor Cyan
        } else {
            # LocalService / NetworkService: sc.exe requires obj= with an
            # empty password. Microsoft convention for these built-in SIDs.
            Write-Host "  Service will run as the built-in $ResolvedServiceAccount account." -ForegroundColor Cyan
            Write-Host "  obj= set with empty password (Microsoft convention); no user credential required." -ForegroundColor Cyan
            $scArgs += @('obj=', $ResolvedServiceAccount, 'password=', '')
        }
    } else {
        if (-not $ServiceCredential) {
            Write-Host ''
            Write-Host "  Service will run as '' (matched your current user). sc.exe requires" -ForegroundColor Cyan
            Write-Host "  the account password to set obj=. Get-Credential will pop a Windows prompt." -ForegroundColor Cyan
            Write-Host "  (If the account does not have a password — e.g. a managed service account — pass" -ForegroundColor Cyan
            Write-Host "  -ServiceAccount LocalSystem instead and install the agent CLIs system-wide.)" -ForegroundColor Cyan
            Write-Host ''
            $ServiceCredential = Get-Credential -Message "Enter the password for $ResolvedServiceAccount (or Ctrl-C to abort)"
            if (-not $ServiceCredential) {
                throw "No credential supplied for $ResolvedServiceAccount; cannot create the service. Re-run with -ServiceCredential or -ServiceAccount LocalSystem."
            }
        }
        # Grant the service account the right to log on as a service.
        # sc.exe create does NOT verify SeServiceLogonRight; without this
        # grant, sc.exe start fails with 1053 ("service did not respond")
        # even though the service registered fine. This is the fresh-box
        # blocker called out in the 2026-08-29 review: the previous
        # installer produced a service that could register but never start.
        Write-Host "  granting SeServiceLogonRight to $ResolvedServiceAccount ..."
        Grant-ServiceLogonRight -Account $ResolvedServiceAccount
        Write-Host "  SeServiceLogonRight granted" -ForegroundColor Green
        $scArgs += @('obj=', $ResolvedServiceAccount, 'password=', $ServiceCredential.GetNetworkCredential().Password)
    }

    & sc.exe @scArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "sc.exe create failed with exit $LASTEXITCODE"
    }
    Write-Host "  sc create OK" -ForegroundColor Green
}

# Verify the service will actually run under the identity we asked for.
# `sc.exe qc` reports the configured `SERVICE_START_NAME` regardless of
# whether the service is currently running, so this works even before
# `sc start`.
$qc = & sc.exe qc $ServiceName 2>&1
$startNameLine = $qc | Where-Object { $_ -match 'SERVICE_START_NAME\s*:\s*(\S+)' } | Select-Object -First 1
if ($startNameLine) {
    $startName = ($startNameLine -replace '.*SERVICE_START_NAME\s*:\s*', '').Trim()
    Write-Host "  service identity:  $startName" -ForegroundColor Green
    if ($startName -ne $PreFlightIdentity -and
        $startName -notmatch 'LocalSystem' -and
        $PreFlightIdentity -notmatch 'LocalSystem') {
        Write-Host "  WARNING: pre-flight ran as $PreFlightIdentity but service will run as $startName." -ForegroundColor Yellow
        Write-Host "  The CLIs found above may not be reachable from the service." -ForegroundColor Yellow
    }
}

# Apply (or refresh) the per-service env block. Both fresh and
# upgrade paths run this so newly-required env vars (e.g. a new
# API key) are picked up on re-install.
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
if ($health.agents) {
    foreach ($prop in $health.agents.PSObject.Properties) {
        $agent = $prop.Name
        $entry = $prop.Value
        if (-not $entry.registered) {
            $failures += "$agent adapter is not registered"
            continue
        }
        # `ready` distinguishes "DI present" from "CLI actually executable".
        # The worker runs a --version probe at startup; if it failed, the
        # operator must fix the install before the worker can do real work
        # (#5 in the 2026-08-28 review).
        if ($entry.PSObject.Properties.Name -contains 'ready' -and -not $entry.ready) {
            $reason = if ($entry.PSObject.Properties.Name -contains 'ready_error') { $entry.ready_error } else { 'no reason given' }
            $failures += "$agent CLI is not ready: $reason"
        }
    }
}

if ($failures.Count -gt 0) {
    Write-Host ''
    Write-Host "  Health checks failed:" -ForegroundColor Red
    foreach ($f in $failures) { Write-Host "    - $f" -ForegroundColor Red }
    throw "Worker is up but not fully configured. Inspect /health above and the install log."
}


$rollbackNeeded = $false
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
}

finally {
    if ($rollbackNeeded) {
        Write-Host ""
        Write-Host "  !!! Post-swap step failed; rolling back to previous version !!!" -ForegroundColor Red
        try {
            Invoke-InstallRollback
        } catch {
            Write-Host "  rollback itself threw: $_" -ForegroundColor Red
        }
    } else {
        # Success path: drop the backup dir. The staging dir was
        # already moved into the live install, so nothing to do there.
        if (Test-Path $BackupDir) {
            Write-Host "  cleaning up backup dir: $BackupDir"
            Remove-Item -Path $BackupDir -Recurse -Force
        }
    }
}
