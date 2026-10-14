#!/usr/bin/env bash
# One-command setup / start for the Federated Sketches streaming project (Linux / macOS).
#
#   ./setup.sh                 set everything up (no demo)
#   ./setup.sh --demo          ... and start the live pipeline demo
#   ./setup.sh --experiments   ... and run all experiments + figures (~20+ min)
#   ./setup.sh --stop          stop all project containers
#   ./setup.sh --clean         remove containers AND Kafka/Influx volumes
# Other flags: --skip-tests --skip-data --no-browser --force (no confirmation for --clean)
#
# Safe to re-run: finished steps are skipped. Written for bash 3.2+ (macOS default).
set -u

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
LOG_DIR="$ROOT/.setup-logs"
mkdir -p "$LOG_DIR"

DEMO=0; EXPERIMENTS=0; SKIP_TESTS=0; SKIP_DATA=0; NO_BROWSER=0; STOP=0; CLEAN=0; FORCE=0
for arg in "$@"; do
  case "$arg" in
    --demo) DEMO=1 ;;
    --experiments) EXPERIMENTS=1 ;;
    --skip-tests) SKIP_TESTS=1 ;;
    --skip-data) SKIP_DATA=1 ;;
    --no-browser) NO_BROWSER=1 ;;
    --stop) STOP=1 ;;
    --clean) CLEAN=1 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (see ./setup.sh --help)"; exit 2 ;;
  esac
done

# ----------------------------------------------------------------- output
if [ -t 1 ]; then C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YEL=$'\033[33m'; C_RED=$'\033[31m'; C_DIM=$'\033[90m'; C_OFF=$'\033[0m'
else C_CYAN=""; C_GREEN=""; C_YEL=""; C_RED=""; C_DIM=""; C_OFF=""; fi
step() { printf '\n%s==> %s%s\n' "$C_CYAN" "$1" "$C_OFF"; }
ok()   { printf '  %s[ok]%s   %s\n' "$C_GREEN" "$C_OFF" "$1"; }
info() { printf '  [..]   %s\n' "$1"; }
warn() { printf '  %s[warn]%s %s\n' "$C_YEL" "$C_OFF" "$1"; }
fail() {
  printf '  %s[FAIL] %s%s\n' "$C_RED" "$1" "$C_OFF"
  [ -n "${2:-}" ] && printf '  %s       -> %s%s\n' "$C_RED" "$2" "$C_OFF"
  exit 1
}
# run_logged NAME CMD...  -> runs CMD, streams indented output, saves it to a log; sets RC and LOG
run_logged() {
  local name="$1"; shift
  LOG="$LOG_DIR/$name.log"
  "$@" 2>&1 | tee "$LOG" | sed "s/^/         ${C_DIM}/;s/\$/${C_OFF}/"
  RC=${PIPESTATUS[0]}
}

# ---------------------------------------------------------- 0. base tools
step "Checking tools"
command -v docker >/dev/null 2>&1 || fail "docker CLI not found." \
  "Install Docker Desktop (macOS/Windows) or Docker Engine (Linux): https://docs.docker.com/get-docker/"
command -v curl >/dev/null 2>&1 || fail "curl not found." "Install curl (e.g. sudo apt install curl)."
ok "docker and curl found"

