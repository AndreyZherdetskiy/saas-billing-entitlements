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
