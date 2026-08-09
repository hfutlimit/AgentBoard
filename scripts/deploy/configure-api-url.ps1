# Configure window.AGENTBOARD_API in the web bundle's index.html.
# In the IIS reverse-proxy topology the browser calls /api same-origin, so the
# default value is '/api'.
# Usage:
#   .\configure-api-url.ps1                 # write '/api' by default
#   .\configure-api-url.ps1 -ApiUrl https://api.example.com
#   .\configure-api-url.ps1 -ApiUrl https://board.example.com/api
#   .\configure-api-url.ps1 -Force          # force rewrite even if already set
param(
    [string]$ApiUrl = '/api',
    [string]$IndexPath = (Join-Path $PSScriptRoot 'index.html'),
    [switch]$Force
)

if (-not (Test-Path $IndexPath)) {
    Write-Error "index.html not found: $IndexPath"
    exit 1
}

$content = Get-Content $IndexPath -Raw -Encoding UTF8

# Placeholder still present -> first-time configure.
if ($content -match '__API_URL__') {
    $content = $content -replace "__API_URL__", $ApiUrl
    Set-Content $IndexPath $content -Encoding UTF8 -NoNewline
    Write-Host "AGENTBOARD_API set to: $ApiUrl"
    exit 0
}

# Placeholder already replaced: print current value. With -Force, rewrite it.
# This fixes historical empty injection ('' -> frontend fell back to the
# browser's own 127.0.0.1:8000 and every API call failed; 2026-08-09 incident).
if ($content -match "window\.AGENTBOARD_API = '([^']*)'") {
    $current = $matches[1]
    Write-Host "Current AGENTBOARD_API = '$current'"
    if (-not $Force) {
        Write-Host "Skipped (no -Force). Add -Force to force rewrite."
        exit 0
    }
    $content = $content -replace "window\.AGENTBOARD_API = '[^']*'", "window.AGENTBOARD_API = '$ApiUrl'"
    Set-Content $IndexPath $content -Encoding UTF8 -NoNewline
    Write-Host "Rewritten AGENTBOARD_API: '$current' -> '$ApiUrl'"
    exit 0
}

Write-Host "window.AGENTBOARD_API not found in index.html; skipped."
exit 0
