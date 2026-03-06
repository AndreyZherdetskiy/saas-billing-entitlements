# Locust Load Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed Locust smoke harness alongside existing k6 profiles A–E, wire both generators into the opt-in Grafana LGTP stack, and gate PRs with CI plus pre-commit — without claiming §8.1.1 12k RPS DoD from a laptop.

**Architecture:** Locust stays a host-side Python extra (`uv` group `load`, repo-root `loadtests/`) that reuses `K6_*` / `BASE_URL` credentials and the same three HTTP paths as k6 mixed smoke (evaluate / usage ingest / admin usage read). Grafana remains ADR-013 opt-in: Locust uses official `--otel` → Alloy OTLP HTTP :4318; k6 uses official `experimental-prometheus-rw` on the Compose network to Prometheus (already `--web.enable-remote-write-receiver`). Default `make load-*` and `make compose-core` stay Grafana-free.

**Tech Stack:** Locust `>=2.32,<3` (installed 2.46.x) with optional `[otel]` extra; existing k6 scripts in `docs/perf/`; Compose project `billing-platform`; Grafana 11.5.2 + Prometheus v3.2.1 + Alloy v1.7.5; GitHub Actions; pre-commit + `astral-sh/ruff-pre-commit` pinned to lockfile Ruff **0.8.6**.

## Global Constraints

- Locust is **additive**. k6 remains the §8.1.1 DoD tool. Locust smoke is **not** 12k evaluate RPS proof. Do not delete or rewrite k6 scripts.
- Tool: k6 (preferred) or Locust — `spec.md` §8.1.1 / §5. Docs language: tracked docs are professional English (`AGENTS.md` §2.12).
- Fail-closed: missing `K6_API_KEY`/`K6_ORG_ID` (or `LOAD_*` overrides), API `/health/ready` not HTTP 200, zero HTTP requests, Locust non-zero exit, Locust UI if host **8089** busy. Do not remap 8000/8001/8080/8081/3000/4317/4318.
- Do not publish Prometheus :9090 on the host (collision risk). k6 remote-write uses Compose network `billing-platform` → `http://prometheus:9090/api/v1/write`.
- Observability stays profile `observability`. Default `make load-*` keeps `OTEL_SDK_DISABLED=true` on **billing-api**. Locust/k6 Grafana paths are extra Make targets.
- No locust-plugins, no locust_exporter sidecar. Official Locust `--otel` + existing Alloy (`docs.locust.io/en/stable/telemetry.html`).
- k6 Grafana: official `experimental-prometheus-rw` + Grafana dashboard **19665** (Counter/Gauge trends). Do **not** enable Prometheus native histograms in this change.
- `uv` group `load` stays separate from `dev`. HTML/CSV under `.local/locust/` (gitignored via `.local/`).
- CI load jobs are **separate** from unit/integration (`spec.md` §10 / Test role). Demo keys from `.env.example` only — no secrets in git.
- Pre-commit: no Docker, no live API. Ruff check+format; locustfile import. Hook rev `v0.8.6` matches `uv.lock`.
- Quality gates unchanged: ruff 0, mypy strict 0, unit cov ≥ 80% services+domain. `loadtests/` is not part of that coverage gate.
- Dual-id: Locust/k6 send `organization_public_id` / org UUID — never BIGINT `id`.
- No dual-write, no rights-from-Kafka, ledger append-only, tenant isolation, PaymentProviderPort — load tests do not change domain code.
- Local-only: no `git commit` / `git push` / `gh` unless the human asked (they have not). Repo may have no commits yet — review via working-tree diffs; do not create a first commit.
- Do not change Cursor allowlists or global settings.
- Work **this project only**. If another numbered-project Compose stack is up, `docker compose -p <name> down` that stack only (no system prune, no `-v` unless the brief says so). After a live task that does not need the stack for the next task, `docker compose -p billing-platform -f deploy/compose/docker-compose.yml down`.
- Container failures: read service logs and fix configuration (env, wait, recreate). No port kludges.
- Ground Locust against https://docs.locust.io/en/2.46.3/ (writing-a-locustfile, running-without-web-ui, configuration, telemetry). Ground k6 RW against https://grafana.com/docs/k6/latest/results-output/real-time/prometheus-remote-write/. Ground Ruff hooks against https://docs.astral.sh/ruff/integrations/#pre-commit.
- SDD artifacts: briefs/reports/review packages → `.superpowers/sdd/locust-*` (gitignored). Plan/runbooks/perf reports → `docs/` (tracked). Do not leave `Task N` in `src/`, Compose, or scripts after a task ships.
- Implementer ≠ Reviewer. No self-APPROVE. No Stage Done declaration.
- When `docs/` changes, update matching `AGENTS.md` sections in the same task (`AGENTS.md` §0.3).

