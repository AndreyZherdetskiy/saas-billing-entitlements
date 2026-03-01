import httpx
import pytest


@pytest.mark.integration
@pytest.mark.live_compose
def test_api_live_returns_200() -> None:
    r = httpx.get("http://localhost:8000/health/live", timeout=5.0)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
