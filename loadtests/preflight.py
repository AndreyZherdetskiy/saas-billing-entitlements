"""Fail-closed preflight checks for Locust smoke (unit-testable without Locust)."""

from __future__ import annotations

import sys
from typing import Final

import httpx

from loadtests.config import load_api_key, load_host, load_org_id

MIN_SMOKE_REQUESTS: Final = 1
READY_PATH: Final = "/health/ready"


class PreflightError(RuntimeError):
    """Raised when preflight checks fail before a load run."""


def assert_minimum_requests(*, request_count: int, minimum: int = MIN_SMOKE_REQUESTS) -> None:
    if request_count < minimum:
        msg = (
            f"load smoke issued {request_count} HTTP request(s); "
            f"need at least {minimum} (check K6_API_KEY, K6_ORG_ID, API, and /health/ready)"
        )
        raise PreflightError(msg)


def preflight_credentials() -> tuple[str, str]:
    api_key = load_api_key()
    org_id = load_org_id()
    missing: list[str] = []
    if not api_key:
        missing.append("LOAD_API_KEY or K6_API_KEY")
    if not org_id:
        missing.append("LOAD_ORG_ID or K6_ORG_ID")
    if missing:
        msg = "missing load credentials: " + ", ".join(missing)
        raise PreflightError(msg)
    return api_key, org_id


def preflight_api_ready(
    *,
    host: str | None = None,
    timeout_seconds: float = 5.0,
    client: httpx.Client | None = None,
) -> None:
    base_url = (host or load_host()).rstrip("/")
    url = f"{base_url}{READY_PATH}"
    own_client = client is None
    http = client or httpx.Client(timeout=timeout_seconds)
    try:
        response = http.get(url)
        if response.status_code != 200:
            msg = f"API ready check failed for {url}: HTTP {response.status_code} {response.text}"
            raise PreflightError(msg)
    except httpx.HTTPError as exc:
        msg = f"API ready check failed for {url}: {exc}"
        raise PreflightError(msg) from exc
    finally:
        if own_client:
            http.close()


def run_smoke_preflight(*, host: str | None = None) -> tuple[str, str]:
    credentials = preflight_credentials()
    preflight_api_ready(host=host)
    return credentials


def main() -> int:
    try:
        api_key, org_id = run_smoke_preflight()
    except PreflightError as exc:
        print(f"preflight failed: {exc}", file=sys.stderr)
        return 1
    print(f"preflight ok org_id={org_id} api_key_len={len(api_key)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
