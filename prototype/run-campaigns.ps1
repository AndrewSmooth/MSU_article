param(
    [int]$Campaigns = 10,
    [int]$Budget = 8,
    [int]$BaseSeed = 1,
    [ValidateSet("both", "random", "trace")]
    [string]$Strategy = "both",
    [switch]$ClearResults,
    [switch]$AnalyzeExisting
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

$arguments = @(
    "python", "/app/scripts/run_campaigns.py",
    "--campaigns", $Campaigns,
    "--budget", $Budget,
    "--base-seed", $BaseSeed,
    "--strategy", $Strategy
)
if ($ClearResults) {
    $arguments += "--clear-results"
}
if ($AnalyzeExisting) {
    $arguments += "--analyze-existing"
}

docker compose exec -T `
    -e ORDER_SERVICE_URL=http://order-service:8000 `
    -e PAYMENT_SERVICE_URL=http://payment-service:8000 `
    -e NOTIFICATION_SERVICE_URL=http://notification-service:8000 `
    -e JAEGER_QUERY_URL=http://jaeger:16686 `
    -e EXPERIMENT_RESULTS_DIR=/app/results `
    order-service `
    @arguments
