# Prod-like overlay + pipeline hunt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tracked prod-like Compose overlay (`docker-compose.perf.yml` + `make perf-up`) for this billing stack, hunt the laptop ceiling on the **full** core stack with Locust wait=0 plus existing k6 hold/breakpoint, fix **one** proven software limiter if the clocks name one, then remesure and write facts only.

**Architecture:** Default `make compose-core` stays 1 Uvicorn worker and demo rate limits. The hunt overlay is **opt-in**: uvicorn `--workers 4` on `billing-api`; SQLAlchemy pool **2+1** on every app process; rate-limit 0; OTEL off; `--scale outbox-relay=2` (Helm/ADR-004 HA). Locust smoke `wait_time` stays 0.1–0.5; ceiling holds use `LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0`. Do **not** clear `REDIS_URL` (evaluate cache is Redis; rate-limit off is `API_RATE_LIMIT_*=0`). k6 A–E and Grafana paths already exist — do not replace them.

**Tech Stack:** Compose v5 merge overlay; Locust 2.46 `between` / `constant`; existing `k6_hotpath_plateau.js` + `k6_ceiling.js`; SQLAlchemy 2 async pooling; pytest pin tests.

## Global Constraints

- Product SoT: `spec.md` v3.2 + Accepted ADRs + `AGENTS.md`. This is **not** Stage Done.
- Locust is **additive**. k6 remains §8.1.1 DoD. Do not delete or rewrite k6 scripts’ stand intensities.
- **Do not commit / push / gh.** Skip every Commit step.
- Implementer ≠ Reviewer. TDD on harness/CI pins and on any `src/` fix. Cursor built-in models only (`composer-2.5` or `cursor-grok-4.6-xhigh`). No `*-fast`, no BYOK.
- Docs language: professional English. **Measured facts only** (command, overlay knobs, RPS, p50/p99, fail%, dropped, limiter). Do not invent RPS. Do not rewrite stand 3k/12k from a laptop.
- Fail-closed: missing `K6_API_KEY`/`K6_ORG_ID` (or `LOAD_*`), `/health/ready` not 200, 0 HTTP, Locust nonzero exit, UI if :8089 busy. Frozen ports — do not remap 8000/8001/8080/8081/3000/4317/4318.
- Overlay is **not** default `compose-core` and **not** `make load-locust`. CI smoke stays `compose-core` + `make load-locust`.
- **Cannot `--scale billing-api`:** host publishes `8000:8000`. Prod-like API concurrency = `UVICORN_WORKERS=4` on **one** replica.
- Do **not** `--scale billing-worker` / `billing-beat` to paint RPS (Celery is batch/cron, not the Locust HTTP mix).
- Do **not** inflate Kafka/prefetch/`max_connections` / `DATABASE_POOL_SIZE` to fake RPS. Pool overlay **tightens** budget: `(pool_size + max_overflow) × processes < 100`.
- Do **not** clear `REDIS_URL` on API (notify token-bucket trick). Billing evaluate **needs** Redis on miss; disable rate-limit with `API_RATE_LIMIT_*=0`.
- Live load = **full** core stack (API + worker + beat + relay + PG + Redis + Kafka + migrate/seed). Not host-uvicorn / API-only.
- Two clocks: **A** HTTP accept (Locust/k6). **B** `outbox_messages.published_at IS NULL`, `pg_stat_activity` vs `max_connections`, `docker stats`. Locust mix does **not** insert outbox (evaluate is read-only; usage ingest writes `usage_events` only). Clock B may stay unpublished=0 — still record it. p95 accept→terminal is N/A for evaluate; for usage, SoT is PG row count not Kafka.
- Measure and name **one** primary limiter before changing `src/`. Forbidden first fix: `--scale`, uvicorn workers++, `max_connections++`, sharding.
- `compose-down` must include the perf overlay file and observability/replica/bouncer **profiles**, **without** `-v`. After live tasks that do not need the stack: `make compose-down`.
- Scripts do not `source .env`. Make already `include .env`. Fail-closed without secrets: `env -u K6_API_KEY -u LOAD_API_KEY ./scripts/load_locust_smoke.sh`.
- Dual-id: HTTP bodies use `organization_public_id` / UUID — never BIGINT `id`. No dual-write; evaluate does not read Kafka; ledger append-only.
- When `docs/` changes, update matching `AGENTS.md` sections in the same task (`AGENTS.md` §0.3).
- No `Task N` left in `src/`, Compose, or scripts after a task ships.
- Other numbered `_real_projects` stacks: `docker compose -p <name> down` only (no prune, no `-v`).
- Grounding: Locust wait-time https://docs.locust.io/en/stable/writing-a-locustfile.html#wait-time-functions ; Compose merge overlay; SQLAlchemy pooling https://docs.sqlalchemy.org/en/20/core/pooling.html ; k6 constant-arrival-rate / dropped_iterations / breakpoint (existing scripts).

