param(
    [int]$Runs = 30,
    [int]$Warmup = 3,
    [switch]$ClearResults
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

$arguments = @(
    "python", "/app/scripts/run_overhead.py",
    "--runs", $Runs,
    "--warmup", $Warmup
)
if ($ClearResults) {
    $arguments += "--clear-results"
}

docker compose exec -T `
    -e ORDER_SERVICE_URL=http://order-service:8000 `
    -e PAYMENT_SERVICE_URL=http://payment-service:8000 `
    -e NOTIFICATION_SERVICE_URL=http://notification-service:8000 `
    -e EXPERIMENT_RESULTS_DIR=/app/results `
    order-service `
    @arguments
