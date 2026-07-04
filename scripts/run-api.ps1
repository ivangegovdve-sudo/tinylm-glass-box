param(
    [string]$ConfigName = "diverse-public.seats.json",
    [int]$Port = 8000,
    [string]$DatabasePath = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $repoRoot "apps\api"
$python = "D:\projects\hermes_agents\.venv\Scripts\python.exe"

$env:PYTHONPATH = Join-Path $apiRoot "src"
$env:TINYLM_GLASS_BOX_CONFIG = Join-Path $apiRoot ("config\" + $ConfigName)
if ($DatabasePath) {
    $env:TINYLM_GLASS_BOX_DB_PATH = $DatabasePath
}

Push-Location $apiRoot
try {
    & $python -m uvicorn tinylm_glass_box_api.app:app --host 127.0.0.1 --port $Port
}
finally {
    Pop-Location
}
