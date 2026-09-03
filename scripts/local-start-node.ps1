# Local dev: start AgentBoard Proposal Worker (.NET 10) on portal port 58240
$ErrorActionPreference = 'Stop'
$Root = 'D:\AI\Projects\AgentBoard'
$WorkerRoot = Join-Path $Root 'src\nodes\AgentBoard.Node'
$WorkerBin  = Join-Path $WorkerRoot 'bin\Debug\net10.0'

if (-not (Test-Path (Join-Path $WorkerBin 'AgentBoard.Node.exe'))) {
    throw "Worker exe not built. Run: dotnet build $WorkerRoot"
}

# .NET picks appsettings.{Environment}.json from the content root (the bin folder).
$env:DOTNET_ENVIRONMENT = 'Local'
$env:ASPNETCORE_ENVIRONMENT = 'Local'

# the worker reads its working dir / sqlite path relative to CWD
Set-Location $WorkerBin

$logDir = Join-Path $Root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$out = Join-Path $logDir 'worker.out.log'
$err = Join-Path $logDir 'worker.err.log'

Write-Host "[worker] starting in $WorkerBin -> logs/worker.{out,err}.log"
Write-Host "[worker] ASPNETCORE_ENVIRONMENT=Local (loads appsettings.Local.json)"

$exe = Join-Path $WorkerBin 'AgentBoard.Node.exe'
$proc = Start-Process -FilePath $exe -WorkingDirectory $WorkerBin `
    -RedirectStandardOutput $out -RedirectStandardError $err `
    -WindowStyle Hidden -PassThru
Write-Host "[worker] pid=$($proc.Id)"
