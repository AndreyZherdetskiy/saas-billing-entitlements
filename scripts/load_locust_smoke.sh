#!/usr/bin/env bash
# Fail-closed Locust smoke: preflight credentials + /health/ready, then headless run.
# Optional OTEL: LOAD_LOCUST_OTEL=1 → --otel → Alloy OTLP HTTP :4318 (host).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOAD_HOST="${LOAD_HOST:-${BASE_URL:-http://localhost:8000}}"
# Default 5 so weighted Evaluate/Usage/Admin users can all spawn in a short smoke.
LOAD_USERS="${LOAD_USERS:-5}"
LOAD_SPAWN_RATE="${LOAD_SPAWN_RATE:-1}"
LOAD_RUN_TIME="${LOAD_RUN_TIME:-10s}"
LOAD_HTML="${LOAD_HTML:-.local/locust/smoke.html}"
LOAD_CSV="${LOAD_CSV:-.local/locust/smoke}"

mkdir -p "$(dirname "$LOAD_HTML")" "$(dirname "$LOAD_CSV")"

log() { printf '[load-locust] %s\n' "$*"; }

log "preflight (credentials + /health/ready) host=$LOAD_HOST ..."
LOAD_HOST="$LOAD_HOST" uv run python -m loadtests.preflight || exit 1

LOCUST_OTEL_ARGS=()
if [[ "${LOAD_LOCUST_OTEL:-0}" == "1" ]]; then
	COMPOSE_NETWORK="${COMPOSE_PROJECT:-billing-platform}"
	docker network inspect "$COMPOSE_NETWORK" >/dev/null 2>&1 || {
		echo "ERROR: docker network $COMPOSE_NETWORK missing; run make observability-up first" >&2
		exit 1
	}
	if ! timeout 2 bash -c 'echo >/dev/tcp/127.0.0.1/4318' 2>/dev/null; then
		echo "ERROR: Alloy OTLP HTTP :4318 unreachable; run make observability-up first" >&2
		exit 1
	fi
	LOCUST_OTEL_ARGS+=(--otel)
	# .env / make load-* default OTEL_SDK_DISABLED=true for billing-api; Locust host
	# process must enable the SDK or --otel becomes a no-op exporter.
	export OTEL_SDK_DISABLED=false
	export OTEL_SERVICE_NAME="${OTEL_SERVICE_NAME:-locust}"
	export OTEL_TRACES_EXPORTER="${OTEL_TRACES_EXPORTER:-otlp}"
	export OTEL_METRICS_EXPORTER="${OTEL_METRICS_EXPORTER:-otlp}"
	export OTEL_EXPORTER_OTLP_PROTOCOL="${OTEL_EXPORTER_OTLP_PROTOCOL:-http/protobuf}"
	export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://127.0.0.1:4318}"
	# Prefer cumulative for Alloy → Prometheus remote_write compatibility.
	export OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE="${OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE:-cumulative}"
	export OTEL_METRIC_EXPORT_INTERVAL="${OTEL_METRIC_EXPORT_INTERVAL:-5000}"
	log "otel enabled endpoint=$OTEL_EXPORTER_OTLP_ENDPOINT sdk_disabled=$OTEL_SDK_DISABLED"
fi

log "running locust host=$LOAD_HOST users=$LOAD_USERS duration=$LOAD_RUN_TIME"
set +e
uv run --group load locust -f loadtests/locustfile.py --headless \
	--host "$LOAD_HOST" -u "$LOAD_USERS" -r "$LOAD_SPAWN_RATE" -t "$LOAD_RUN_TIME" \
	--exit-code-on-error 1 \
	--html "$LOAD_HTML" \
	--csv "$LOAD_CSV" \
	"${LOCUST_OTEL_ARGS[@]}"
locust_exit=$?
set -e

if [[ "$locust_exit" -ne 0 ]]; then
	log "FAIL: locust exited $locust_exit"
	exit "$locust_exit"
fi

log "PASS html=$LOAD_HTML csv_prefix=$LOAD_CSV"
exit 0
