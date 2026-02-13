.PHONY: help lint typecheck test-unit test-integration test \
	compose-core compose-up compose-all compose-down compose-ps compose-prune \
	observability-up observability-ps perf-up stack-up stack-down \
	load-a load-b load-c load-d load-e load-smoke-all load-smoke-all-soft \
	load-a-full load-b-full load-c-full load-d-full load-e-full \
	load-a-docker load-perf-env load-locust load-locust-ui \
	load-locust-otel load-k6-grafana

# Host-side env (K6_*, DEMO_UI_*, BASE_URL, …). File is gitignored.
ifneq (,$(wildcard .env))
include .env
export
endif

K6 ?= k6
# Auto-fallback to Docker k6 when host binary missing (override: K6=/path/to/k6 on CLI/env).
ifeq ($(filter command environment,$(origin K6)),)
ifneq ($(shell command -v k6 2>/dev/null),)
K6 := $(shell command -v k6 2>/dev/null)
else
K6_USE_DOCKER := 1
endif
endif
K6_PROFILE ?= smoke
K6_EXTRA_ARGS ?=
BASE_URL ?= http://localhost:8000
K6_DOCKER_BASE_URL ?= http://billing-api:8000
K6_SOAK_DURATION ?=
K6_BATCH_SIZE ?=
K6_CEILING_RPS ?=
LOAD_SCRIPT ?= k6_evaluate_peak.js
COMPOSE_FILE ?= deploy/compose/docker-compose.yml
COMPOSE_PERF_FILE ?= deploy/compose/docker-compose.perf.yml
# Compose paths are relative to deploy/compose/ — do not set --project-directory to repo root.
# Explicit -p beats COMPOSE_PROJECT_NAME env so sibling stacks never collide.
COMPOSE_PROJECT ?= billing-platform
COMPOSE ?= docker compose -p $(COMPOSE_PROJECT) --env-file $(CURDIR)/.env
COMPOSE_PROFILES_ALL := postgres-replica pgbouncer pgbouncer-replica observability
COMPOSE_PRUNE_DANGLING ?= 1
# Local perf only — never use in prod. See docs/perf/README.md.
LOAD_API_RATE_LIMIT_PER_MINUTE ?= 0
LOAD_API_RATE_LIMIT_PLATFORM_ADMIN_PER_MINUTE ?= 0
LOAD_OTEL_SDK_DISABLED ?= true
LOAD_UVICORN_WORKERS ?= 4
LOAD_DATABASE_POOL_SIZE ?= 8
LOAD_DATABASE_MAX_OVERFLOW ?= 4

help:
	@echo "Quality:"
	@echo "  make lint                 ruff check + format check"
	@echo "  make typecheck            mypy on src/"
	@echo "  make test-unit            pytest unit (Docker required)"
	@echo "  make test-integration     pytest integration (Testcontainers; excludes live_compose)"
	@echo "  make test                 lint + typecheck + unit + integration"
	@echo ""
	@echo "Compose (pick ONE — profiles are opt-in by design):"
	@echo "  make compose-core         CORE app only (PG primary, Redis, Kafka, API, worker,"
	@echo "                            beat, relay, mock-stripe, kafbat-ui, demo-ui)"
	@echo "  make compose-up           alias → compose-core"
	@echo "  make compose-all          EVERYTHING: core + PG replica + PgBouncer +"
	@echo "                            PgBouncer-RO + LGTP observability (Grafana :3000)"
	@echo "  make observability-up     CORE + monitoring profile only (no replica/bouncer)"
	@echo "  make compose-ps           docker compose ps"
	@echo "  make compose-down         stop all profiles (core + observability + replica/bouncer)"
	@echo "  make perf-up              core + perf overlay (4 workers, pool 2+1, relay×2; not default)"
	@echo "  make compose-prune        project-labeled dangling image prune"
	@echo ""
	@echo "Load (k6 — auto Docker fallback if host k6 missing):"
	@echo "  make load-perf-env        compose-core + disable rate limits + OTel off on API"
	@echo "  make load-a … load-e      smoke profile (default)"
	@echo "  make load-smoke-all       A→E sequentially"
	@echo "  make load-smoke-all-soft  same with --no-thresholds"
	@echo "  make load-a-full …        full §8.1.1 intensity"
	@echo "  make load-a-docker        explicit Docker k6 (LOAD_SCRIPT= override)"
	@echo ""
	@echo "Load (Locust — Python; does not replace k6):"
	@echo "  make load-locust          headless smoke (5 users / 10s)"
	@echo "  make load-locust-ui       Web UI on :8089 (fails if port busy)"
	@echo "  make load-locust-otel     Locust + --otel → Alloy :4318 (needs observability-up)"
	@echo "  make load-k6-grafana      k6 Prometheus RW on compose net (needs observability-up)"
	@echo ""
	@echo "Env: repo-root .env (see .env.example). Profiles/DSN: docs/runbooks/local-compose-profiles.md"