## Git vs gitignore

| Tracked | Ignored |
|---------|---------|
| `deploy/compose/docker-compose.perf.yml`, `Makefile`, `loadtests/`, `tests/unit/test_perf_overlay.py`, `tests/unit/test_load_helpers.py`, `.env.example` | `.env`, `.venv/`, `.local/` |
| `docs/plans/2026-03-08-prodlike-perf.md`, `docs/perf/*`, `docs/runbooks/*`, `AGENTS.md` | `.superpowers/` (briefs, reports, ledger) |
| `.github/workflows/ci.yml` only if pin list must include the new test | |

---

### Task 1: Tracked perf overlay + Locust wait knobs (no live stack)

**Files:**
- Create: `deploy/compose/docker-compose.perf.yml`
- Create: `tests/unit/test_perf_overlay.py`
- Modify: `Makefile` (`perf-up`, `compose-down` includes overlay, help; optional `stack-up`/`stack-down` aliases → existing compose-core/down)
- Modify: `loadtests/config.py`, `loadtests/locustfile.py`
- Modify: `tests/unit/test_load_helpers.py`, `tests/unit/test_ci_load_workflow.py`, `.github/workflows/ci.yml` (`load-harness` pytest includes `test_perf_overlay.py`)
- Modify: `.env.example` (comment `LOAD_WAIT_MIN`/`LOAD_WAIT_MAX`, `make perf-up`)
- Modify: `docs/runbooks/load-locust.md`, `docs/runbooks/local-compose-profiles.md`, `docs/perf/README.md`, `AGENTS.md` §8 / §10.1 / §0.2 as needed

**Interfaces:**
- Consumes: existing `deploy/compose/docker-compose.yml` services `billing-api`, `billing-worker`, `billing-beat`, `outbox-relay`
- Produces: `make perf-up`; `load_wait_bounds() -> tuple[float, float]`; Locust `wait_time` from env (default 0.1/0.5; both 0 → `constant(0)`)

- [ ] **Step 1: Write failing pin tests**

Create `tests/unit/test_perf_overlay.py`:

```python
"""Pin perf overlay compose + Makefile (no live stack)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _makefile() -> str:
    return _read("Makefile")


def _perf_overlay() -> str:
    return _read("deploy/compose/docker-compose.perf.yml")


def test_perf_overlay_file_exists() -> None:
    assert (ROOT / "deploy/compose/docker-compose.perf.yml").is_file()


def test_perf_overlay_api_uses_uvicorn_workers_4() -> None:
    text = _perf_overlay()
    assert "billing_platform.main:app" in text
    assert "--workers" in text
    assert re.search(r"--workers\s+4", text) or '"4"' in text or "'4'" in text


def test_perf_overlay_disables_rate_limit_and_otel() -> None:
    text = _perf_overlay()
    assert "API_RATE_LIMIT_PER_MINUTE" in text
    assert re.search(r'API_RATE_LIMIT_PER_MINUTE:\s*"0"', text)
    assert re.search(r'API_RATE_LIMIT_PLATFORM_ADMIN_PER_MINUTE:\s*"0"', text)
    assert re.search(r'OTEL_SDK_DISABLED:\s*"true"', text)


def test_perf_overlay_keeps_redis_url() -> None:
    text = _perf_overlay()
    assert "REDIS_URL:" not in text
    assert 'REDIS_URL: ""' not in text


def test_perf_overlay_pool_budget_2_plus_1() -> None:
    text = _perf_overlay()
    assert re.search(r'DATABASE_POOL_SIZE:\s*"2"', text)
    assert re.search(r'DATABASE_MAX_OVERFLOW:\s*"1"', text)
    for role in ("billing-api", "billing-worker", "billing-beat", "outbox-relay"):
        assert role in text


def test_perf_overlay_no_host_ports_or_grafana() -> None:
    text = _perf_overlay()
    assert "ports:" not in text
    assert "4318" not in text
    assert "grafana" not in text.lower()
    assert "alloy" not in text.lower()


def test_makefile_perf_up_uses_overlay_and_scale_relay() -> None:
    text = _makefile()
    assert "perf-up:" in text
    perf_block = text.split("perf-up:")[1].split("\n\n")[0]
    assert "docker-compose.perf.yml" in perf_block
    assert "--wait" in perf_block
    assert "--scale outbox-relay=2" in perf_block
    assert "--scale billing-api" not in perf_block
    assert "--scale billing-worker" not in perf_block


def test_makefile_compose_down_includes_perf_overlay() -> None:
    text = _makefile()
    down = text.split("compose-down:")[1].split("\n\n")[0]
    assert "docker-compose.perf.yml" in down
    assert " -v" not in down
    assert " --volumes" not in down


def test_default_compose_core_and_load_locust_omit_perf_overlay() -> None:
    text = _makefile()
    core = text.split("compose-core:")[1].split("compose-all:")[0]
    load = text.split("load-locust:")[1].split("load-locust-otel:")[0]
    assert "docker-compose.perf.yml" not in core
    assert "docker-compose.perf.yml" not in load
    assert "--scale" not in core
    assert "--scale" not in load
```

