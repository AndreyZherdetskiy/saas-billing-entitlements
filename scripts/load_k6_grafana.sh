#!/usr/bin/env bash
# k6 → Prometheus remote write on Compose network (Grafana dashboard 19665).
# Fail-closed if docker network missing. Does not publish Prometheus :9090.
# Script delivery: k6 run - (stdin) — Docker Desktop WSL bind-mount of docs/perf
# is often empty (same class of issue as baked observability configs).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SCRIPT="${LOAD_SCRIPT:-k6_evaluate_peak.js}"
NETWORK="${COMPOSE_PROJECT:-billing-platform}"
HOST_SCRIPT="$ROOT/docs/perf/$SCRIPT"

test -n "${K6_API_KEY:-}" || { echo "Set K6_API_KEY" >&2; exit 1; }
test -n "${K6_ORG_ID:-}" || { echo "Set K6_ORG_ID" >&2; exit 1; }
test -f "$HOST_SCRIPT" || {
	echo "ERROR: missing $HOST_SCRIPT" >&2
	exit 1
}

docker network inspect "$NETWORK" >/dev/null 2>&1 || {
	echo "ERROR: docker network $NETWORK missing; run make observability-up first" >&2
	exit 1
}

# Explicit -e KEY=value: Docker Desktop (desktop-linux) often drops bare -e KEY
# pass-through from the WSL client environment.
k6_env=(
	-e "K6_API_KEY=${K6_API_KEY}"
	-e "K6_ORG_ID=${K6_ORG_ID}"
	-e "K6_FEATURE_KEY=${K6_FEATURE_KEY:-}"
	-e BASE_URL=http://billing-api:8000
	-e "K6_PROFILE=${K6_PROFILE:-smoke}"
	-e K6_PROMETHEUS_RW_SERVER_URL=http://prometheus:9090/api/v1/write
	-e "K6_PROMETHEUS_RW_TREND_STATS=${K6_PROMETHEUS_RW_TREND_STATS:-p(95),p(99),avg,min,max}"
)

docker run --rm -i --network "$NETWORK" \
	"${k6_env[@]}" \
	grafana/k6 run -o experimental-prometheus-rw --tag "testid=${K6_TESTID:-k6-grafana-smoke}" \
	- <"$HOST_SCRIPT"
