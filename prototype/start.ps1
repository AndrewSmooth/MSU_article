$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

docker compose up --build -d
Write-Host "Stand started."
Write-Host "Order API: http://localhost:8000/docs"
Write-Host "Jaeger:   http://localhost:16686"
Write-Host "Run control scenario: .\control.ps1"

