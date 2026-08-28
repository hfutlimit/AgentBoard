# SPDX-License-Identifier: MIT
<#
.SYNOPSIS
    Pull the live FastAPI /openapi.json snapshot into the .NET contracts
    directory, then write a SHA-256 hash that CI can pin against.

.DESCRIPTION
    The dual-stack BFF keeps FastAPI as the contract source of truth: every
    public REST endpoint the .NET WebAPI serves must be 1:1 with the
    endpoint exposed by FastAPI. This script:

      1. Resolves the FastAPI base URL (env var or default).
      2. GETs the live /openapi.json document.
      3. Pretty-prints it (sorted keys) for diff stability.
      4. Writes src/backend-dotnet/contracts/openapi-v3.json.
      5. Writes src/backend-dotnet/contracts/openapi-v3.sha256.

.PARAMETER FastApiUrl
    Base URL of the FastAPI service. Defaults to
    http://127.0.0.1:18000 (the dev compose port). Override via
    -FastApiUrl or $env:AGENTBOARD_FASTAPI_URL.

.EXAMPLE
    pwsh scripts/sync-openapi.ps1
    pwsh scripts/sync-openapi.ps1 -FastApiUrl http://localhost:8000

.NOTES
    The .NET CI workflow runs schema-drift-check.py after this; if the
    generated sha256 differs from the committed one, the build fails.
#>
[CmdletBinding()]
param(
    [string]$FastApiUrl = $env:AGENTBOARD_FASTAPI_URL ?? 'http://127.0.0.1:18000',
    [string]$ContractsDir = (Join-Path $PSScriptRoot '..' 'src' 'backend-dotnet' 'contracts')
)

$ErrorActionPreference = 'Stop'

$openApiPath = "$FastApiUrl/openapi.json"
Write-Host "Fetching $openApiPath ..."

try {
    $response = Invoke-WebRequest -Uri $openApiPath -Method GET -UseBasicParsing -TimeoutSec 10
}
catch {
    Write-Error "Failed to fetch $openApiPath. Is FastAPI running? Start it with 'docker compose -f config/docker/docker-compose.yml up -d api' or 'uvicorn agentboard.api:app --port 8000'."
    exit 1
}

if ($response.StatusCode -ne 200) {
    Write-Error "FastAPI returned HTTP $($response.StatusCode)."
    exit 1
}

# Parse + pretty-print with stable key order so diffs are meaningful.
$json = $response.Content | ConvertFrom-Json
$pretty = ($json | ConvertTo-Json -Depth 50) -replace "`r`n", "`n"

if (-not (Test-Path $ContractsDir)) {
    New-Item -ItemType Directory -Path $ContractsDir -Force | Out-Null
}

$outJson = Join-Path $ContractsDir 'openapi-v3.json'
$outSha  = Join-Path $ContractsDir 'openapi-v3.sha256'
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($outJson, $pretty + "`n", $utf8NoBom)

# SHA-256 over the raw bytes (utf-8 no-BOM, matches schema-drift-check.py).
$hash = (Get-FileHash -Path $outJson -Algorithm SHA256).Hash.ToLower()
[System.IO.File]::WriteAllText($outSha, "$hash  openapi-v3.json`n", $utf8NoBom)

Write-Host "Wrote $outJson ($((Get-Item $outJson).Length) bytes)"
Write-Host "Wrote $outSha  ($hash)"
Write-Host ""
Write-Host "Next:"
Write-Host "  1. Review the diff:  git diff src/backend-dotnet/contracts/openapi-v3.json"
Write-Host "  2. Commit the snapshot: git add src/backend-dotnet/contracts/ && git commit -m 'chore(contracts): refresh OpenAPI snapshot'"
Write-Host "  3. (Optional) Regenerate the FastAPI client: pwsh scripts/generate-fastapi-client.ps1"
