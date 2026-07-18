# 将 web 包内 index.html 的占位符 __API_URL__ 替换为真实的 API 基址。
# 在 IIS 反向代理拓扑下，浏览器同源访问 /api，因此默认填 /api 即可。
# 用法：
#   .\configure-api-url.ps1                 # 默认写入 /api
#   .\configure-api-url.ps1 -ApiUrl https://api.example.com
#   .\configure-api-url.ps1 -ApiUrl https://board.example.com/api
param(
    [string]$ApiUrl = '/api',
    [string]$IndexPath = (Join-Path $PSScriptRoot 'index.html')
)

if (-not (Test-Path $IndexPath)) {
    Write-Error "未找到 index.html：$IndexPath"
    exit 1
}

$content = Get-Content $IndexPath -Raw -Encoding UTF8
if ($content -notmatch '__API_URL__') {
    Write-Host "index.html 中未找到 __API_URL__ 占位符（可能已配置过）。当前值："
    if ($content -match "window.AGENTBOARD_API = '([^']*)'") { Write-Host "  $($matches[1])" }
    exit 0
}

$content = $content -replace "__API_URL__", $ApiUrl
Set-Content $IndexPath $content -Encoding UTF8 -NoNewline
Write-Host "已将 AGENTBOARD_API 配置为：$ApiUrl"
