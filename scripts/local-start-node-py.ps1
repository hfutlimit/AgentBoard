# =============================================================================
# 本地部署 AgentBoard Python Worker（连接远程 prod API）
#
# 启动三个常驻进程（DB 轮询模式，未配置 AGENTBOARD_MQ_URL 时自动回退）：
#   proposal : Proposal Worker，AGENTBOARD_WORKER_AGENT_COMMANDS 配置多 agent
#              通道 + AGENTBOARD_WORKER_AGENT_ROUTING 按 handler.name 路由
#              （clarify/ticket → minimax 快速决策；story/review → codebuddy MCP）
#   workflow : Workflow 分配器 Worker（task reviewer 自动指派等，不调 agent）
#   portal   : 本机配置台 UI（127.0.0.1:18240 免登录），可配置项目 cwd / Agent 池
#
# 配置文件：tmp/agentboard-worker/.env.worker（gitignored，含密钥勿提交）
# 日志    ：logs/worker-py-<name>-<stamp>.{out,err}.log
# PID     ：logs/worker-py.pids（scripts/local-stop-node-py.ps1 使用）
# =============================================================================
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root 'tmp\agentboard-worker\.env.worker'
if (-not (Test-Path $EnvFile)) {
    throw "缺少配置文件 $EnvFile —— 按 scripts/local-start-node-py.ps1 头部说明创建"
}

# ---- 防重复启动 ----
$PidFile = Join-Path $Root 'logs\worker-py.pids'
if (Test-Path $PidFile) {
    $alive = @()
    foreach ($line in Get-Content $PidFile -Encoding UTF8) {
        if ($line -match '^([^=]+)=(\d+)$') {
            $aliveId = [int]$Matches[2]
            $p = Get-Process -Id $aliveId -ErrorAction SilentlyContinue
            if ($p) { $alive += $Matches[1] }
        }
    }
    if ($alive.Count -gt 0) {
        throw "已有 worker 在运行（$($alive -join ', ')）。先执行 scripts\local-stop-node-py.ps1 再启动。"
    }
}

# ---- 加载配置（KEY=VALUE，# 开头为注释；值原样保留含引号模板）----
Get-Content $EnvFile -Encoding UTF8 | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        Set-Item -Path ("Env:" + $Matches[1]) -Value $Matches[2].Trim()
    }
}
if (-not $env:AGENTBOARD_API_URL -or -not $env:AGENTBOARD_WORKER_TOKEN) {
    throw ".env.worker 缺少 AGENTBOARD_API_URL / AGENTBOARD_WORKER_TOKEN"
}

# Codex's AgentBoard MCP entry uses this exact bearer-token variable.  Fresh
# worker machines normally only have AGENTBOARD_WORKER_TOKEN in .env.worker;
# bridge it for the child CLI without requiring a manual user-level env edit.
if (-not $env:AgentBoard_Api_Key) {
    $mcpToken = if ($env:AGENTBOARD_MCP_TOKEN) {
        $env:AGENTBOARD_MCP_TOKEN
    } else {
        $env:AGENTBOARD_WORKER_TOKEN
    }
    if ($mcpToken) { Set-Item -Path 'Env:AgentBoard_Api_Key' -Value $mcpToken }
}

# ---- 生成本机 MCP 配置（不写入仓库，避免新 Worker 因缺少 tmp 文件而只能运行 CLI）----
$McpConfig = Join-Path $Root 'tmp\mcp-prod.json'
if (-not (Test-Path $McpConfig)) {
    $mcpUrl = ($env:AGENTBOARD_MCP_URL)
    if (-not $mcpUrl) { $mcpUrl = ($env:AGENTBOARD_API_URL.TrimEnd('/') + '/mcp') }
    $mcp = @{ mcpServers = @{ agentboard = @{ transport = 'http'; url = $mcpUrl } } }
    $mcp | ConvertTo-Json -Depth 5 | Set-Content -Path $McpConfig -Encoding UTF8
    Write-Host "已生成 MCP 配置：$McpConfig -> $mcpUrl"
}
$env:AGENTBOARD_MCP_CONFIG = $McpConfig

# ---- Python 运行环境 ----
$env:PYTHONPATH = Join-Path $Root 'src\backend-fastapi'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$Py = (Get-Command python).Source

$LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$pids = @()

function Start-PyWorker([string]$Name, [string[]]$ArgList) {
    $out = Join-Path $LogDir "worker-py-$Name-$Stamp.out.log"
    $err = Join-Path $LogDir "worker-py-$Name-$Stamp.err.log"
    $p = Start-Process -FilePath $script:Py -ArgumentList $ArgList `
        -WorkingDirectory $script:Root -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    Write-Host ("[{0}] pid={1}" -f $Name, $p.Id)
    Write-Host ("    log: {0}" -f $out)
    $script:pids += "$Name=$($p.Id)"
}

# ---- 进程 1：Proposal Worker · 多 agent 路由（minimax + codebuddy）----
Start-PyWorker 'proposal' @('-m', 'agentboard.processors', '--loop')

# ---- 进程 2：Workflow 分配器 Worker（评审指派，与 agent 通道无关）----
Start-PyWorker 'workflow' @('-m', 'agentboard.workflow_worker', '--loop')

# ---- 进程 3：本机配置台（项目 cwd / Agent 池配置 SPA，127.0.0.1 免登录）----
$PortalPort = if ($env:AGENTBOARD_PORTAL_PORT) { $env:AGENTBOARD_PORTAL_PORT } else { '18240' }
Start-PyWorker 'portal' @('-m', 'agentboard.processors_portal', '--host', '127.0.0.1', '--port', $PortalPort)

Set-Content -Path $PidFile -Value $pids -Encoding UTF8
Write-Host "`npid 文件：$PidFile"
Write-Host "停止：scripts\local-stop-node-py.ps1"
Write-Host "配置台：  http://127.0.0.1:$PortalPort"
