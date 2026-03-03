"""Unit tests for Locust OTEL + k6 Grafana load script contracts (no live Grafana)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _k6_full_block(text: str) -> str:
    marker = "  full: {"
    start = text.index(marker)
    return text[start : start + 500]


def test_load_locust_smoke_otel_guard() -> None:
    text = (ROOT / "scripts" / "load_locust_smoke.sh").read_text(encoding="utf-8")
    assert "LOAD_LOCUST_OTEL" in text
    assert "--otel" in text
    assert 'LOAD_LOCUST_OTEL:-0}" == "1"' in text or 'LOAD_LOCUST_OTEL:-0}" = "1"' in text
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in text
    assert "http/protobuf" in text
    assert "http://127.0.0.1:4318" in text
    assert "docker network inspect" in text
    assert "observability-up first" in text
    # Default path must not always pass --otel (only when LOAD_LOCUST_OTEL=1).
    assert "LOCUST_OTEL_ARGS" in text


def test_load_perf_env_uses_later_compose_env_file() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert ".local/load-perf.env" in makefile
    assert "--force-recreate" in makefile
    assert "--wait" in makefile
    assert "UVICORN_WORKERS" in makefile
    perf_block = makefile.split("_load_perf_rate_limits:")[1].split("load-perf-env:")[0]
    assert "DATABASE_POOL_SIZE" in perf_block
    assert "2>/dev/null" not in perf_block


def test_docker_k6_smoke_uses_stdin_not_bind_mount() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "run_k6_docker.sh").read_text(encoding="utf-8")
    assert "run_k6_docker.sh" in makefile
    assert "/scripts:ro" not in makefile
    assert "/scripts:ro" not in script
    assert "grafana/k6 run" in script
    assert '- <"$HOST_SCRIPT"' in script
    assert "http://billing-api:8000" in script
    assert "--network" in script
    assert "host-gateway" not in script
    assert "TARGET_RPS=${TARGET_RPS}" in script


def test_k6_ceiling_is_grafana_breakpoint() -> None:
    text = (ROOT / "docs" / "perf" / "k6_ceiling.js").read_text(encoding="utf-8")
    assert "ramping-arrival-rate" in text
    assert "abortOnFail" in text
    assert "delayAbortEval" in text
    assert "constant-arrival-rate" not in text
    assert "target: 15" in text
    assert "K6_CEILING_RPS" in text
    assert "laptop:" in text
    assert "K6_PROFILE=smoke|laptop|full" in text
    assert "use smoke, laptop, or full." in text
    assert "startRate: 100" in text
    assert "target: 2000" in text
    assert "rate<0.05" in text
    assert "delayAbortEval: '20s'" in text or 'delayAbortEval: "20s"' in text
    # Laptop VUs stay in the hundreds; full may still use 8000 for the stand.
    laptop_block = text.split("laptop:")[1].split("full:")[0]
    assert "maxVUs: 8000" not in laptop_block
    assert "preAllocatedVUs: 1000" not in laptop_block


def test_k6_full_rates_match_measured_nfr() -> None:
    """k6 `full` intensities follow spec §8.1.1."""
    peak = (ROOT / "docs" / "perf" / "k6_evaluate_peak.js").read_text(encoding="utf-8")
    peak_full = _k6_full_block(peak)
    assert "rate: 3000," in peak_full
    assert "rate: 12000" not in peak

    usage = (ROOT / "docs" / "perf" / "k6_usage_ingest.js").read_text(encoding="utf-8")
    usage_full = _k6_full_block(usage)
    assert "eventsPerSec: 1500," in usage_full
    assert "eventsPerSec: 5000" not in usage

    mixed = (ROOT / "docs" / "perf" / "k6_mixed.js").read_text(encoding="utf-8")
    mixed_full = _k6_full_block(mixed)
    assert "evaluate: { rate: 3000," in mixed_full
    assert "usage: { rate: 1500," in mixed_full
    assert "adminRead: { rate: 500," in mixed_full
    assert "rate: 10000" not in mixed

    soak = (ROOT / "docs" / "perf" / "k6_soak.js").read_text(encoding="utf-8")
    soak_full = _k6_full_block(soak)
    assert "evaluate: { rate: 900," in soak_full
    assert "usage: { rate: 450," in soak_full
    assert "adminRead: { rate: 150," in soak_full

    ceiling = (ROOT / "docs" / "perf" / "k6_ceiling.js").read_text(encoding="utf-8")
    assert "|| 8000" in ceiling
    assert "|| 30000" not in ceiling
    assert "default 8000" in ceiling or "default 8 000" in ceiling
    assert "30000" not in ceiling
    assert "12k" not in ceiling
    assert "12 000" not in ceiling


def test_load_k6_grafana_script_contract() -> None:
    text = (ROOT / "scripts" / "load_k6_grafana.sh").read_text(encoding="utf-8")
    assert "experimental-prometheus-rw" in text
    assert "K6_PROMETHEUS_RW_SERVER_URL=http://prometheus:9090/api/v1/write" in text
    assert "--network" in text
    assert "billing-platform" in text
    assert "BASE_URL=http://billing-api:8000" in text
    assert "K6_PROMETHEUS_RW_TREND_STATS" in text
    assert "p(95),p(99),avg,min,max" in text
    assert "testid=" in text
    assert "grafana/k6" in text
    assert "docker network inspect" in text
    assert "--no-thresholds" not in text
    assert '- <"$HOST_SCRIPT"' in text


def test_pyproject_load_group_has_locust_otel() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "locust[otel]" in text
    assert "load = [" in text or "load = [" in text
