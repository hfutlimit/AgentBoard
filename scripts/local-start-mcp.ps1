# Local dev: start AgentBoard MCP (Streamable HTTP) on port 18001
$ErrorActionPreference = 'Stop'
$Root = 'D:\AI\Projects\AgentBoard'
Set-Location $Root

Get-Content .env | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line -match '^([^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
    }
}

$env:AGENTBOARD_MCP_TRANSPORT   = 'http'
$env:AGENTBOARD_MCP_HOST        = '127.0.0.1'
$env:AGENTBOARD_MCP_PORT        = '18001'
$env:AGENTBOARD_MCP_PATH        = '/mcp'
$env:AGENTBOARD_MCP_REQUIRE_AUTH = '0'
$env:AGENTBOARD_API_URL         = 'http://127.0.0.1:18000'

$logDir = Join-Path $Root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$out = Join-Path $logDir 'mcp.out.log'
$err = Join-Path $logDir 'mcp.err.log'

Write-Host "[mcp] starting on $($env:AGENTBOARD_MCP_HOST):$($env:AGENTBOARD_MCP_PORT)$($env:AGENTBOARD_MCP_PATH) -> logs/mcp.{out,err}.log"

$exe = Join-Path $Root '.venv\Scripts\python.exe'
$args = @('-m', 'agentboard.mcp_server')
$proc = Start-Process -FilePath $exe -ArgumentList $args -WorkingDirectory $Root `
    -RedirectStandardOutput $out -RedirectStandardError $err `
    -WindowStyle Hidden -PassThru
Write-Host "[mcp] pid=$($proc.Id)"
