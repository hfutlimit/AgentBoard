<#
Local .NET Worker deploy for KnowledgeVault E2E (id=8).

Pipeline:
  1. Load tokens from tmp/remote_service_users.json (wb_main + codex_main + admin).
  2. Inject tokens into appsettings.Local.json (StartToken / per-agent AgentBoardToken / Portal.ApiKey).
  3. dotnet build src/workers/AgentBoard.ProposalWorker
  4. Set env (MINIMAX_API_KEY, DOTNET_ENVIRONMENT=Local).
  5. Start the worker in background, capture pid + out/err log paths.

Tokens are NEVER echoed; they are written only to the on-disk appsettings.Local.json (next to the
worker exe after `dotnet build`).
#>
$ErrorActionPreference = 'Stop'
# Auto-detect repo root from the script's own location (scripts/..) so the
# deploy works on any drive/checkout path — previously hardcoded to
# 'D:\AI\Projects\AgentBoard', which broke on other machines. $PSScriptRoot is
# the directory containing this .ps1.
$Root = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { 'D:\AI\Projects\AgentBoard' }
$WorkerRoot = Join-Path $Root 'src\workers\AgentBoard.ProposalWorker'
$BinDir = Join-Path $WorkerRoot 'bin\Debug\net10.0'
$SettingsPath = Join-Path $WorkerRoot 'appsettings.Local.json'
$SecretsPath = Join-Path $Root 'tmp\remote_service_users.json'
$LogDir = Join-Path $Root 'logs'

if (-not (Test-Path $SettingsPath)) {
    $TemplatePath = Join-Path $WorkerRoot 'appsettings.Local.template.json'
    if (Test-Path $TemplatePath) {
        Copy-Item -Path $TemplatePath -Destination $SettingsPath -Force
        Write-Host "[deploy] bootstrapped $SettingsPath from template (git-ignored local secret file)"
    } else {
        throw "Missing $SettingsPath and no template at $TemplatePath — nothing to deploy onto."
    }
}
$template = Get-Content $SettingsPath -Raw
$deploy_ph = 'PLACEHOLDER_REPLACED_BY_DEPLOY_SCRIPT'
if (-not $template.Contains($deploy_ph)) {
    Write-Host "[deploy] $SettingsPath has no placeholders (already filled); skipping token injection"
} else {
    if (-not (Test-Path $SecretsPath)) {
        throw "Missing $SecretsPath. Run: .venv\Scripts\python.exe tmp\seed_remote_service_users.py"
    }
    $secrets = Get-Content $SecretsPath -Raw | ConvertFrom-Json
    $startupToken = $secrets.admin_token
    $wbToken = $secrets.users.wb_main.api_key
    $codexToken = $secrets.users.codex_main.api_key
    $portalApiKey = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')

    Write-Host "[deploy] loading template -> $SettingsPath"
    # settings 模板里 4 个 PLACEHOLDER 出现顺序：WorkBuddy → Codex → StartupToken → Portal.ApiKey
    # 4 次 inline replace 必须按这个顺序给对应 token，否则会错位。
    $idx = $template.IndexOf($deploy_ph)
    $template = $template.Substring(0, $idx) + $wbToken + $template.Substring($idx + $deploy_ph.Length)
    $idx = $template.IndexOf($deploy_ph)
    $template = $template.Substring(0, $idx) + $codexToken + $template.Substring($idx + $deploy_ph.Length)
    $idx = $template.IndexOf($deploy_ph)
    $template = $template.Substring(0, $idx) + $startupToken + $template.Substring($idx + $deploy_ph.Length)
    $idx = $template.IndexOf($deploy_ph)
    $template = $template.Substring(0, $idx) + $portalApiKey + $template.Substring($idx + $deploy_ph.Length)
    [System.IO.File]::WriteAllText($SettingsPath, $template, (New-Object System.Text.UTF8Encoding $false))

    # 兜底确认
    $final = Get-Content $SettingsPath -Raw
    $placeholderCount = ([regex]::Matches($final, [regex]::Escape($deploy_ph))).Count
    if ($placeholderCount -ne 0) {
        throw "Still $placeholderCount PLACEHOLDER left in $SettingsPath"
    }
    Write-Host "[deploy] tokens injected into $SettingsPath (placeholders left: 0)"
}

