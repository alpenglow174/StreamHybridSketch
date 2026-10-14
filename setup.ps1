<#
.SYNOPSIS
  One-command setup / start for the Federated Sketches streaming project (Windows).

.DESCRIPTION
  Checks prerequisites, downloads what is missing, builds the image, starts
  Kafka + InfluxDB + Grafana + the app container, runs the unit tests and
  preprocesses the datasets. Safe to re-run: finished steps are skipped.

.EXAMPLE
  .\setup.ps1                 # set everything up (no demo)
  .\setup.ps1 -Demo           # ... and start the live pipeline demo
  .\setup.ps1 -Experiments    # ... and run all experiments + figures (~20+ min)
  .\setup.ps1 -Stop           # stop all project containers
  .\setup.ps1 -Clean          # remove containers AND Kafka/Influx volumes
#>
[CmdletBinding()]
param(
    [switch]$Demo,
    [switch]$Experiments,
    [switch]$SkipTests,
    [switch]$SkipData,
    [switch]$NoBrowser,
    [switch]$Stop,
    [switch]$Clean,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$LogDir = Join-Path $PSScriptRoot '.setup-logs'
New-Item -ItemType Directory -Force $LogDir | Out-Null

# ----------------------------------------------------------------- output
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Info($m) { Write-Host "  [..]   $m" }
function Warn($m) { Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Fail($m, $hint) {
    Write-Host "  [FAIL] $m" -ForegroundColor Red
    if ($hint) { Write-Host "         -> $hint" -ForegroundColor Red }
    exit 1
}

# Run a native command, tee output to a log file, return its exit code.
function Run-Logged([string]$name, [scriptblock]$cmd) {
    $log = Join-Path $LogDir "$name.log"
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $cmd *>&1 | Tee-Object -FilePath $log | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return @{ Code = $code; Log = $log }
}

# Run a native command quietly; returns @{Code; Out}
function Run-Quiet([scriptblock]$cmd) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $out = & $cmd 2>&1 | Out-String
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return @{ Code = $code; Out = $out.Trim() }
}

function Compose { docker compose @args }

# ------------------------------------------------------------ 1. docker
Step 'Checking Docker'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail 'docker CLI not found on PATH.' 'Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/ (then open it once).'
}

$info = Run-Quiet { docker info --format '{{.OSType}}|{{.Architecture}}|{{.MemTotal}}|{{.NCPU}}' }
if ($info.Code -ne 0) {
    if ($info.Out -match 'permission denied|Access is denied') {
        Fail 'Docker refused access.' 'Add your user to the "docker-users" group (Computer Management > Local Users and Groups), then sign out and in.'
    }
    $candidates = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe",
        "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }
    if (-not $candidates) {
        Fail 'Docker engine is not running and Docker Desktop was not found.' 'Start Docker Desktop (or your Docker engine) and re-run this script.'
    }
    Info "Docker engine not running - starting Docker Desktop ($($candidates[0]))"
    Start-Process -FilePath $candidates[0] | Out-Null
    $deadline = (Get-Date).AddSeconds(240)
    do {
        Start-Sleep 5
        $info = Run-Quiet { docker info --format '{{.OSType}}|{{.Architecture}}|{{.MemTotal}}|{{.NCPU}}' }
        Write-Host '.' -NoNewline
    } while ($info.Code -ne 0 -and (Get-Date) -lt $deadline)
    Write-Host ''
    if ($info.Code -ne 0) {
        Fail 'Docker Desktop did not become ready within 4 minutes.' 'Open Docker Desktop, wait for "Engine running", check it is not asking to accept terms / update WSL, then re-run.'
    }
}
$osType, $arch, $mem, $ncpu = $info.Out.Split('|')
if ($osType -ne 'linux') {
    Fail "Docker is in Windows-containers mode (OSType=$osType)." 'Right-click the Docker tray icon > "Switch to Linux containers...", then re-run.'
}
Ok "Docker engine running (linux/$arch, $ncpu CPUs, $([math]::Round([double]$mem/1GB,1)) GB RAM)"

