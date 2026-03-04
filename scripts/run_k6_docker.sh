#!/usr/bin/env bash
# Docker k6 on the Compose network (service DNS billing-api), not host NAT.
# Script delivery: k6 run - (stdin) — Grafana Docker docs; WSL bind-mounts of
# docs/perf often appear as empty directories inside the container.
# Env: explicit -e KEY=value (Docker Desktop drops bare -e KEY pass-through).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SCRIPT="${1:-${LOAD_SCRIPT:-k6_evaluate_peak.js}}"
HOST_SCRIPT="$ROOT/docs/perf/$SCRIPT"
NETWORK="${COMPOSE_PROJECT:-billing-platform}"
docker_base="${K6_DOCKER_BASE_URL:-http://billing-api:8000}"

test -n "${K6_API_KEY:-}" || { echo "Set K6_API_KEY" >&2; exit 1; }
test -n "${K6_ORG_ID:-}" || { echo "Set K6_ORG_ID" >&2; exit 1; }
test -f "$HOST_SCRIPT" || {
	echo "ERROR: missing $HOST_SCRIPT" >&2
	exit 1
}

docker network inspect "$NETWORK" >/dev/null 2>&1 || {
	echo "ERROR: docker network $NETWORK missing; run make compose-core first" >&2
	exit 1
}

k6_env=(
	-e "K6_API_KEY=${K6_API_KEY}"
	-e "K6_ORG_ID=${K6_ORG_ID}"
	-e "K6_FEATURE_KEY=${K6_FEATURE_KEY:-}"
	-e "BASE_URL=${docker_base}"
	-e "K6_PROFILE=${K6_PROFILE:-smoke}"
)
[[ -n "${K6_BATCH_SIZE:-}" ]] && k6_env+=(-e "K6_BATCH_SIZE=${K6_BATCH_SIZE}")
[[ -n "${K6_SOAK_DURATION:-}" ]] && k6_env+=(-e "K6_SOAK_DURATION=${K6_SOAK_DURATION}")
[[ -n "${K6_CEILING_RPS:-}" ]] && k6_env+=(-e "K6_CEILING_RPS=${K6_CEILING_RPS}")
[[ -n "${TARGET_RPS:-}" ]] && k6_env+=(-e "TARGET_RPS=${TARGET_RPS}")
[[ -n "${DURATION:-}" ]] && k6_env+=(-e "DURATION=${DURATION}")
[[ -n "${MAX_VUS:-}" ]] && k6_env+=(-e "MAX_VUS=${MAX_VUS}")

extra=()
if [[ -n "${K6_EXTRA_ARGS:-}" ]]; then
	# Intentional word-split of official k6 flags (e.g. --no-thresholds).
	# shellcheck disable=SC2206
	extra=(${K6_EXTRA_ARGS})
fi

docker run --rm -i --network "$NETWORK" \
	"${k6_env[@]}" \
	grafana/k6 run "${extra[@]}" - <"$HOST_SCRIPT"
