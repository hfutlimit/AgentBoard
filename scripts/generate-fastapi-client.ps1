# SPDX-License-Identifier: MIT
<#
.SYNOPSIS
    Regenerate the FastAPI C# client (NSwag-generated) used by the .NET
    BFF to call into the internal AI subsystem.

.DESCRIPTION
    Reads src/backend-dotnet/contracts/openapi-v3.json and writes
    src/backend-dotnet/src/AgentBoard.Api/Clients/AgentBoardFastApiClient.cs.

    The generated file is committed to source so reviewers can see the
    diff when the FastAPI contract changes. The CI workflow asserts the
    committed file matches the freshly-generated file (see
    .github/workflows/dotnet-contract-check.yml).

.PARAMETER NswagPath
    Path to nswag.exe. Defaults to the global .NET tool location.

.EXAMPLE
    pwsh scripts/generate-fastapi-client.ps1
#>
[CmdletBinding()]
param(
    [string]$NswagPath = "$env:USERPROFILE\.dotnet\tools\nswag.exe"
)

$ErrorActionPreference = 'Stop'

$repoRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$snapshot   = Join-Path $repoRoot 'src/backend-dotnet/contracts/openapi-v3.json'
$output     = Join-Path $repoRoot 'src/backend-dotnet/src/AgentBoard.Api/Clients/AgentBoardFastApiClient.cs'
$namespace  = 'AgentBoard.Api.Clients'

# Allow nswag (shipped as net9.0) to run on .NET 10 hosts.
$env:DOTNET_ROLL_FORWARD = 'Major'

if (-not (Test-Path $NswagPath)) {
    Write-Error "nswag not found at $NswagPath. Install with: dotnet tool install --global NSwag.ConsoleCore"
}
if (-not (Test-Path $snapshot)) {
    Write-Error "Snapshot not found: $snapshot. Run scripts/sync-openapi.ps1 first."
}

$outputDir = Split-Path $output -Parent
if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}

Write-Host "Generating $output from $snapshot ..."

& $NswagPath openapi2csclient `
    /input:$snapshot `
    /output:$output `
    /namespace:$namespace `
    /operationGenerationMode:SingleClientFromOperationId `
    /className:AgentBoardFastApiClient `
    /generateClientInterfaces:true `
    /generateDtoTypes:true `
    /useHttpClientCreationMethod:true `
    /useBaseUrl:false

if ($LASTEXITCODE -ne 0) {
    Write-Error "nswag failed with exit code $LASTEXITCODE."
}

# NSwag emits whitespace-only indentation on a number of generated blank
# lines. Normalize it here so the committed artifact passes git diff --check
# and regeneration remains deterministic across Windows and Linux runners.
$generated = [System.IO.File]::ReadAllText($output)
$normalized = [System.Text.RegularExpressions.Regex]::Replace(
    $generated,
    '[ \t]+(?=\r?$)',
    '',
    [System.Text.RegularExpressions.RegexOptions]::Multiline
)
if ($normalized -ne $generated) {
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($output, $normalized, $utf8NoBom)
}

Write-Host "Wrote $output ($((Get-Item $output).Length) bytes)"
Write-Host ""
Write-Host "Next:"
Write-Host "  git diff src/backend-dotnet/src/AgentBoard.Api/Clients/AgentBoardFastApiClient.cs"
Write-Host "  git add src/backend-dotnet/ && git commit -m 'chore(clients): regenerate FastAPI client'"
