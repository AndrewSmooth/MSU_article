param(
    [int]$Repetitions = 5,
    [int]$BaseSeed = 1000,
    [switch]$ClearResults
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

$arguments = @(
    "python", "/app/scripts/run_reproducibility.py",
    "--repetitions", $Repetitions,
    "--base-seed", $BaseSeed
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
