# AgentBoard WebAPI 启动脚本
# 由 NSSM 作为 Windows 服务调用（前台阻塞运行 uvicorn）。
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
    Write-Host "[webapi] 创建虚拟环境 .venv ..."
    python -m venv $Venv
    & "$Venv\Scripts\pip.exe" install --upgrade pip
    & "$Venv\Scripts\pip.exe" install -r (Join-Path $Here 'requirements.txt')
}

$Port = if ($env:AGENTBOARD_API_PORT) { $env:AGENTBOARD_API_PORT } else { '8000' }
Write-Host "[webapi] 启动 uvicorn agentboard.api:app on 127.0.0.1:$Port"
& "$Venv\Scripts\python.exe" -m uvicorn agentboard.api:app --host 127.0.0.1 --port $Port