# 旧的 worker 进程要停掉（先停再 build，否则 apphost.exe 被锁无法 copy）
# PowerShell 5.1 + dotnet apphost 在某些 host 下 [System.Diagnostics.Process]::GetProcessesByName
# 返回 $null（找不到时），跟直接 dotnet 调 .NET API 行为不一致；用 taskkill 绕过。
Write-Host "[deploy] killing any running AgentBoard.ProposalWorker via taskkill..."
# taskkill 找不到进程时 exit 128 并写 stderr；ErrorActionPreference='Stop' 会让 throw
# 所以用 | Out-String 一次拿全 stdout+stderr，再 reset 偏好容忍非零退出
$prevPref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$tk = & taskkill /F /IM 'AgentBoard.ProposalWorker.exe' 2>&1 | Out-String
$ErrorActionPreference = $prevPref
Write-Host ("[deploy] taskkill output: {0}" -f $tk.Trim())
Start-Sleep -Seconds 2

# dotnet build
Write-Host "[deploy] dotnet build $WorkerRoot"
& dotnet build $WorkerRoot -c Debug -nologo -v minimal
if ($LASTEXITCODE -ne 0) { throw "dotnet build failed: $LASTEXITCODE" }

# 把 appsettings.* 复制到 bin（dotnet 默认会复制，但保险起见）
Copy-Item -Path (Join-Path $WorkerRoot 'appsettings*.json') -Destination $BinDir -Force
Write-Host "[deploy] appsettings copied to $BinDir"

# env
$env:DOTNET_ENVIRONMENT = 'Local'
$env:ASPNETCORE_ENVIRONMENT = 'Local'
# 读 MINIMAX_API_KEY from .env.worker（line grep）
Write-Host ("[deploy] Root=[{0}]" -f $Root)
$envFile = Join-Path $Root 'tmp\agentboard-worker\.env.worker'
Write-Host ("[deploy] envFile path=[{0}]" -f $envFile)
if (Test-Path $envFile) {
    $mmLine = Select-String -Path $envFile -Pattern '^MINIMAX_API_KEY='
    if ($mmLine) {
        $val = $mmLine.Line -replace '^MINIMAX_API_KEY=', ''
        $env:MINIMAX_API_KEY = $val
        Write-Host "[deploy] MINIMAX_API_KEY loaded from .env.worker (len=$($val.Length))"
    } else {
        Write-Warning "[deploy] MINIMAX_API_KEY not found in $envFile; minimax_invoker.py will fail"
    }
}

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$out = Join-Path $LogDir 'worker.out.log'
$err = Join-Path $LogDir 'worker.err.log'

Write-Host "[deploy] starting worker -> logs/worker.{out,err}.log"
Set-Location $BinDir
$exe = Join-Path $BinDir 'AgentBoard.ProposalWorker.exe'
$proc = Start-Process -FilePath $exe -WorkingDirectory $BinDir `
    -RedirectStandardOutput $out -RedirectStandardError $err `
    -WindowStyle Hidden -PassThru
Write-Host "[deploy] worker pid=$($proc.Id)"
Write-Host "[deploy] waiting 8s for boot..."
Start-Sleep -Seconds 8

# 验证：portal 是否在 58240
$listener = Get-NetTCPConnection -LocalPort 58240 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host "[deploy] portal listening: pid=$($listener[0].OwningProcess)"
} else {
    Write-Warning "[deploy] portal NOT listening on 58240 after 8s (see logs/worker.err.log)"
}

# 把 pid 写到 logs dir
$pidFile = Join-Path $LogDir 'worker.pid'
Set-Content -Path $pidFile -Value $proc.Id
Write-Host "[deploy] pid stored in $pidFile"
Write-Host ""
Write-Host "=== quick health ==="
Write-Host "  portal:    http://127.0.0.1:58240"
Write-Host "  logs:      $out"
Write-Host "  errors:    $err"
Write-Host "  stop:      Get-Process -Id $proc.Id | Stop-Process -Force"
