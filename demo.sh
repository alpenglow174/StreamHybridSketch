#!/usr/bin/env bash
# Fast restart of the live demo for recording (Linux/macOS). Run ./setup.sh once first.
# Recreates the Kafka topics, starts producer + 8 edges + Flower server + evaluator, opens Grafana.
set -u
cd "$(dirname "$0")" || exit 1
fail() { printf '[FAIL] %s\n' "$1"; [ -n "${2:-}" ] && printf '       -> %s\n' "$2"; exit 1; }

docker info >/dev/null 2>&1 || fail "Docker is not running." "Start Docker, then run ./setup.sh once."
RUNNING="$(docker compose ps --status running --services 2>/dev/null)"
for svc in kafka influxdb grafana; do
  printf '%s\n' "$RUNNING" | grep -qx "$svc" || fail "The $svc container is not running." "Run ./setup.sh first."
done
DS="$(sed -n 's/.*"dataset": *"\([^"]*\)".*/\1/p' pipeline/config.json | head -n1)"
ROUNDS="$(sed -n 's/.*"max_rounds": *\([0-9]*\).*/\1/p' pipeline/config.json | head -n1)"
[ -f "data/processed/${DS}_meta.json" ] || fail "Dataset '$DS' is not preprocessed." "Run ./setup.sh first."

docker compose --profile pipeline up -d --force-recreate --no-deps topics >/dev/null 2>&1
[ "$(docker wait fedsketch-topics-1)" = "0" ] || { docker compose logs --tail 20 topics; fail "Creating Kafka topics failed."; }
docker compose --profile pipeline up -d --force-recreate --no-deps \
  producer oracle fl-server edge-0 edge-1 edge-2 edge-3 edge-4 edge-5 edge-6 edge-7 >/dev/null 2>&1 \
  || fail "Starting the pipeline failed." "Run ./setup.sh --demo for a full check."
URL="http://localhost:3000/d/fedsketch?orgId=1&from=now-5m&to=now&refresh=5s"
if [ "$(uname -s)" = "Darwin" ]; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 & fi
echo "[ok] demo running: $ROUNDS rounds of '$DS' (~$(( ROUNDS * 82 / 100 )) s). Drifts at rounds 70 and 140."
echo "     follow in a terminal:  docker compose logs -f fl-server oracle"