# ------------------------------------------------------------ 1. docker
step "Checking Docker"
INFO_FMT='{{.OSType}}|{{.Architecture}}|{{.MemTotal}}|{{.NCPU}}'
INFO="$(docker info --format "$INFO_FMT" 2>&1)"; RC=$?
if [ $RC -ne 0 ]; then
  if printf '%s' "$INFO" | grep -qi 'WSL integration\|could not be found in this WSL'; then
    fail "This WSL distro has no access to Docker Desktop." \
      "Docker Desktop > Settings > Resources > WSL integration: enable it for this distro, then re-run (or run setup.ps1 from Windows)."
  fi
  if printf '%s' "$INFO" | grep -qi 'permission denied'; then
    fail "Your user may not talk to the Docker daemon (permission denied)." \
      "Run: sudo usermod -aG docker \$USER   then log out and back in (or use: newgrp docker)."
  fi
  case "$(uname -s)" in
    Darwin)
      if [ -d "/Applications/Docker.app" ]; then
        info "Docker engine not running - starting Docker Desktop"
        open -a Docker
        i=0
        while [ $i -lt 48 ]; do
          sleep 5; i=$((i + 1)); printf '.'
          INFO="$(docker info --format "$INFO_FMT" 2>&1)" && break
        done
        echo
        docker info >/dev/null 2>&1 || fail "Docker Desktop did not become ready within 4 minutes." \
          "Open Docker Desktop, wait for 'Engine running' (accept any terms/update dialogs), then re-run."
      else
        fail "Docker engine is not running and Docker Desktop was not found." "Install/start Docker Desktop, then re-run."
      fi ;;
    *)
      fail "Docker engine is not running." \
        "Start it: sudo systemctl start docker   (or open Docker Desktop), then re-run." ;;
  esac
fi
OS_TYPE="${INFO%%|*}"; REST="${INFO#*|}"; ARCH="${REST%%|*}"; REST="${REST#*|}"; MEM="${REST%%|*}"; NCPU="${REST#*|}"
[ "$OS_TYPE" = "linux" ] || fail "Docker is not running Linux containers (OSType=$OS_TYPE)." "Switch Docker to Linux containers and re-run."
MEM_GB=$(awk -v m="$MEM" 'BEGIN{printf "%.1f", m/1073741824}')
ok "Docker engine running (linux/$ARCH, $NCPU CPUs, $MEM_GB GB RAM)"

CV="$(docker compose version --short 2>&1)" || fail "Docker Compose v2 ('docker compose') is not available." \
  "Install the compose plugin (e.g. sudo apt install docker-compose-plugin) or update Docker Desktop. Legacy docker-compose v1 is not supported."
ok "Docker Compose $CV"

# ------------------------------------------------------- stop / clean
if [ $STOP -eq 1 ] || [ $CLEAN -eq 1 ]; then
  if [ $CLEAN -eq 1 ]; then
    step "Removing project containers, network and volumes (Kafka + InfluxDB data)"
    if [ $FORCE -ne 1 ]; then
      printf '  This deletes the Kafka topics and all InfluxDB metrics of this project. Continue? [y/N] '
      read -r ans
      case "$ans" in y|Y|yes|YES) ;; *) info "Aborted."; exit 0 ;; esac
    fi
    run_logged clean docker compose --profile pipeline down -v --remove-orphans
  else
    step "Stopping project containers"
    run_logged stop docker compose --profile pipeline stop
  fi
  [ $RC -eq 0 ] || fail "docker compose failed (exit $RC)." "See $LOG"
  ok 'Done. (Only containers of compose project "fedsketch" were touched.)'
  exit 0
fi

# --------------------------------------------------------- 2. resources
step "Checking resources and ports"
if [ "$MEM" -lt 4294967296 ] 2>/dev/null; then
  warn "Docker has only $MEM_GB GB RAM. Preprocessing Sentiment140 may be killed (exit 137)."
  warn "Give Docker >= 6 GB (Docker Desktop > Settings > Resources)."
elif [ "$MEM" -lt 6442450944 ] 2>/dev/null; then
  warn "Docker has $MEM_GB GB RAM; 6 GB+ is recommended for the full experiment suite."
else
  ok "Docker memory is sufficient"
fi
FREE_KB=$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')
FREE_GB=$(awk -v k="$FREE_KB" 'BEGIN{printf "%.1f", k/1048576}')
if [ "${FREE_KB:-0}" -lt 3145728 ]; then fail "Only $FREE_GB GB free disk space here." "Free up at least 5 GB (images ~2 GB + data ~0.7 GB)."
elif [ "${FREE_KB:-0}" -lt 6291456 ]; then warn "Only $FREE_GB GB free disk space (5 GB+ recommended)."
else ok "$FREE_GB GB free disk space"; fi

