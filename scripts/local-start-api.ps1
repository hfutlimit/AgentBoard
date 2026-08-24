# Local dev: start AgentBoard REST API on port 18000.
# Uses cmd.exe to detach from this PowerShell session.
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$AppRoot = Join-Path $Root 'src\backend-fastapi'
$env:PYTHONPATH = $AppRoot

# load .env into process env
Get-Content .env | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line -match '^([^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
    }
}

# override ports to avoid clashes with AI-Search (8000) and Apache (8080)
$env:AGENTBOARD_API_PORT = '18000'

$logDir = Join-Path $Root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$out = Join-Path $logDir 'api.out.log'
$err = Join-Path $logDir 'api.err.log'

Write-Host "[api] starting on 127.0.0.1:$($env:AGENTBOARD_API_PORT) -> logs/api.{out,err}.log"

# Use Start-Process so the child is fully detached; the script returns immediately.
$exe = Join-Path $Root '.venv\Scripts\python.exe'
$args = @(
    '-m', 'uvicorn', 'agentboard.api:app',
    '--host', '127.0.0.1',
    '--port', $env:AGENTBOARD_API_PORT
)
$proc = Start-Process -FilePath $exe -ArgumentList $args -WorkingDirectory $Root `
    -RedirectStandardOutput $out -RedirectStandardError $err `
    -WindowStyle Hidden -PassThru
Write-Host "[api] pid=$($proc.Id)"
