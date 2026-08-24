# 将当前包安装为 Windows 服务（依赖 NSSM：https://nssm.cc）。
# 用法（在包目录内以管理员 PowerShell 运行）：
#   .\install-service.ps1                 # 使用默认参数（WebAPI 或 MCP 视脚本旁的配置而定）
# 参数可由调用方覆盖，例如：
#   .\install-service.ps1 -ServiceName AgentBoard-MCP -RunScript run-mcp.ps1 -DisplayName "AgentBoard MCP"
param(
    [string]$ServiceName  = 'AgentBoard-WebAPI',
    [string]$AppDir       = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [string]$RunScript    = 'run-webapi.ps1',
    [string]$DisplayName  = 'AgentBoard WebAPI',
    [string]$Description  = 'AgentBoard REST API (FastAPI/uvicorn)'
)

$nssm = 'nssm.exe'
if (-not (Get-Command $nssm -ErrorAction SilentlyContinue)) {
    Write-Error "未找到 nssm.exe。请下载 https://nssm.cc 并将其所在目录加入 PATH 后重试。"
    exit 1
}

$exe  = 'powershell.exe'
$args = "-ExecutionPolicy Bypass -NoProfile -File `"$RunScript`""

Write-Host "安装服务 $ServiceName ..."
& $nssm install $ServiceName $exe $args
& $nssm set $ServiceName AppDirectory $AppDir
& $nssm set $ServiceName DisplayName $DisplayName
& $nssm set $ServiceName Description $Description
& $nssm set $ServiceName Start SERVICE_AUTO_START
& $nssm set $ServiceName AppExit Default Restart
& $nssm start $ServiceName

Write-Host "完成。服务 $ServiceName 已安装并启动。可用 'nssm status $ServiceName' 查看状态。"