RUNNING="$(docker compose ps --status running --services 2>/dev/null)"
port_busy() {   # 0 = something listens on 127.0.0.1:$1
  if command -v lsof >/dev/null 2>&1; then lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1 && return 0; fi
  if command -v ss >/dev/null 2>&1; then ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$1\$" && return 0; fi
  (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1 && return 0
  return 1
}
port_owner() {
  local c; c="$(docker ps --filter "publish=$1" --format '{{.Names}}' 2>/dev/null | head -n1)"
  if [ -n "$c" ]; then echo "container '$c'"; return; fi
  if command -v lsof >/dev/null 2>&1; then
    c="$(lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1}')"
    [ -n "$c" ] && { echo "process '$c'"; return; }
  fi
  echo "another program"
}
for pair in 3000:grafana 8086:influxdb 29092:kafka; do
  p="${pair%%:*}"; svc="${pair#*:}"
  if printf '%s\n' "$RUNNING" | grep -qx "$svc"; then ok "port $p held by our $svc container"; continue; fi
  if port_busy "$p"; then
    owner="$(port_owner "$p")"
    case "$owner" in *"'fedsketch-"*) ok "port $p held by our $owner"; continue ;; esac
    fail "Port $p (needed by $svc) is already in use by $owner." \
      "Stop it, or change the left side of the '$p' port mapping of '$svc' in docker-compose.yml."
  fi
  ok "port $p free"
done

