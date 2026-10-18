$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

docker compose exec -T `
    -e ORDER_SERVICE_URL=http://order-service:8000 `
    order-service `
    python /app/scripts/run_control.py