lint:
	uv run ruff check src tests loadtests && uv run ruff format --check src tests loadtests

typecheck:
	uv run mypy src

test-unit:
	@docker info >/dev/null 2>&1 || ( \
		echo "ERROR: Docker is required for make test-unit." >&2; \
		echo "Many unit tests use PostgresContainer; without Docker they skip and --cov-fail-under=80 fails (~51%)." >&2; \
		exit 1; \
	)
	uv run pytest tests/unit -q --cov=billing_platform.services --cov=billing_platform.domain --cov-fail-under=80

# Host pytest + Testcontainers. Live API smoke is `make load-locust`, not this target.
test-integration:
	@docker info >/dev/null 2>&1 || ( \
		echo "ERROR: Docker is required for make test-integration." >&2; \
		echo "HTTP tests use Testcontainers; compose YAML tests need docker compose; Helm tests need helm." >&2; \
		exit 1; \
	)
	uv run pytest tests/integration -q -m "integration and not live_compose"

test: lint typecheck test-unit test-integration

# Compose entry points — profile details: docs/runbooks/local-compose-profiles.md
_compose_up_build:
	$(COMPOSE) $(COMPOSE_PROFILE_FLAGS) -f $(COMPOSE_FILE) up -d --build --wait --remove-orphans
	@$(MAKE) compose-prune

compose-core:
	@echo ">>> compose-core: application stack (no optional profiles)"
	@$(MAKE) _compose_up_build COMPOSE_PROFILE_FLAGS=

compose-up: compose-core

stack-up: compose-core

perf-up:
	@echo ">>> core + docker-compose.perf.yml (4 API workers, pool 2+1, scale outbox-relay=2)"
	$(COMPOSE) -f $(COMPOSE_FILE) -f deploy/compose/docker-compose.perf.yml up -d --build --wait --wait-timeout 180 --remove-orphans --scale outbox-relay=2

compose-all:
	@echo ">>> compose-all: core + replica + pgbouncer + observability"
	@if [ -z "$$DATABASE_READ_URL" ]; then \
		echo "Note: DATABASE_READ_URL is empty — replica is up, but API will keep using primary until you set it in .env and recreate billing-api."; \
	fi
	OTEL_SDK_DISABLED=false OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318 \
		$(MAKE) _compose_up_build \
		COMPOSE_PROFILE_FLAGS="$(foreach p,$(COMPOSE_PROFILES_ALL),--profile $(p))"

compose-ps:
	$(COMPOSE) -f $(COMPOSE_FILE) ps -a

compose-down:
	@echo ">>> all profiles + perf overlay (volumes kept)"
	$(COMPOSE) $(foreach p,$(COMPOSE_PROFILES_ALL),--profile $(p)) -f $(COMPOSE_FILE) -f deploy/compose/docker-compose.perf.yml down --remove-orphans

stack-down: compose-down

compose-prune:
ifeq ($(COMPOSE_PRUNE_DANGLING),1)
	docker image prune -af --filter "label=com.docker.compose.project=$(COMPOSE_PROJECT)"
else
	@echo "Skipping dangling image prune (COMPOSE_PRUNE_DANGLING=$(COMPOSE_PRUNE_DANGLING))"
