param(
    [int]$Port = 18240,
    [string]$WorkerId = "$env:COMPUTERNAME-worker-owned",
    [string]$ConfigurationPath = '',
    [switch]$NoBuild,
    [switch]$PassThruBootstrap
)
$ErrorActionPreference = 'Stop'
$portalRepo = Split-Path $PSScriptRoot -Parent
$portalOutput = Join-Path $portalRepo 'tmp\worker-owned-portal'
$portalBinary = Join-Path $portalOutput 'bin\AgentBoard.Node.dll'
if (-not $ConfigurationPath) {
    $ConfigurationPath = Join-Path $env:LOCALAPPDATA 'AgentBoard\worker-owned.local.json'
}
$ConfigurationPath = [IO.Path]::GetFullPath($ConfigurationPath)
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Stop the identified portal process before replacing it."
}
if (-not $NoBuild) {
    & dotnet publish (Join-Path $portalRepo 'src\nodes\AgentBoard.Node\AgentBoard.Node.csproj') -c Release -o (Split-Path $portalBinary) --verbosity quiet
    if ($LASTEXITCODE -ne 0) { throw 'Node publish failed' }
}
if (-not (Test-Path -LiteralPath $portalBinary)) { throw 'Publish the Node before using -NoBuild' }
$portalSavedEnvironment = @{}
try {
    foreach ($portalName in @('AgentBoard__ServerUrl','AgentBoard__StartupToken','RabbitMq__Uri','Portal__ApiKey')) {
        $portalSavedEnvironment[$portalName] = [Environment]::GetEnvironmentVariable($portalName, 'Process')
        if (-not $portalSavedEnvironment[$portalName]) {
            $portalUserValue = [Environment]::GetEnvironmentVariable($portalName, 'User')
            if ($portalUserValue) { [Environment]::SetEnvironmentVariable($portalName, $portalUserValue, 'Process') }
        }
    }
    if (-not $env:AgentBoard__ServerUrl -or -not $env:AgentBoard__StartupToken) {
        throw 'Set AgentBoard__ServerUrl and AgentBoard__StartupToken in environment; never pass credentials on the command line.'
    }
    if (-not $env:Portal__ApiKey) {
        $portalRandom = New-Object byte[] 32
        $portalRng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $portalRng.GetBytes($portalRandom) } finally { $portalRng.Dispose() }
        $env:Portal__ApiKey = [Convert]::ToBase64String($portalRandom)
        # Separate local access credential, not a production token.
        [Environment]::SetEnvironmentVariable('Portal__ApiKey', $env:Portal__ApiKey, 'User')
    }
    $portalArguments = @(
        ('"' + $portalBinary + '"'),
        '--Portal:ConfigurationOnly=true',
        "--Portal:Urls=http://127.0.0.1:$Port",
        '--DurableExecution:Enabled=false',
        ('"--LocalConfigurationPath=' + $ConfigurationPath + '"'),
        ('"--Node:Id=' + $WorkerId + '"'),
        ('"--Node:HistoryDatabasePath=' + (Join-Path $portalOutput 'portal.db') + '"')
    )
    $portalProcess = Start-Process -FilePath (Get-Command dotnet.exe).Source -ArgumentList $portalArguments -WindowStyle Hidden -PassThru -WorkingDirectory $portalRepo -RedirectStandardOutput (Join-Path $portalOutput 'portal.stdout.log') -RedirectStandardError (Join-Path $portalOutput 'portal.stderr.log')
    $portalResult = @{ processId = $portalProcess.Id; url = "http://127.0.0.1:$Port/"; configurationPath = $ConfigurationPath; configurationOnly = $true }
    if ($PassThruBootstrap) { $portalResult['bootstrapUrl'] = $portalResult.url + '#key=' + [Uri]::EscapeDataString($env:Portal__ApiKey) }
    $portalResult | ConvertTo-Json -Compress
} finally {
    foreach ($portalName in $portalSavedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($portalName, $portalSavedEnvironment[$portalName], 'Process')
    }
}