Add to `tests/unit/test_load_helpers.py`:

```python
from loadtests.config import load_wait_bounds


def test_load_wait_bounds_default_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_WAIT_MIN", raising=False)
    monkeypatch.delenv("LOAD_WAIT_MAX", raising=False)
    assert load_wait_bounds() == (0.1, 0.5)


def test_load_wait_bounds_zero_is_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAD_WAIT_MIN", "0")
    monkeypatch.setenv("LOAD_WAIT_MAX", "0")
    assert load_wait_bounds() == (0.0, 0.0)
```

Extend `test_load_harness_syncs_load_group_and_runs_helper_tests` so the CI blob also contains `tests/unit/test_perf_overlay.py`.

Run: `uv run pytest tests/unit/test_perf_overlay.py tests/unit/test_load_helpers.py::test_load_wait_bounds_default_smoke tests/unit/test_ci_load_workflow.py::test_load_harness_syncs_load_group_and_runs_helper_tests -q`
Expected: FAIL (missing overlay / function / CI line).

- [ ] **Step 2: Overlay file**

`deploy/compose/docker-compose.perf.yml` — comment at top: local ceiling characterization only; not default `compose-core`. Override **only** app services (no `ports:`). Keep `REDIS_URL` inherited from `x-app-env`.

```yaml
# Local ceiling characterization only. Not default compose-core.
# Pool budget: (2+1) × (4 API workers + worker + beat + 2 relay) = 24 ≪ Postgres max_connections 100.
services:
  billing-api:
    environment:
      API_RATE_LIMIT_PER_MINUTE: "0"
      API_RATE_LIMIT_PLATFORM_ADMIN_PER_MINUTE: "0"
      OTEL_SDK_DISABLED: "true"
      UVICORN_WORKERS: "4"
      DATABASE_POOL_SIZE: "2"
      DATABASE_MAX_OVERFLOW: "1"
    command:
      [
        "sh",
        "-c",
        "uvicorn billing_platform.main:app --host 0.0.0.0 --port 8000 --workers 4 --timeout-graceful-shutdown ${SHUTDOWN_GRACE_SECONDS:-30}",
      ]
  billing-worker:
    environment:
      OTEL_SDK_DISABLED: "true"
      DATABASE_POOL_SIZE: "2"
      DATABASE_MAX_OVERFLOW: "1"
  billing-beat:
    environment:
      OTEL_SDK_DISABLED: "true"
      DATABASE_POOL_SIZE: "2"
      DATABASE_MAX_OVERFLOW: "1"
  outbox-relay:
    environment:
      OTEL_SDK_DISABLED: "true"
      DATABASE_POOL_SIZE: "2"
      DATABASE_MAX_OVERFLOW: "1"
```

- [ ] **Step 3: Makefile**

Add `perf-up` to `.PHONY`. Help: one line that overlay is **not** default core.

