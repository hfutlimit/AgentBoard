# Local dev: start AgentBoard Web (Angular SPA) on port 28080
$ErrorActionPreference = 'Stop'
$Root = 'D:\AI\Projects\AgentBoard'
Set-Location $Root

Get-Content .env | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line -match '^([^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
    }
}

$env:AGENTBOARD_WEB_PORT = '28080'
# The browser fetches via this URL; from same machine it goes to API directly
$env:AGENTBOARD_API_URL  = 'http://127.0.0.1:18000'

$logDir = Join-Path $Root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$out = Join-Path $logDir 'web.out.log'
$err = Join-Path $logDir 'web.err.log'

Write-Host "[web] starting on 127.0.0.1:$($env:AGENTBOARD_WEB_PORT) -> logs/web.{out,err}.log"

$exe = Join-Path $Root '.venv\Scripts\python.exe'
$args = @(
    '-m', 'uvicorn', 'agentboard.web_app:app',
    '--host', '127.0.0.1',
    '--port', $env:AGENTBOARD_WEB_PORT
)
$proc = Start-Process -FilePath $exe -ArgumentList $args -WorkingDirectory $Root `
    -RedirectStandardOutput $out -RedirectStandardError $err `
    -WindowStyle Hidden -PassThru
Write-Host "[web] pid=$($proc.Id)"
