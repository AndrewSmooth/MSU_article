$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

docker compose down --volumes --remove-orphans
Write-Host "Containers, PostgreSQL data, Kafka data and offsets were removed."

