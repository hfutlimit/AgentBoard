# AgentBoard MCP 服务启动脚本
# 由 NSSM 作为 Windows 服务调用（前台阻塞运行 FastMCP HTTP 服务）。
# 首次运行会自动创建 .venv 并安装依赖。
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

# ---- 加载同目录 .env ----
$EnvFile = Join-Path $Here '.env'
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line -match '^([^=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
        }
    }
}

# ---- 准备虚拟环境 ----
$Venv = Join-Path $Here '.venv'
if (-not (Test-Path $Venv)) {
    Write-Host "[mcp] 创建虚拟环境 .venv ..."
    python -m venv $Venv
    & "$Venv\Scripts\pip.exe" install --upgrade pip
    & "$Venv\Scripts\pip.exe" install -r (Join-Path $Here 'requirements.txt')
}

Write-Host "[mcp] 启动 agentboard.mcp_server (transport=http, host 127.0.0.1, port $($env:AGENTBOARD_MCP_PORT or '8001'))"
& "$Venv\Scripts\python.exe" -m agentboard.mcp_server