$cv = Run-Quiet { docker compose version --short }
if ($cv.Code -ne 0) {
    Fail 'Docker Compose v2 ("docker compose") is not available.' 'Update Docker Desktop; the legacy docker-compose v1 is not supported by this project.'
}
Ok "Docker Compose $($cv.Out)"

# ---------------------------------------------------------- stop / clean
if ($Stop -or $Clean) {
    if ($Clean) {
        Step 'Removing project containers, network and volumes (Kafka + InfluxDB data)'
        if (-not $Force) {
            $ans = Read-Host '  This deletes the Kafka topics and all InfluxDB metrics of this project. Continue? [y/N]'
            if ($ans -notmatch '^(y|yes)$') { Info 'Aborted.'; exit 0 }
        }
        $r = Run-Logged 'clean' { docker compose --profile pipeline down -v --remove-orphans }
    } else {
        Step 'Stopping project containers'
        $r = Run-Logged 'stop' { docker compose --profile pipeline stop }
    }
    if ($r.Code -ne 0) { Fail "docker compose failed (exit $($r.Code))." "See $($r.Log)" }
    Ok 'Done. (Only containers of compose project "fedsketch" were touched.)'
    exit 0
}

# --------------------------------------------------------- 2. resources
Step 'Checking resources and ports'
if ([double]$mem -lt 4GB) {
    Warn "Docker has only $([math]::Round([double]$mem/1GB,1)) GB RAM. Preprocessing Sentiment140 may be killed (exit 137)."
    Warn 'Docker Desktop > Settings > Resources (or %UserProfile%\.wslconfig memory=) -> give it >= 6 GB.'
} elseif ([double]$mem -lt 6GB) {
    Warn "Docker has $([math]::Round([double]$mem/1GB,1)) GB RAM; 6 GB+ is recommended for the full experiment suite."
} else { Ok 'Docker memory is sufficient' }

$drive = (Get-Item $PSScriptRoot).PSDrive
if ($drive -and $drive.Free -ne $null) {
    $freeGB = [math]::Round($drive.Free / 1GB, 1)
    if ($freeGB -lt 3) { Fail "Only $freeGB GB free on drive $($drive.Name):" 'Free up at least 5 GB (images ~2 GB + data ~0.7 GB).' }
    elseif ($freeGB -lt 6) { Warn "Only $freeGB GB free on drive $($drive.Name): (5 GB+ recommended)." }
    else { Ok "$freeGB GB free on drive $($drive.Name):" }
}

# ports: a port in use is fine if it is OUR container that holds it
$running = @((Run-Quiet { docker compose ps --status running --services }).Out -split "`r?`n" | Where-Object { $_ })
$ports = @(@(3000, 'grafana'), @(8086, 'influxdb'), @(29092, 'kafka'))
foreach ($pair in $ports) {
    $p = $pair[0]; $svc = $pair[1]
    if ($running -contains $svc) { Ok "port $p held by our $svc container"; continue }
    $busy = $false; $owner = ''
    try {
        $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($conn) { $busy = $true; $owner = (Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue).ProcessName }
    } catch {
        $tcp = New-Object Net.Sockets.TcpClient
        try { $tcp.Connect('127.0.0.1', $p); $busy = $true } catch { } finally { $tcp.Close() }
    }
    if ($busy) {
        $other = (Run-Quiet { docker ps --filter "publish=$p" --format '{{.Names}}' }).Out
        if ($other -like 'fedsketch-*') { Ok "port $p held by our container $other"; continue }
        $who = if ($other) { "container '$other'" } elseif ($owner) { "process '$owner'" } else { 'another program' }
        Fail "Port $p (needed by $svc) is already in use by $who." "Stop it, or change the left side of the '$p' port mapping of '$svc' in docker-compose.yml."
    }
    Ok "port $p free"
}

