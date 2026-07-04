$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $repoRoot "apps\api"
$python = "D:\projects\hermes_agents\.venv\Scripts\python.exe"

$env:PYTHONPATH = Join-Path $apiRoot "src"

Push-Location $apiRoot
try {
    & $python -m tinylm_glass_box_api.bench @args
}
finally {
    Pop-Location
}