## Git vs gitignore

| Tracked (git) | Ignored |
|---------------|---------|
| `loadtests/`, `scripts/load_locust_smoke.sh`, `Makefile`, `pyproject.toml`, `uv.lock`, `.env.example` | `.env`, `.venv/`, `.local/` (Locust HTML/CSV) |
| `docs/plans/2026-03-05-locust-load-testing.md`, `docs/perf/*`, `docs/runbooks/load-locust.md` | `.superpowers/` (briefs, reports, ledger) |
| `.github/workflows/ci.yml`, `.pre-commit-config.yaml` | `.coverage`, `__pycache__/`, `.ruff_cache/` |

---

### Task 1: Load helper package and unit tests

**Status:** complete (working tree; no commit). Do not re-implement.

**Files:** `loadtests/__init__.py`, `loadtests/config.py`, `loadtests/preflight.py`, `tests/unit/test_load_helpers.py`

**Interfaces:**
- Consumes: `LOAD_*`, `K6_*`, `BASE_URL`
- Produces: `load_host()`, `load_api_key()`, `load_org_id()`, `load_feature_key()`, `PreflightError`, `assert_minimum_requests()`, `preflight_credentials()`, `preflight_api_ready()`, `run_smoke_preflight()`, `main()`

---

### Task 2: Locustfile, uv load group, Make wrapper

**Status:** complete (working tree; port-8089 fail-closed fix included). Do not re-implement.

**Files:** `loadtests/locustfile.py`, `scripts/load_locust_smoke.sh`, `pyproject.toml` `[dependency-groups] load`, `Makefile` `load-locust` / `load-locust-ui`, `.env.example` Locust comment block

**Interfaces:**
- Consumes: Task 1 helpers
- Produces: `EvaluateUser` (weight 9), `UsageIngestUser` (4), `AdminReadUser` (2); `make load-locust`; `make load-locust-ui`

