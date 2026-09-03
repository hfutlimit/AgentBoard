# 停止 local-start-node-py.ps1 启动的全部 worker 进程（读取 logs\worker-py.pids）。
#
# 说明：进程被强杀时若恰有提案处于 analyzing，租约到期后由 reclaim-stale
# 自动回退 queued 重投（默认 1800s），无需人工干预。
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $Root 'logs\worker-py.pids'
if (-not (Test-Path $PidFile)) {
    Write-Host "未找到 $PidFile —— 没有已记录的 worker 进程。"
    return
}

foreach ($line in Get-Content $PidFile -Encoding UTF8) {
    if ($line -notmatch '^([^=]+)=(\d+)$') { continue }
    $name = $Matches[1]
    $procId = [int]$Matches[2]
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($p) {
        Stop-Process -Id $procId -Force
        Write-Host ("[{0}] pid={1} 已停止" -f $name, $procId)
    }
    else {
        Write-Host ("[{0}] pid={1} 已不存在（跳过）" -f $name, $procId)
    }
}
Remove-Item $PidFile -Force
Write-Host '全部 worker 已停止。'
