"""Unit tests for Locust load helpers (no Locust / Compose required)."""

from __future__ import annotations

import httpx
import pytest
from loadtests.config import (
    load_api_key,
    load_feature_key,
    load_host,
    load_org_id,
    load_wait_bounds,
)
from loadtests.preflight import (
    PreflightError,
    assert_minimum_requests,
    preflight_api_ready,
    preflight_credentials,
)


def test_load_host_prefers_load_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAD_HOST", "http://127.0.0.1:9999/")
    monkeypatch.setenv("BASE_URL", "http://localhost:8000")
    assert load_host() == "http://127.0.0.1:9999"


def test_load_host_falls_back_to_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_HOST", raising=False)
    monkeypatch.setenv("BASE_URL", "http://api.example:8000/")
    assert load_host() == "http://api.example:8000"


def test_load_credentials_prefer_load_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAD_API_KEY", "load-key")
    monkeypatch.setenv("K6_API_KEY", "k6-key")
    monkeypatch.setenv("LOAD_ORG_ID", "load-org")
    monkeypatch.setenv("K6_ORG_ID", "k6-org")
    monkeypatch.setenv("LOAD_FEATURE_KEY", "seats")
    assert load_api_key() == "load-key"
    assert load_org_id() == "load-org"
    assert load_feature_key() == "seats"


def test_preflight_credentials_fail_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_API_KEY", raising=False)
    monkeypatch.delenv("K6_API_KEY", raising=False)
    monkeypatch.delenv("LOAD_ORG_ID", raising=False)
    monkeypatch.delenv("K6_ORG_ID", raising=False)
    with pytest.raises(PreflightError, match="K6_API_KEY"):
        preflight_credentials()


def test_preflight_credentials_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K6_API_KEY", "bp_local_demo_platform_admin_key_v1")
    monkeypatch.setenv("K6_ORG_ID", "01900000-0000-7000-8000-000000000001")
    monkeypatch.delenv("LOAD_API_KEY", raising=False)
    monkeypatch.delenv("LOAD_ORG_ID", raising=False)
    key, org_id = preflight_credentials()
    assert key.startswith("bp_local_")
    assert org_id.endswith("0001")


def test_assert_minimum_requests_rejects_zero() -> None:
    with pytest.raises(PreflightError, match="0 HTTP request"):
        assert_minimum_requests(request_count=0)


def test_assert_minimum_requests_accepts_positive() -> None:
    assert_minimum_requests(request_count=3, minimum=1)


def test_preflight_api_ready_accepts_200_degraded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health/ready"
        return httpx.Response(200, json={"status": "degraded"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    preflight_api_ready(host="http://test", client=client)


def test_load_wait_bounds_default_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_WAIT_MIN", raising=False)
    monkeypatch.delenv("LOAD_WAIT_MAX", raising=False)
    assert load_wait_bounds() == (0.1, 0.5)


def test_load_wait_bounds_zero_is_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAD_WAIT_MIN", "0")
    monkeypatch.setenv("LOAD_WAIT_MAX", "0")
    assert load_wait_bounds() == (0.0, 0.0)


def test_preflight_api_ready_rejects_503() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"status": "unavailable"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    with pytest.raises(PreflightError, match="ready"):
        preflight_api_ready(host="http://test", client=client)