# ------------------------------------------------------------ 3. wheels
Step 'Python packages for the image'
New-Item -ItemType Directory -Force 'docker\wheels' | Out-Null
$wheelCount = @(Get-ChildItem 'docker\wheels' -Filter *.whl -ErrorAction SilentlyContinue).Count
if ($arch -notmatch 'x86_64|amd64') {
    Info "CPU architecture is ${arch}: bundled wheels are x86-64 only, the build will install from PyPI."
} elseif ($wheelCount -ge 50) {
    Ok "$wheelCount bundled wheels found (offline install)"
} else {
    Info "Bundled wheels missing ($wheelCount found) - trying to download them on the host"
    $py = $null
    foreach ($c in @(@('py', '-3'), @('python'), @('python3'))) {
        if (Get-Command $c[0] -ErrorAction SilentlyContinue) {
            $rest = if ($c.Count -gt 1) { $c[1..($c.Count - 1)] } else { @() }
            $t = Run-Quiet { & $c[0] @rest -m pip --version }
            if ($t.Code -eq 0) { $py = $c; break }   # skips the Microsoft Store "python" stub
        }
    }
    if (-not $py) {
        Warn 'No usable host Python with pip found; the image build will download packages from PyPI instead.'
    } else {
        $rest = if ($py.Count -gt 1) { $py[1..($py.Count - 1)] } else { @() }
        $r = Run-Logged 'pip-download' { & $py[0] @rest -m pip download -q -r docker\requirements.txt pytest==8.2.2 -d docker\wheels `
            --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux_2_28_x86_64 `
            --platform any --python-version 3.11 --implementation cp --only-binary=:all: --retries 5 --timeout 60 }
        if ($r.Code -eq 0) { Ok 'wheels downloaded' }
        else { Warn "wheel download failed (see $($r.Log)); the image build will fall back to PyPI." }
    }
}

# ---------------------------------------------------------- 4. datasets
$datasets = @(
    @{ Name = 'gowalla'; File = 'data\raw\loc-gowalla_totalCheckins.txt.gz'; Min = 100MB; Magic = @(0x1F, 0x8B)
       Url = 'https://snap.stanford.edu/data/loc-gowalla_totalCheckins.txt.gz' },
    @{ Name = 'sentiment140'; File = 'data\raw\sentiment140.zip'; Min = 70MB; Magic = @(0x50, 0x4B)
       Url = 'https://cs.stanford.edu/people/alecmgo/trainingandtestdata.zip' }
)
function Test-RawFile($d) {
    if (-not (Test-Path $d.File)) { return $false }
    $f = Get-Item $d.File
    if ($f.Length -lt $d.Min) { return $false }
    $fs = [IO.File]::OpenRead($f.FullName)
    try { $b = New-Object byte[] 2; [void]$fs.Read($b, 0, 2) } finally { $fs.Close() }
    return ($b[0] -eq $d.Magic[0] -and $b[1] -eq $d.Magic[1])
}
$haveRaw = @{}
if ($SkipData) {
    Step 'Datasets (skipped by -SkipData)'
} else {
    Step 'Datasets'
    New-Item -ItemType Directory -Force 'data\raw', 'data\processed' | Out-Null
    foreach ($d in $datasets) {
        if (Test-Path "data\processed\$($d.Name)_meta.json") { Ok "$($d.Name): already preprocessed"; $haveRaw[$d.Name] = $true; continue }
        if (Test-RawFile $d) { Ok "$($d.Name): raw file present"; $haveRaw[$d.Name] = $true; continue }
        if (Test-Path $d.File) { Warn "$($d.Name): existing file is truncated or corrupt - re-downloading"; Remove-Item $d.File -Force }
        Info "$($d.Name): downloading $($d.Url)"
        $tmp = "$($d.File).part"
        try {
            if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                & curl.exe -L --fail --retry 3 --retry-delay 5 --connect-timeout 30 -o $tmp $d.Url
                if ($LASTEXITCODE -ne 0) { throw "curl exit $LASTEXITCODE" }
            } else {
                $ProgressPreference = 'SilentlyContinue'
                Invoke-WebRequest -Uri $d.Url -OutFile $tmp -UseBasicParsing -TimeoutSec 600
            }
            Move-Item -Force $tmp $d.File
        } catch {
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
            Warn "$($d.Name): download failed ($($_.Exception.Message))."
            Warn "         Download it manually from $($d.Url) into $($d.File) and re-run. Continuing without it."
            continue
        }
        if (Test-RawFile $d) { Ok "$($d.Name): downloaded and verified"; $haveRaw[$d.Name] = $true }
        else { Warn "$($d.Name): downloaded file failed verification (size/format). The server may be down; continuing without it." }
    }
}