```make
COMPOSE_PERF_FILE ?= deploy/compose/docker-compose.perf.yml

perf-up:
	@echo ">>> perf-up: core + docker-compose.perf.yml (4 API workers, pool 2+1, scale outbox-relay=2)"
	$(COMPOSE) -f $(COMPOSE_FILE) -f $(COMPOSE_PERF_FILE) up -d --build --wait --wait-timeout 180 --remove-orphans --scale outbox-relay=2

compose-down:
	@echo ">>> compose-down: all profiles + perf overlay (no -v)"
	$(COMPOSE) $(foreach p,$(COMPOSE_PROFILES_ALL),--profile $(p)) -f $(COMPOSE_FILE) -f $(COMPOSE_PERF_FILE) down --remove-orphans
```

Optional aliases (same recipe, no extra behavior): `stack-up: compose-core` and `stack-down: compose-down`.

Do **not** wire `load-locust` to `perf-up`. Keep `_load_perf_rate_limits` for smoke/k6 Make targets.

- [ ] **Step 4: Locust wait knobs**

`loadtests/config.py`:

```python
def load_wait_bounds() -> tuple[float, float]:
    """Return (min, max) Locust wait seconds from LOAD_WAIT_MIN / LOAD_WAIT_MAX."""
    min_wait = float(os.environ.get("LOAD_WAIT_MIN", "0.1"))
    max_wait = float(os.environ.get("LOAD_WAIT_MAX", "0.5"))
    return min_wait, max_wait
```

`loadtests/locustfile.py`: import `constant` and `load_wait_bounds`. Replace `wait_time = between(0.1, 0.5)` with:

```python
def _resolve_wait_time():
    min_wait, max_wait = load_wait_bounds()
    if min_wait <= 0 and max_wait <= 0:
        return constant(0)
    return between(min_wait, max_wait)
```

Assign `wait_time = _resolve_wait_time()` on `BillingAuthMixin`. Official: https://docs.locust.io/en/stable/writing-a-locustfile.html#wait-time-functions

- [ ] **Step 5: CI pin + docs**

`.github/workflows/ci.yml` `load-harness` pytest line: add `tests/unit/test_perf_overlay.py`.

`.env.example` Locust comments:

```
# LOAD_WAIT_MIN=0.1
# LOAD_WAIT_MAX=0.5
# Ceiling hold (not smoke): LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0
# Prod-like overlay (not default compose-core): make perf-up
```

Runbook + `docs/perf/README.md` + `local-compose-profiles.md`: table of overlay knobs (workers 4, pool 2+1, scale relay 2, rate 0, OTEL off, Redis **kept**). State default core remains 1 worker. `compose-down` lists the perf file.

`AGENTS.md` §8 add `make perf-up` / `make compose-down` (overlay included). §10.1 load row: overlay is characterization, not profile A DoD. §0.2 if a new perf report path is named (report itself is Task 4).

- [ ] **Step 6: GREEN pins + lint**

```bash
uv run pytest tests/unit/test_perf_overlay.py tests/unit/test_load_helpers.py tests/unit/test_ci_load_workflow.py tests/unit/test_load_grafana_helpers.py -q
uv run ruff check loadtests tests/unit/test_perf_overlay.py
```

Expected: PASS.

- [ ] **Step 7: Commit** — SKIP.

**Acceptance:** Overlay tracked; `perf-up` scales **only** `outbox-relay=2`; `compose-down` includes overlay and has no `-v`; smoke defaults unchanged; CI harness lists the new pin test; `REDIS_URL` not cleared; no live Compose in this task.

---

### Task 2: Live hunt on `perf-up` (two clocks, read-only)

**Depends on:** Task 1.

**Files:**
- Create: `docs/perf/2026-03-07-prodlike-hunt.md` (facts)
- Modify: `.superpowers/sdd/progress.md` (this task row)
- Do **not** change `src/` in this task.

**Interfaces:**
- Consumes: `make perf-up`, `LOAD_WAIT_MIN/MAX=0`, existing `scripts/run_k6_docker.sh`, `k6_hotpath_plateau.js`, `k6_ceiling.js` `K6_PROFILE=laptop`
- Produces: named **one** primary limiter + evidence table

- [ ] **Step 1: Isolate + bring up overlay**

If another numbered-project stack is up: `docker compose -p <name> down` only. Then:

```bash
cd /home/andrey_py_dev/Dev/_real_projects/1_saas_billing_entitlements
test -f .env || cp .env.example .env
make compose-down || true
make perf-up
curl -sf http://localhost:8000/health/ready
docker compose -p billing-platform -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.perf.yml ps
```

Inspect (record in the report):

