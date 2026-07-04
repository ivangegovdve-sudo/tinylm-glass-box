param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8000/api"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$webRoot = Join-Path $repoRoot "apps\web"
$env:VITE_API_BASE_URL = $ApiBaseUrl

Push-Location $webRoot
try {
    npm run dev
}
finally {
    Pop-Location
}