# ------------------------------------------------------------- 5. build
Step 'Building the app image'
$r = Run-Logged 'build' { docker compose build app }
if ($r.Code -ne 0) {
    $txt = Get-Content $r.Log -Raw
    if ($txt -match 'Name or service not known|Temporary failure in name resolution|Could not resolve|Failed to establish a new connection') {
        Warn 'Network/DNS failure inside the Docker build - retrying once...'
        $r = Run-Logged 'build-retry' { docker compose build app }
        if ($r.Code -ne 0) {
            Fail 'Image build failed: the build container cannot reach PyPI.' 'Run this script again after fixing network (VPN/proxy?), or let it download docker\wheels on the host (needs host Python) for an offline build.'
        }
    } elseif ($txt -match 'no space left on device') {
        Fail 'Docker ran out of disk space during the build.' 'Run "docker system prune" or enlarge the Docker disk (Settings > Resources).'
    } elseif ($txt -match 'pull access denied|TLS handshake timeout|i/o timeout') {
        Fail 'Could not pull the python base image from Docker Hub.' 'Check your internet connection / proxy settings in Docker Desktop and re-run.'
    } else {
        Fail "Image build failed (exit $($r.Code))." "See $($r.Log)"
    }
}
Ok 'image fedsketch-app:1.0 built'

# ------------------------------------------------------------- 6. start
Step 'Starting Kafka, InfluxDB, Grafana and the app container'
$r = Run-Logged 'up' { docker compose up -d kafka influxdb grafana app }
if ($r.Code -ne 0) {
    $txt = Get-Content $r.Log -Raw
    if ($txt -match 'port is already allocated|address already in use') {
        Fail 'A port is already in use.' 'Another container/program grabbed 3000, 8086 or 29092 - stop it or change docker-compose.yml.'
    }
    Fail "docker compose up failed (exit $($r.Code))." "See $($r.Log)"
}