1. `outbox-relay` replica count = 2; `billing-api` count = 1; `billing-worker` = 1.
2. `billing-api` Cmd contains `--workers 4`.
3. API env: `API_RATE_LIMIT_PER_MINUTE=0`, `DATABASE_POOL_SIZE=2`, `OTEL_SDK_DISABLED=true`, `REDIS_URL` **non-empty**.
4. `/health/ready` HTTP 200.

If unhealthy: `docker compose … logs --tail=120` for `billing-api`, `postgres`, `kafka`, `outbox-relay`. Fix **overlay/config**, not ports.

- [ ] **Step 2: Clock A — evaluate hold (open model)**

Same method as `docs/perf/2026-03-04-hot-path-perf.md`, **this** overlay (pool 2+1, relay=2). Docker k6 stdin. Start at last known hold **1000**, then 700 / 1000 / 1500 as needed. `MAX_VUS` high enough that early `dropped_iterations` is not the first limiter.

```bash
TARGET_RPS=700  DURATION=22s MAX_VUS=800 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js
TARGET_RPS=1000 DURATION=22s MAX_VUS=800 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js
TARGET_RPS=1500 DURATION=22s MAX_VUS=800 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js
```

Stop at first **break** (fail% > 0, dropped storm, achieved ≪ target, or p50 ×2 with API pegged). Last **hold** = target ≈ achieved, 0% fail, 0 dropped.

Optional one `K6_PROFILE=laptop` breakpoint if time. Do **not** `--no-thresholds`. Scheduled arrival ≠ achieved RPS. `rate<0.05` is **5%**.

- [ ] **Step 3: Clock A — mixed Locust wait=0**

```bash
LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=20 LOAD_SPAWN_RATE=10 LOAD_RUN_TIME=30s make load-locust
```

If RPS ≈ users / tiny RT and fail=0, double users (40, then 50) until stop (fail%>1, p50×2, CPU wall, 5xx). Record CSV `# reqs` / RPS / fail% / p50/p99 **per name** (evaluate vs usage vs admin).

- [ ] **Step 4: Clock B — mid-run sample (during a 30s mixed hold)**

While Locust or k6 is running:

```bash
docker exec <postgres-container> psql -U billing -d billing -c "SHOW max_connections;"
docker exec <postgres-container> psql -U billing -d billing -c "SELECT count(*) AS activity FROM pg_stat_activity;"
docker exec <postgres-container> psql -U billing -d billing -c "SELECT count(*) FILTER (WHERE published_at IS NULL) AS unpublished FROM outbox_messages;"
docker stats --no-stream
```

Record API vs postgres vs redis vs kafka vs relay vs worker CPU. If unpublished grows and relay CPU ~0 → limiter is **relay/publish**, not workers. If unpublished=0 and usage p50 climbs → **usage/PG path**. If evaluate p50 climbs and API CPU pegged → **API CPU** (known).

- [ ] **Step 5: Classify one primary limiter**

Decision table (pick **one**):

| Observation | Verdict |
|-------------|---------|
| HTTP 500 `TooManyConnections` / activity at cap | pool vs `max_connections` — **budget**, do not raise cap |
| Pool/TimeoutError / `IndexError: pop from empty list` / `MaxConnectionsError` **on hold** | software (pool timeout→503 and/or Redis pool) |
| Unpublished rising, relay idle | relay loop (engine/producer per tick) |
| Evaluate hold: 0% fail, API 4 cores pegged, PG/Redis idle | API CPU (implementation vs laptop) — **no** replica hammer |
| Usage named stats dominate fail/p50; evaluate still cheap | usage ingest / PG writes |
| `wait_time` not 0 | invalid hunt — rerun |

Write `docs/perf/2026-03-07-prodlike-hunt.md`: overlay knobs, commands, tables, limiter sentence. No secrets. No 12k sermon.

- [ ] **Step 6: Tear down**

```bash
make compose-down
docker compose -p billing-platform ps -a   # expect empty
```

- [ ] **Step 7: Commit** — SKIP.

**Acceptance:** Hunt file names last hold / break / mixed facts / Clock B samples / **one** limiter; stack down; no `src/` edits.

---

### Task 3: Software fix of the limiter Task 2 named

**Depends on:** Task 2 APPROVE.

**Files:** whichever row matches Task 2. Do **not** implement a row Task 2 did not name.