Official flags in smoke script: `--headless`, `-u`, `-r`, `-t`, `--host`, `--exit-code-on-error 1`, `--html`, `--csv` (https://docs.locust.io/en/2.46.3/configuration.html and running-without-web-ui).

---

### Task 3: Live Compose smoke, report, AGENTS/README sync

**Files:**
- Create: `docs/perf/locust-smoke-report.md`
- Create: `docs/runbooks/load-locust.md`
- Modify: `docs/perf/README.md` (Locust subsection after the k6 table; keep k6 table intact)
- Modify: `docs/runbooks/README.md` (index row)
- Modify: `AGENTS.md` §0.2 / §0.3 / §8 / §10.1
- Modify: `README.md` only where load testing is listed (Locust additive; do not remove k6)
- Modify: `.superpowers/sdd/progress.md` (locust Task 3 row)

**Interfaces:**
- Consumes: Task 2 `make load-locust`
- Produces: honest smoke report with commands and numbers; stack left **down** at the end of the task

- [ ] **Step 1: Isolate Compose**

If any other numbered-project Compose stack is running, `docker compose -p <name> down` that stack only (no system prune, no port edits). Then:

```bash
cd /home/andrey_py_dev/Dev/_real_projects/1_saas_billing_entitlements
test -f .env || cp .env.example .env
make compose-core
docker compose -p billing-platform -f deploy/compose/docker-compose.yml ps
docker compose -p billing-platform -f deploy/compose/docker-compose.yml logs --tail=80 billing-api
```

If API is unhealthy: read logs, fix **configuration** (env, wait, recreate that service). Do not remap ports.

- [ ] **Step 2: Run Locust smoke**

```bash
uv sync --group load
make load-locust
```

Expected: preflight ok; Locust exit 0; ≥1 HTTP request; evaluate/usage/admin names in stats.

If fail: capture locust output + API logs, fix the harness (auth, path, 429 → confirm `_load_perf_rate_limits` ran). No k6 script edits.

Also confirm fail-closed: with API stopped, `./scripts/load_locust_smoke.sh` exits nonzero at preflight. To do that without killing a still-needed stack: after the successful run, either stop `billing-api` briefly or point `LOAD_HOST` at `http://127.0.0.1:1` and expect preflight failure — restore afterward if the stack is still up.

- [ ] **Step 3: Write report and runbook**

`docs/runbooks/load-locust.md` — symptoms/prerequisites (`make compose-core`, `.env` K6_*), `make load-locust`, `make load-locust-ui` (:8089), env table, fail-closed behavior, links to official Locust docs (quickstart, writing-a-locustfile, running-without-web-ui, configuration), explicit “does not replace k6 / not §8.1.1 12k RPS proof”.

`docs/perf/locust-smoke-report.md` — date, hardware (laptop/WSL), users/duration, request counts, fail %, p50/p95 from Locust output, command used, limits.

Update `docs/perf/README.md` with a **Locust** subsection after the k6 table. Update `docs/runbooks/README.md` index. Update `AGENTS.md` §0.2 if `docs/perf/` is k6-only; §10.1 Load row to mention Locust additive; §8 local commands add `make load-locust`. README: one line that Locust smoke exists alongside k6.

- [ ] **Step 4: Tear down this stack**

```bash
docker compose -p billing-platform -f deploy/compose/docker-compose.yml down
```

Leave no billing-platform containers running.

- [ ] **Step 5: Commit**

SKIP unless the human asked.

**Acceptance:** `make load-locust` exit 0 against compose-core with evidence in `docs/perf/locust-smoke-report.md`; preflight fail-closed evidenced; runbook + AGENTS/README sync; stack down.

---

### Task 4: Grafana — Locust OTEL + k6 Prometheus remote write

**Depends on:** Task 3.

**Files:**
- Modify: `pyproject.toml` load group → `locust[otel]>=2.32,<3` and regenerate `uv.lock` via `uv lock` / `uv sync --group load`
- Modify: `scripts/load_locust_smoke.sh` — optional `--otel` when `LOAD_LOCUST_OTEL=1`
- Modify: `Makefile` — `load-locust-otel`, `load-k6-grafana` (or `load-a-grafana`); help text; do not change default `load-a`…`load-e` behavior
- Create: `scripts/load_k6_grafana.sh` — docker k6 on network `billing-platform`, `BASE_URL=http://billing-api:8000`, `-o experimental-prometheus-rw`, fail-closed if Prometheus/API missing
- Create: `deploy/observability/grafana/provisioning/dashboards/k6-prometheus.json` (official dashboard 19665, datasource uid `prometheus`)
- Create: `deploy/observability/grafana/provisioning/dashboards/locust-otel.json` (folder Billing; panels from **actual** PromQL after a live `--otel` run)
- Modify: `deploy/observability/README.md`, `docs/perf/README.md`, `docs/runbooks/load-locust.md`, `AGENTS.md` §0.2 / §10.1 as needed
- Test: `tests/unit/test_load_grafana_helpers.py` — parse/guard script flags and env (no live Grafana required)

**Interfaces:**
- Consumes: Task 2 locustfile; existing Alloy OTLP :4317/:4318; Prometheus remote-write receiver; k6 scripts in `docs/perf/`
- Produces: `make load-locust-otel`, `make load-k6-grafana`; two provisioned Grafana dashboards under folder **Billing**

**Grounding (must cite in report):**
- Locust OTEL: https://docs.locust.io/en/stable/telemetry.html — `pip install locust[otel]`, `locust --otel`, `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`
- k6 RW: https://grafana.com/docs/k6/latest/results-output/real-time/prometheus-remote-write/ — `K6_PROMETHEUS_RW_SERVER_URL`, `-o experimental-prometheus-rw`, `K6_PROMETHEUS_RW_TREND_STATS`, dashboard 19665
- Do not use native histograms (`K6_PROMETHEUS_RW_TREND_AS_NATIVE_HISTOGRAM`).

- [ ] **Step 1: Failing unit tests for helper contract**

Create `tests/unit/test_load_grafana_helpers.py` that asserts:

1. `scripts/load_locust_smoke.sh` contains `LOAD_LOCUST_OTEL` and passes `--otel` only when that var is `1`.
2. `scripts/load_k6_grafana.sh` (once written) contains `experimental-prometheus-rw`, `K6_PROMETHEUS_RW_SERVER_URL=http://prometheus:9090/api/v1/write`, `--network` `billing-platform`, and `BASE_URL=http://billing-api:8000`.
3. `pyproject.toml` load extra includes `locust[otel]`.

Run: `uv run pytest tests/unit/test_load_grafana_helpers.py -v`
Expected: FAIL until scripts/toml exist.

- [ ] **Step 2: Locust `[otel]` + smoke script flag**

In `pyproject.toml`:

```toml
load = [
    "locust[otel]>=2.32,<3",
]
```

`uv sync --group load`. Verify: `uv run --group load python -c "import locust; print(locust.__version__)"`.

In `scripts/load_locust_smoke.sh`, after the existing locust argument list, add:

```bash
LOCUST_OTEL_ARGS=()
if [[ "${LOAD_LOCUST_OTEL:-0}" == "1" ]]; then
	LOCUST_OTEL_ARGS+=(--otel)
	export OTEL_SERVICE_NAME="${OTEL_SERVICE_NAME:-locust}"
	export OTEL_TRACES_EXPORTER="${OTEL_TRACES_EXPORTER:-otlp}"
	export OTEL_METRICS_EXPORTER="${OTEL_METRICS_EXPORTER:-otlp}"
	export OTEL_EXPORTER_OTLP_PROTOCOL="${OTEL_EXPORTER_OTLP_PROTOCOL:-http/protobuf}"
	export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://127.0.0.1:4318}"
	log "otel enabled endpoint=$OTEL_EXPORTER_OTLP_ENDPOINT"
fi
```

Pass `"${LOCUST_OTEL_ARGS[@]}"` into the locust invocation.

- [ ] **Step 3: k6 Grafana runner**

`scripts/load_k6_grafana.sh` (executable):

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
SCRIPT="${LOAD_SCRIPT:-k6_evaluate_peak.js}"
NETWORK="${COMPOSE_PROJECT:-billing-platform}"
test -n "${K6_API_KEY:-}" || { echo "Set K6_API_KEY" >&2; exit 1; }
test -n "${K6_ORG_ID:-}" || { echo "Set K6_ORG_ID" >&2; exit 1; }
docker network inspect "$NETWORK" >/dev/null 2>&1 || {
	echo "ERROR: docker network $NETWORK missing; run make observability-up first" >&2
	exit 1
}
docker run --rm --network "$NETWORK" \
	-e K6_API_KEY -e K6_ORG_ID -e K6_FEATURE_KEY \
	-e BASE_URL=http://billing-api:8000 \
	-e K6_PROFILE="${K6_PROFILE:-smoke}" \
	-e K6_PROMETHEUS_RW_SERVER_URL=http://prometheus:9090/api/v1/write \
	-e K6_PROMETHEUS_RW_TREND_STATS="${K6_PROMETHEUS_RW_TREND_STATS:-p(95),p(99),avg,min,max}" \
	-v "$ROOT/docs/perf:/scripts:ro" \
	grafana/k6 run -o experimental-prometheus-rw --tag "testid=${K6_TESTID:-k6-grafana-smoke}" \
	/scripts/"$SCRIPT"
```

Makefile:

```make
load-locust-otel: _load_env_check _load_perf_rate_limits
	@mkdir -p .local/locust
	LOAD_LOCUST_OTEL=1 ./scripts/load_locust_smoke.sh

load-k6-grafana: _load_env_check _load_perf_rate_limits
	./scripts/load_k6_grafana.sh
```

If `observability` is not up, both targets must fail closed with a readable error (network inspect / connection refused), not hang.

- [ ] **Step 4: Live observability + metric discovery**

```bash
make observability-up   # rebuilds Grafana/Prometheus images so new dashboards bake in
# wait for grafana health :3000 and alloy :4318
make load-locust-otel
make load-k6-grafana
```

Query Prometheus **from the prometheus container** (no host :9090):

```bash
docker compose -p billing-platform -f deploy/compose/docker-compose.yml \
  exec prometheus wget -qO- 'http://localhost:9090/api/v1/label/__name__/values'
```

Record Locust/OTEL and `k6_*` metric names in the task report. If Locust metrics are missing: check Alloy logs, Locust stdout for “OpenTelemetry enabled”, and `OTEL_EXPORTER_OTLP_ENDPOINT`. Fix configuration, do not add an exporter sidecar.

If API/Alloy unhealthy: `docker compose … logs --tail=120` for `billing-api`, `alloy`, `prometheus`, `grafana`.

- [ ] **Step 5: Dashboards**

Download official k6 dashboard 19665 JSON (Grafana.com API) and set Prometheus datasource uid to `prometheus` (already in `datasources.yaml`). Save as `k6-prometheus.json`.

Create `locust-otel.json` with uid `locust-otel`, title **Locust (OTLP)**, folder Billing: at least request rate, error count, and latency using the metric names discovered in Step 4. If OTEL HTTP client metrics use standard `http.client.*` names, map those; do not invent PromQL that 404s.

Rebuild grafana image (`make observability-up`) so COPY picks up JSON. Verify Grafana health. Optionally `curl` Grafana search API with admin/admin (local demo only) for dashboard titles.

- [ ] **Step 6: Tests green + docs + tear down**

```bash
uv run pytest tests/unit/test_load_grafana_helpers.py tests/unit/test_load_helpers.py -v
make lint
```

Update observability README + perf README: how to open Grafana :3000, which dashboards, `make load-locust-otel` / `make load-k6-grafana`, “not §8.1.1 DoD”.

```bash
docker compose -p billing-platform -f deploy/compose/docker-compose.yml down
```

- [ ] **Step 7: Commit** — SKIP.

**Acceptance:** dashboards provisioned; locust `--otel` and k6 RW evidenced against live LGTP; default `make load-a` still does not require Grafana; no new host ports; stack down.

---

### Task 5: CI — Locust harness + smoke job

**Depends on:** Task 3 (harness). Task 4 otel extra must remain installable (`uv sync --frozen --group load`).

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `Makefile` if a `load-locust-ci` / `load-harness` target helps CI (optional)
- Modify: `AGENTS.md` §1 / §8 / §10.1 if CI description is “stubs” only
- Test: `tests/unit/test_ci_load_workflow.py` — YAML contains required job names and `uv sync --frozen --group load`

**Interfaces:**
- Consumes: `make load-locust`, `.env.example` demo keys
- Produces: GHA jobs `load-harness` (no API) and `load-locust-smoke` (compose-core + locust)

Official Locust CI pattern: headless run + `environment.process_exit_code` (https://docs.locust.io/en/2.46.3/running-without-web-ui.html#running-in-ci-cd). Existing locustfile already sets exit code on zero requests; `--exit-code-on-error 1` fails on failed samples.

Do **not** mix load into `test-unit` / `test-integration` jobs.

- [ ] **Step 1: Failing pin test**

`tests/unit/test_ci_load_workflow.py` reads `.github/workflows/ci.yml` and asserts jobs `load-harness` and `load-locust-smoke` exist; smoke job runs `make load-locust` or equivalent; harness runs `uv sync --frozen --group load` and `pytest tests/unit/test_load_helpers.py`.

Expected: FAIL until workflow updated.

- [ ] **Step 2: Workflow**

Add jobs (same checkout/setup-uv/python 3.12 pattern as existing jobs):

`load-harness`:
```yaml
- run: uv sync --frozen --group dev --group load
- run: uv run pytest tests/unit/test_load_helpers.py tests/unit/test_load_grafana_helpers.py -q
- run: uv run --group load locust -f loadtests/locustfile.py --list
```

`load-locust-smoke`:
- timeout-minutes: 20
- `cp .env.example .env` (demo keys already in example)
- `uv sync --frozen --group dev --group load`
- `make compose-core`
- `make load-locust`
- always: `docker compose -p billing-platform -f deploy/compose/docker-compose.yml logs --tail=80 billing-api` on failure
- always: compose down

Do not add a full k6 A–E matrix to CI (too long). Optional one-line comment that k6 remains `make load-*` locally. If adding k6 smoke is cheap (docker `grafana/k6` + compose already up), a **single** `make load-a` step on the same smoke job is allowed; it must not replace Locust.

- [ ] **Step 3: Tests + lint**

```bash
uv run pytest tests/unit/test_ci_load_workflow.py -v
make lint
```

- [ ] **Step 4: Commit** — SKIP.

**Acceptance:** workflow YAML valid; pin tests green; load jobs separate from unit/integration; no secrets.

---

### Task 6: Pre-commit hooks

**Depends on:** Task 2 (locustfile exists). Task 4 locust[otel] should already be in lockfile.

**Files:**
- Create: `.pre-commit-config.yaml`
- Modify: `pyproject.toml` — add `pre-commit>=4,<5` to `[dependency-groups] dev` (or a `hooks` group if adding to `dev` pulls too much; prefer `dev` so `uv sync --group dev` can run hooks)
- Modify: `uv.lock` via `uv lock`
- Modify: `CONTRIBUTING.md` — replace “no pre-commit hooks required” with install + run instructions
- Modify: `AGENTS.md` §8 if local commands list is stale
- Test: `tests/unit/test_precommit_config.py`

**Grounding:** https://docs.astral.sh/ruff/integrations/#pre-commit and https://pre-commit.com/

- [ ] **Step 1: Failing tests**

Assert `.pre-commit-config.yaml` exists; repo `https://github.com/astral-sh/ruff-pre-commit` with `rev: v0.8.6`; hooks `ruff-check` then `ruff-format`; a local hook that imports `loadtests.locustfile` via `uv run --group load`; `pre-commit` declared in pyproject.

Expected: FAIL until files exist.

- [ ] **Step 2: Config**

```yaml
default_language_version:
  python: python3.12
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.6
    hooks:
      - id: ruff-check
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
      - id: detect-private-key
  - repo: local
    hooks:
      - id: locustfile-import
        name: locustfile import
        entry: uv run --group load python -c "import loadtests.locustfile"
        language: system
        pass_filenames: false
        files: ^(loadtests/.*\.py|pyproject.toml|uv.lock)$
```

Do **not** run Locust headless or Docker in hooks.

- [ ] **Step 3: Install and run**

```bash
uv sync --group dev --group load
uv run pre-commit run --all-files
```

Fix any hook findings in tracked files (whitespace/EOF). Do not hook-fix gitignored paths as a substitute for config excludes.

- [ ] **Step 4: Docs**

CONTRIBUTING: `uv run pre-commit install` then `uv run pre-commit run --all-files`. Hooks are required for local commits once the human starts committing; CI still runs `make lint`.

- [ ] **Step 5: Commit** — SKIP.

**Acceptance:** `uv run pre-commit run --all-files` exit 0; ruff rev matches lock 0.8.6; no live load in hooks.

---

## Official sources (orchestrator grounding, 2026-03-05)

| Topic | URL | Takeaway |
|-------|-----|----------|
| Locust headless / CI / exit code | https://docs.locust.io/en/2.46.3/running-without-web-ui.html | `--headless -u -r -t`; `process_exit_code`; GHA example |
| Locust CLI | https://docs.locust.io/en/2.46.3/configuration.html | `--html`, `--csv`, `--exit-code-on-error`, `--web-port` 8089 |
| Locust locustfile | https://docs.locust.io/en/2.46.3/writing-a-locustfile.html | `HttpUser`, `@task`, `between`, `catch_response`, weights |
| Locust OTEL | https://docs.locust.io/en/stable/telemetry.html | `locust[otel]`, `--otel`, OTLP HTTP |
| k6 Prometheus RW | https://grafana.com/docs/k6/latest/results-output/real-time/prometheus-remote-write/ | `-o experimental-prometheus-rw`; dashboard 19665; trend stats not native hist |
| Ruff pre-commit | https://docs.astral.sh/ruff/integrations/#pre-commit | `ruff-check` before `ruff-format` when using `--fix` |

## Task graph

```text
Task 1 (done) → Task 2 (done) → Task 3 (live smoke + docs)
                                → Task 4 (Grafana Locust+k6)
                                → Task 5 (CI)  [after 3; otel extra from 4 if present]
                                → Task 6 (pre-commit)
```

Tasks 5 and 6 may run only after Task 4 if they assert `locust[otel]` / grafana helper tests. Sequence: 3 → 4 → 5 → 6. No parallel implementers.

## Out of scope

- Replacing k6 A–E or claiming Stage 3 load DoD on a laptop
- Distributed Locust workers / master:5557
- Publishing Prometheus on the host
- Changing application ports or Cursor allowlists
- git commit / push / GitHub PR
- Native-histogram Prometheus / dashboard 18030
- Mixing load soak into pytest unit/integration
