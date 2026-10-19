$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

$experimentId = "b3-" + (Get-Date -Format "yyyyMMdd-HHmmss")
docker compose exec -T `
    -e ORDER_SERVICE_URL=http://order-service:8000 `
    -e PAYMENT_SERVICE_URL=http://payment-service:8000 `
    -e NOTIFICATION_SERVICE_URL=http://notification-service:8000 `
    -e EXPERIMENT_RESULTS_PATH=/app/results/experiments.jsonl `
    order-service `
    python /app/scripts/run_experiment.py `
    --experiment-id $experimentId `
    --point order.kafka.order_paid.publish `
    --fault-type error `
    --delay-ms 0 `
    --status-code 503 `
    --activation-limit 1 `
    --seed 1 `
    --expect-bug B3