# ------------------------------------------------------------ 3. wheels
step "Python packages for the image"
mkdir -p docker/wheels
WHEELS=$(ls docker/wheels/*.whl 2>/dev/null | wc -l | tr -d ' ')
case "$ARCH" in
  x86_64|amd64)
    if [ "$WHEELS" -ge 50 ]; then
      ok "$WHEELS bundled wheels found (offline install)"
    else
      info "Bundled wheels missing ($WHEELS found) - trying to download them on the host"
      PY=""
      for c in python3 python; do
        if command -v "$c" >/dev/null 2>&1 && "$c" -m pip --version >/dev/null 2>&1; then PY="$c"; break; fi
      done
      if [ -z "$PY" ]; then
        warn "No host Python with pip found; the image build will download packages from PyPI instead."
      else
        run_logged pip-download "$PY" -m pip download -q -r docker/requirements.txt pytest==8.2.2 -d docker/wheels \
          --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux_2_28_x86_64 \
          --platform any --python-version 3.11 --implementation cp --only-binary=:all: --retries 5 --timeout 60
        if [ $RC -eq 0 ]; then ok "wheels downloaded"; else warn "wheel download failed (see $LOG); the build will fall back to PyPI."; fi
      fi
    fi ;;
  *) info "CPU architecture is $ARCH: bundled wheels are x86-64 only, the build will install from PyPI." ;;
esac

# ---------------------------------------------------------- 4. datasets
# name|file|min bytes|magic hex|url
DATASETS="gowalla|data/raw/loc-gowalla_totalCheckins.txt.gz|104857600|1f8b|https://snap.stanford.edu/data/loc-gowalla_totalCheckins.txt.gz
sentiment140|data/raw/sentiment140.zip|73400320|504b|https://cs.stanford.edu/people/alecmgo/trainingandtestdata.zip"
raw_ok() {  # file minbytes magic
  [ -f "$1" ] || return 1
  [ "$(wc -c < "$1" | tr -d ' ')" -ge "$2" ] || return 1
  [ "$(head -c 2 "$1" | od -An -tx1 | tr -d ' \n')" = "$3" ]
}
HAVE=" synthetic "
if [ $SKIP_DATA -eq 1 ]; then
  step "Datasets (skipped by --skip-data)"
else
  step "Datasets"
  mkdir -p data/raw data/processed
  while IFS='|' read -r name file min magic url; do
    if [ -f "data/processed/${name}_meta.json" ]; then ok "$name: already preprocessed"; HAVE="$HAVE$name "; continue; fi
    if raw_ok "$file" "$min" "$magic"; then ok "$name: raw file present"; HAVE="$HAVE$name "; continue; fi
    if [ -f "$file" ]; then warn "$name: existing file is truncated or corrupt - re-downloading"; rm -f "$file"; fi
    info "$name: downloading $url"
    if curl -L --fail --retry 3 --retry-delay 5 --connect-timeout 30 -o "$file.part" "$url"; then
      mv -f "$file.part" "$file"
      if raw_ok "$file" "$min" "$magic"; then ok "$name: downloaded and verified"; HAVE="$HAVE$name "
      else warn "$name: downloaded file failed verification (size/format). The server may be down; continuing without it."; fi
    else
      rm -f "$file.part"
      warn "$name: download failed. Download it manually from $url into $file and re-run. Continuing without it."
    fi
  done <<EOF
$DATASETS
EOF
fi

# ------------------------------------------------------------- 5. build
step "Building the app image"
run_logged build docker compose build app
if [ $RC -ne 0 ]; then
  if grep -Eq 'Name or service not known|Temporary failure in name resolution|Could not resolve|Failed to establish a new connection' "$LOG"; then
    warn "Network/DNS failure inside the Docker build - retrying once..."
    run_logged build-retry docker compose build app
    [ $RC -eq 0 ] || fail "Image build failed: the build container cannot reach PyPI." \
      "Fix network/VPN/proxy and re-run, or install Python 3 + pip on this machine so the script can download docker/wheels for an offline build."
  elif grep -q 'no space left on device' "$LOG"; then
    fail "Docker ran out of disk space during the build." "Run 'docker system prune' or give Docker more disk."
  elif grep -Eq 'pull access denied|TLS handshake timeout|i/o timeout' "$LOG"; then
    fail "Could not pull the python base image from Docker Hub." "Check your internet connection / Docker proxy settings and re-run."
  else
    fail "Image build failed (exit $RC)." "See $LOG"
  fi
fi
ok "image fedsketch-app:1.0 built"

# ------------------------------------------------------------- 6. start
step "Starting Kafka, InfluxDB, Grafana and the app container"
run_logged up docker compose up -d kafka influxdb grafana app
if [ $RC -ne 0 ]; then
  grep -Eq 'port is already allocated|address already in use' "$LOG" && \
    fail "A port is already in use." "Another container/program grabbed 3000, 8086 or 29092 - stop it or change docker-compose.yml."
  fail "docker compose up failed (exit $RC)." "See $LOG"
fi
wait_ready() {  # name seconds cmd...
  local name="$1" secs="$2"; shift 2
  local end=$(( $(date +%s) + secs ))
  while [ "$(date +%s)" -lt "$end" ]; do
    if "$@" >/dev/null 2>&1; then ok "$name is ready"; return 0; fi
    sleep 3
  done
  echo "  last log lines of $name:"; docker compose logs --tail 25 "$name"
  fail "$name did not become ready within $secs s." "Inspect with: docker compose logs $name"
}
wait_ready kafka 120 docker compose exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list
wait_ready influxdb 90 sh -c 'curl -fsS -o /dev/null -w "%{http_code}" http://localhost:8086/ping | grep -q 204'
wait_ready grafana 90 curl -fsS -o /dev/null http://localhost:3000/api/health
wait_ready app 60 docker compose exec -T app python -c "import flwr, dp_accounting, confluent_kafka"

# ------------------------------------------------------------- 7. tests
if [ $SKIP_TESTS -eq 1 ]; then step "Unit tests (skipped)"; else
  step "Running unit tests"
  run_logged tests docker compose exec -T app python -m pytest -q tests
  [ $RC -eq 0 ] || fail "Unit tests failed." "See $LOG"
  ok "unit tests passed"
fi

# ----------------------------------------------------------- 8. prepare
step "Preprocessing datasets"
for n in synthetic gowalla sentiment140; do
  case "$HAVE" in *" $n "*) ;; *) continue ;; esac
  if [ -f "data/processed/${n}_meta.json" ]; then ok "$n: already done"; continue; fi
  info "$n: preprocessing (1-3 min)..."
  run_logged "prepare-$n" docker compose exec -T app python -m experiments.prepare "$n"
  [ $RC -eq 137 ] && fail "$n: preprocessing was killed (out of memory)." "Give Docker more memory (6 GB+) and re-run."
  [ $RC -eq 0 ] || fail "$n: preprocessing failed (exit $RC)." "See $LOG"
  ok "$n: done"
done

# ------------------------------------------------------------ 9. demo
cfg_get() { sed -n "s/.*\"$1\": *\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" pipeline/config.json | head -n1; }
if [ $DEMO -eq 1 ]; then
  step "Starting the live pipeline demo"
  DS="$(cfg_get dataset)"; ROUNDS="$(cfg_get max_rounds)"; SPR="$(cfg_get seconds_per_round)"
  [ -f "data/processed/${DS}_meta.json" ] || fail "pipeline/config.json uses dataset '$DS', which is not preprocessed." \
    "Re-run without --skip-data, or set \"dataset\" to \"synthetic\" in pipeline/config.json."
  run_logged demo-topics docker compose --profile pipeline up -d --force-recreate --no-deps topics
  CODE="$(docker wait fedsketch-topics-1 2>/dev/null)"
  if [ $RC -ne 0 ] || [ "$CODE" != "0" ]; then
    docker compose logs --tail 20 topics
    fail "Creating the Kafka topics failed." "See the log above; is Kafka healthy? (docker compose logs kafka)"
  fi
  ok "Kafka topics (re)created"
  run_logged demo-up docker compose --profile pipeline up -d --force-recreate --no-deps \
    producer oracle fl-server edge-0 edge-1 edge-2 edge-3 edge-4 edge-5 edge-6 edge-7
  [ $RC -eq 0 ] || fail "Starting the pipeline services failed." "See $LOG"
  sleep 20
  EXITED="$(docker compose --profile pipeline ps --status exited --services 2>/dev/null | grep -vx topics)"
  if [ -n "$EXITED" ]; then
    for s in $EXITED; do echo "  --- $s ---"; docker compose logs --tail 15 "$s"; done
    fail "Pipeline service(s) exited early: $(echo $EXITED)" "See the logs above."
  fi
  MINS=$(awk -v r="$ROUNDS" -v s="$SPR" 'BEGIN{printf "%d", (r*s+59)/60}')
  ok "pipeline running: $ROUNDS rounds of '$DS' at $SPR s/round (~$MINS min)"
  info "follow it with:  docker compose logs -f fl-server oracle"
  if [ $NO_BROWSER -ne 1 ]; then
    URL="http://localhost:3000/d/fedsketch"
    if command -v open >/dev/null 2>&1 && [ "$(uname -s)" = "Darwin" ]; then open "$URL"
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 &
    fi
  fi
fi

# ------------------------------------------------------- 10. experiments
if [ $EXPERIMENTS -eq 1 ]; then
  step "Running all experiments (this takes 20+ minutes)"
  run_logged experiments docker compose exec -T app sh experiments/run_all.sh
  [ $RC -eq 0 ] || fail "Some experiments failed." "Check results/logs/*.log"
  run_logged plots docker compose exec -T app python -m experiments.plots
  run_logged tables docker compose exec -T app python -m experiments.tables
  ok "results in results/ (CSV), results/figures/, report/tables/"
fi

# ------------------------------------------------------------- summary
step "Ready"
echo "  Grafana dashboard : http://localhost:3000/d/fedsketch   (anonymous admin)"
echo "  Toolbox shell     : docker compose exec app bash"
echo "  Live demo         : ./setup.sh --demo"
echo "  All experiments   : ./setup.sh --experiments"
echo "  Stop / remove     : ./setup.sh --stop   |   ./setup.sh --clean"
echo "  Logs of this run  : $LOG_DIR"