endif

observability-up:
	@echo ">>> observability-up: core + LGTP (no postgres-replica / pgbouncer)"
	OTEL_SDK_DISABLED=false OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318 \
		$(MAKE) _compose_up_build COMPOSE_PROFILE_FLAGS="--profile observability"

observability-ps:
	$(COMPOSE) --profile observability -f $(COMPOSE_FILE) ps -a

# Load (k6) — docs/perf/README.md

_load_env_check:
	@test -n "$$K6_API_KEY" || (echo "Set K6_API_KEY (seed: .local/seed-output.json → platform_admin_key)" >&2; exit 1)
	@test -n "$$K6_ORG_ID" || (echo "Set K6_ORG_ID (seed: .local/seed-output.json → organization_public_id)" >&2; exit 1)

LOAD_PERF_ENV_FILE ?= $(CURDIR)/.local/load-perf.env

# Recreate billing-api with rate limits + OTel off for local k6 (perf only).
# Compose v5: explicit --env-file wins over a one-shot shell prefix, so overlay
# via a second --env-file (later file overrides; Compose interpolation docs).
# OTLP under load: LOAD_OTEL_SDK_DISABLED=false OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318 make load-a
_load_perf_rate_limits:
	@mkdir -p $(dir $(LOAD_PERF_ENV_FILE))
	@printf '%s\n' \
		'API_RATE_LIMIT_PER_MINUTE=$(LOAD_API_RATE_LIMIT_PER_MINUTE)' \
		'API_RATE_LIMIT_PLATFORM_ADMIN_PER_MINUTE=$(LOAD_API_RATE_LIMIT_PLATFORM_ADMIN_PER_MINUTE)' \
		'OTEL_SDK_DISABLED=$(LOAD_OTEL_SDK_DISABLED)' \
		'UVICORN_WORKERS=$(LOAD_UVICORN_WORKERS)' \
		'DATABASE_POOL_SIZE=$(LOAD_DATABASE_POOL_SIZE)' \
		'DATABASE_MAX_OVERFLOW=$(LOAD_DATABASE_MAX_OVERFLOW)' \
		$(if $(filter false,$(LOAD_OTEL_SDK_DISABLED)),OTEL_EXPORTER_OTLP_ENDPOINT=$(or $(OTEL_EXPORTER_OTLP_ENDPOINT),http://alloy:4318),) \
		> $(LOAD_PERF_ENV_FILE)
	$(COMPOSE) --env-file $(LOAD_PERF_ENV_FILE) -f $(COMPOSE_FILE) \
		up -d --no-deps --force-recreate --wait --wait-timeout 120 billing-api

load-perf-env: compose-core _load_perf_rate_limits
	@echo "Perf env: API_RATE_LIMIT_*=$(LOAD_API_RATE_LIMIT_PER_MINUTE), OTEL_SDK_DISABLED=$(LOAD_OTEL_SDK_DISABLED) on billing-api (local load only)."

# Host k6 or Docker fallback (K6_USE_DOCKER=1 when host binary missing).
# Docker path: stdin (`k6 run -`) — Grafana Docker docs; WSL bind-mounts break.
define _k6_run
	$(if $(K6_USE_DOCKER),\
		K6_PROFILE=$(K6_PROFILE) K6_DOCKER_BASE_URL=$(K6_DOCKER_BASE_URL) \
		K6_EXTRA_ARGS=$(K6_EXTRA_ARGS) \
		$(if $(K6_BATCH_SIZE),K6_BATCH_SIZE=$(K6_BATCH_SIZE),) \
		$(if $(K6_SOAK_DURATION),K6_SOAK_DURATION=$(K6_SOAK_DURATION),) \
		$(if $(K6_CEILING_RPS),K6_CEILING_RPS=$(K6_CEILING_RPS),) \
		./scripts/run_k6_docker.sh $(1),\
		K6_PROFILE=$(K6_PROFILE) BASE_URL=$(BASE_URL) \
		$(if $(K6_BATCH_SIZE),K6_BATCH_SIZE=$(K6_BATCH_SIZE),) \
		$(if $(K6_SOAK_DURATION),K6_SOAK_DURATION=$(K6_SOAK_DURATION),) \
		$(if $(K6_CEILING_RPS),K6_CEILING_RPS=$(K6_CEILING_RPS),) \
		$(K6) run $(K6_EXTRA_ARGS) docs/perf/$(1))
endef

load-a: _load_env_check _load_perf_rate_limits
	@$(call _k6_run,k6_evaluate_peak.js)

load-b: _load_env_check _load_perf_rate_limits
	@$(call _k6_run,k6_usage_ingest.js)

load-c: _load_env_check _load_perf_rate_limits
	@$(call _k6_run,k6_mixed.js)

load-d: _load_env_check _load_perf_rate_limits
	@$(call _k6_run,k6_soak.js)

load-e: _load_env_check _load_perf_rate_limits
	@$(call _k6_run,k6_ceiling.js)

load-smoke-all: _load_env_check _load_perf_rate_limits
	$(MAKE) load-a K6_PROFILE=smoke
	$(MAKE) load-b K6_PROFILE=smoke
	$(MAKE) load-c K6_PROFILE=smoke
	$(MAKE) load-d K6_PROFILE=smoke
	$(MAKE) load-e K6_PROFILE=smoke

# Escape hatch: skip k6 thresholds entirely (e.g. route smoke on a very slow laptop).
load-smoke-all-soft: _load_env_check _load_perf_rate_limits
	$(MAKE) load-a K6_PROFILE=smoke K6_EXTRA_ARGS=--no-thresholds
	$(MAKE) load-b K6_PROFILE=smoke K6_EXTRA_ARGS=--no-thresholds
	$(MAKE) load-c K6_PROFILE=smoke K6_EXTRA_ARGS=--no-thresholds
	$(MAKE) load-d K6_PROFILE=smoke K6_EXTRA_ARGS=--no-thresholds
	$(MAKE) load-e K6_PROFILE=smoke K6_EXTRA_ARGS=--no-thresholds

load-a-full:
	$(MAKE) load-a K6_PROFILE=full

load-b-full:
	$(MAKE) load-b K6_PROFILE=full

load-c-full:
	$(MAKE) load-c K6_PROFILE=full

load-d-full:
	$(MAKE) load-d K6_PROFILE=full K6_SOAK_DURATION=$(or $(K6_SOAK_DURATION),30m)

load-e-full:
	$(MAKE) load-e K6_PROFILE=full

# Docker k6 → host API. Override: make load-a-docker LOAD_SCRIPT=k6_mixed.js
load-a-docker: _load_env_check _load_perf_rate_limits
	K6_PROFILE=$(K6_PROFILE) K6_DOCKER_BASE_URL=$(K6_DOCKER_BASE_URL) \
		K6_EXTRA_ARGS=$(K6_EXTRA_ARGS) \
		./scripts/run_k6_docker.sh $(LOAD_SCRIPT)

load-locust: _load_env_check _load_perf_rate_limits
	@mkdir -p .local/locust
	./scripts/load_locust_smoke.sh

load-locust-otel: _load_env_check _load_perf_rate_limits
	@mkdir -p .local/locust
	LOAD_LOCUST_OTEL=1 ./scripts/load_locust_smoke.sh

load-k6-grafana: _load_env_check _load_perf_rate_limits
	./scripts/load_k6_grafana.sh

load-locust-ui: _load_env_check _load_perf_rate_limits
	@if command -v ss >/dev/null 2>&1; then \
		if ss -ltn | grep -qE ':8089([[:space:]]|$$)'; then \
			echo "ERROR: port 8089 is busy; free it or skip UI. Do not remap app ports." >&2; \
			exit 1; \
		fi; \
	else \
		uv run python -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',8089)); s.close()" \
			|| { echo "ERROR: port 8089 is busy; free it or skip UI. Do not remap app ports." >&2; exit 1; }; \
	fi
	uv run --group load locust -f loadtests/locustfile.py --host $(BASE_URL)