| Task 2 verdict | Code (TDD) | Forbidden |
|----------------|------------|-----------|
| API CPU only; hold has **no** 5xx / pool / Redis errors | **No `src/` change.** Document in hunt file that overlay did not move the limiter. Proceed to Task 4 remesure (new knobs may still change numbers). | workers++ / `--scale` / max_connections++ |
| Pool TimeoutError / TooManyConnections on hold | `create_async_engine(..., pool_timeout=10)`; FastAPI dependency catches timeout → HTTP **503** (not a new PG client). Settings `database_pool_timeout: float = 10`. Ready probe must not create extra engines. Ground: https://docs.sqlalchemy.org/en/20/core/pooling.html | raising `max_connections` |
| Redis `MaxConnectionsError` / `IndexError: pop from empty list` on **hold** | Bound `Redis.from_url(..., max_connections=…)` per process; map pool exhaustion to 503 on evaluate miss path; add unit test with fake pool. Do not cache raw keys. | unbounded pool “to hold 1500” |
| Usage ingest is the mix limiter (double SELECT / partition) | Trim proven extra round-trip only; keep idempotency. TDD on `tests/unit` / integration usage tests. | batch-size games, drop idempotency |
| Unpublished grows, relay idle | Persistent relay loop: **one** engine + **one** Kafka producer for process lifetime; `idle sleep` only when tick published 0. TDD that `poll_and_publish` reuses injected `session_factory`/`producer` (already optional kwargs — `__main__.py` must pass them). Do not copy notify AMQP N-channels. | `--scale` relay as the fix |

- [ ] **Step 1: RED tests for the chosen row only.**
- [ ] **Step 2: Minimal implementation.**
- [ ] **Step 3: GREEN focused tests + `uv run ruff check` on touched files + `make typecheck` if `src/` changed.**
- [ ] **Step 4: If `docs/` / ADR Decision must change to stay true, edit in this task and sync `AGENTS.md` §0.3. Do not create ADR-016.**
- [ ] **Step 5: Commit** — SKIP.

**Acceptance:** Only the proven limiter changed; tests green; no sharding; no password-KDF regression; no Bearer logging.

---

### Task 4: Rebuild, remesure, facts-only docs, stack down

**Depends on:** Task 3 (or Task 2 if Task 3 was docs-only).

**Files:**
- Modify: `docs/perf/2026-03-07-prodlike-hunt.md` **or** create `docs/perf/2026-03-08-prodlike-remeasure.md` if cleaner
- Modify: `spec.md` §8.1 footnote **only** with this run’s measured overlay numbers (do not change stand 3k targets)
- Modify: `docs/perf/README.md`, `AGENTS.md` §0.2 / §10.1 / §10.6 headlines if numbers change
- Modify: `.superpowers/sdd/progress.md`

- [ ] **Step 1: Rebuild images on the same overlay**

```bash
make compose-down || true
make perf-up
curl -sf http://localhost:8000/health/ready
```

Confirm Task 3 code is in the API/relay image (not a stale layer). `--build` is required.

- [ ] **Step 2: Same plateaus + mixed wait=0 + Clock B sample as Task 2**

Do not change overlay knobs. Record hold/break/mixed/unpublished/docker stats. Re-classify limiter (it may have moved).

- [ ] **Step 3: Docs**

Facts only. Laptop ≠ §8.1.1 profile A DoD. k6 ceiling = Grafana breakpoint, not a claimed hold of a scheduled rate.

- [ ] **Step 4: Quality if `src/` changed in Task 3**

```bash
make lint
make typecheck
make test-unit
```

Integration if the fix touched DB/Redis behavior.

- [ ] **Step 5: Tear down**

```bash
make compose-down
```

Leave no billing-platform containers. No `-v`.

- [ ] **Step 6: Commit** — SKIP.

**Acceptance:** Evidence matches spec footnote; stack down; no Stage Done.

---

## Task graph

```text
Task 1 (overlay + wait pins)
  → Task 2 (live hunt, no src)
    → Task 3 (one software limiter or explicit no-src)
      → Task 4 (rebuild + remesure + docs + down)
```

No parallel implementers.

## Out of scope

- Replacing k6 A–E or claiming profile A 3k on a laptop
- Grafana/CI/pre-commit redo (already APPROVE)
- `--scale billing-api` / Celery worker scale as RPS paint
- Clearing `REDIS_URL`
- `down -v`, prune, port shifts, commits, Stage Done
- Copying notify AMQP pipeline confirms / 3-node RabbitMQ
- Distributed Locust master:5557