function Wait-Ready($name, [scriptblock]$probe, $seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        $okNow = $false
        try { $okNow = & $probe } catch { $okNow = $false }
        if ($okNow) { Ok "$name is ready"; return }
        Start-Sleep 3
    }
    Write-Host "  last log lines of ${name}:" -ForegroundColor Red
    docker compose logs --tail 25 $name
    Fail "$name did not become ready within $seconds s." "Inspect with: docker compose logs $name"
}
Wait-Ready 'kafka' { (Run-Quiet { docker compose exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list }).Code -eq 0 } 120
Wait-Ready 'influxdb' { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://localhost:8086/ping).StatusCode -eq 204 } 90
Wait-Ready 'grafana' { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://localhost:3000/api/health).StatusCode -eq 200 } 90
Wait-Ready 'app' { (Run-Quiet { docker compose exec -T app python -c "import flwr, dp_accounting, confluent_kafka" }).Code -eq 0 } 60

# ------------------------------------------------------------- 7. tests
if ($SkipTests) { Step 'Unit tests (skipped)' } else {
    Step 'Running unit tests'
    $r = Run-Logged 'tests' { docker compose exec -T app python -m pytest -q tests }
    if ($r.Code -ne 0) { Fail 'Unit tests failed.' "See $($r.Log)" }
    Ok 'unit tests passed'
}

# ----------------------------------------------------------- 8. prepare
Step 'Preprocessing datasets'
$toPrepare = @('synthetic') + @($datasets | Where-Object { $haveRaw[$_.Name] } | ForEach-Object { $_.Name })
foreach ($n in $toPrepare) {
    if (Test-Path "data\processed\${n}_meta.json") { Ok "${n}: already done"; continue }
    Info "${n}: preprocessing (1-3 min)..."
    $r = Run-Logged "prepare-$n" { docker compose exec -T app python -m experiments.prepare $n }
    if ($r.Code -eq 137) { Fail "${n}: preprocessing was killed (out of memory)." 'Give Docker more memory (6 GB+) and re-run.' }
    if ($r.Code -ne 0) { Fail "${n}: preprocessing failed (exit $($r.Code))." "See $($r.Log)" }
    Ok "${n}: done"
}

# ------------------------------------------------------------ 9. demo
if ($Demo) {
    Step 'Starting the live pipeline demo'
    $cfg = Get-Content 'pipeline\config.json' -Raw | ConvertFrom-Json
    if (-not (Test-Path "data\processed\$($cfg.dataset)_meta.json")) {
        Fail "pipeline\config.json uses dataset '$($cfg.dataset)', which is not preprocessed." 'Re-run without -SkipData, or set "dataset" to "synthetic" in pipeline\config.json.'
    }
    $r = Run-Logged 'demo-topics' { docker compose --profile pipeline up -d --force-recreate --no-deps topics }
    $code = (Run-Quiet { docker wait fedsketch-topics-1 }).Out
    if ($r.Code -ne 0 -or $code -ne '0') {
        docker compose logs --tail 20 topics
        Fail 'Creating the Kafka topics failed.' 'See the log above; is Kafka healthy? (docker compose logs kafka)'
    }
    Ok 'Kafka topics (re)created'
    $svcs = @('producer', 'oracle', 'fl-server') + (0..7 | ForEach-Object { "edge-$_" })
    $r = Run-Logged 'demo-up' { docker compose --profile pipeline up -d --force-recreate --no-deps @svcs }
    if ($r.Code -ne 0) { Fail 'Starting the pipeline services failed.' "See $($r.Log)" }
    Start-Sleep 20
    $exited = (Run-Quiet { docker compose --profile pipeline ps --status exited --services }).Out -split "`r?`n" |
        Where-Object { $_ -and $_ -ne 'topics' }
    if ($exited) {
        foreach ($s in $exited) { Write-Host "  --- $s ---" -ForegroundColor Red; docker compose logs --tail 15 $s }
        Fail "Pipeline service(s) exited early: $($exited -join ', ')" 'See the logs above.'
    }
    Ok "pipeline running: $($cfg.max_rounds) rounds of '$($cfg.dataset)' at $($cfg.seconds_per_round) s/round (~$([math]::Ceiling($cfg.max_rounds * $cfg.seconds_per_round / 60)) min)"
    Info 'follow it with:  docker compose logs -f fl-server oracle'
    if (-not $NoBrowser) { Start-Process 'http://localhost:3000/d/fedsketch' }
}

# ------------------------------------------------------- 10. experiments
if ($Experiments) {
    Step 'Running all experiments (this takes 20+ minutes)'
    $r = Run-Logged 'experiments' { docker compose exec -T app sh experiments/run_all.sh }
    if ($r.Code -ne 0) { Fail 'Some experiments failed.' 'Check results\logs\*.log' }
    $r = Run-Logged 'plots' { docker compose exec -T app python -m experiments.plots }
    $r = Run-Logged 'tables' { docker compose exec -T app python -m experiments.tables }
    Ok 'results in results\ (CSV), results\figures\, report\tables\'
}

# ------------------------------------------------------------- summary
Step 'Ready'
Write-Host '  Grafana dashboard : http://localhost:3000/d/fedsketch   (anonymous admin)'
Write-Host '  Toolbox shell     : docker compose exec app bash'
Write-Host '  Live demo         : .\setup.ps1 -Demo'
Write-Host '  All experiments   : .\setup.ps1 -Experiments'
Write-Host '  Stop / remove     : .\setup.ps1 -Stop   |   .\setup.ps1 -Clean'
Write-Host "  Logs of this run  : $LogDir"
