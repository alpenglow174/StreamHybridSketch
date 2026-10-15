<#
.SYNOPSIS
  Fast restart of the live demo for recording (Windows). Run .\setup.ps1 once first.
  Recreates the Kafka topics, starts producer + 8 edges + Flower server + evaluator,
  and opens Grafana. With the default config the run takes about 2 minutes.
#>
$ErrorActionPreference = 'Continue'   # native tools write progress to stderr; exit codes are checked instead
Set-Location -LiteralPath $PSScriptRoot
function Fail($m, $h) { Write-Host "[FAIL] $m" -ForegroundColor Red; if ($h) { Write-Host "       -> $h" -ForegroundColor Red }; exit 1 }

docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail 'Docker is not running.' 'Start Docker Desktop, then run .\setup.ps1 once.' }
$running = (docker compose ps --status running --services 2>$null) -join ' '
foreach ($svc in 'kafka', 'influxdb', 'grafana') {
    if ($running -notmatch "\b$svc\b") { Fail "The $svc container is not running." 'Run .\setup.ps1 first (it starts and checks the stack).' }
}
$cfg = Get-Content 'pipeline\config.json' -Raw | ConvertFrom-Json
if (-not (Test-Path "data\processed\$($cfg.dataset)_meta.json")) { Fail "Dataset '$($cfg.dataset)' is not preprocessed." 'Run .\setup.ps1 first.' }

docker compose --profile pipeline up -d --force-recreate --no-deps topics *> $null
if ((docker wait fedsketch-topics-1) -ne '0') { docker compose logs --tail 20 topics; Fail 'Creating Kafka topics failed.' }
$svcs = @('producer', 'oracle', 'fl-server') + (0..7 | ForEach-Object { "edge-$_" })
docker compose --profile pipeline up -d --force-recreate --no-deps @svcs *> $null
if ($LASTEXITCODE -ne 0) { Fail 'Starting the pipeline failed.' 'Run .\setup.ps1 -Demo for a full check.' }
Start-Process 'http://localhost:3000/d/fedsketch?orgId=1&from=now-5m&to=now&refresh=5s'
$secs = [math]::Ceiling($cfg.max_rounds * 0.82)
Write-Host "[ok] demo running: $($cfg.max_rounds) rounds of '$($cfg.dataset)' (~$secs s). Drifts at rounds 70 and 140."
Write-Host '     follow in a terminal:  docker compose logs -f fl-server oracle'
