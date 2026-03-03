"""Environment-backed defaults for Locust load scenarios (no app imports)."""

from __future__ import annotations

import os
from typing import Final

DEFAULT_LOAD_HOST: Final = "http://localhost:8000"
DEFAULT_FEATURE_KEY: Final = "api_calls"


def load_host() -> str:
    raw = os.environ.get("LOAD_HOST") or os.environ.get("BASE_URL") or DEFAULT_LOAD_HOST
    return raw.rstrip("/")


def load_api_key() -> str:
    return (os.environ.get("LOAD_API_KEY") or os.environ.get("K6_API_KEY") or "").strip()


def load_org_id() -> str:
    return (os.environ.get("LOAD_ORG_ID") or os.environ.get("K6_ORG_ID") or "").strip()


def load_feature_key() -> str:
    value = (os.environ.get("LOAD_FEATURE_KEY") or os.environ.get("K6_FEATURE_KEY") or "").strip()
    return value or DEFAULT_FEATURE_KEY


def load_wait_bounds() -> tuple[float, float]:
    """Return (min, max) Locust wait seconds from LOAD_WAIT_MIN / LOAD_WAIT_MAX."""
    min_wait = float(os.environ.get("LOAD_WAIT_MIN", "0.1"))
    max_wait = float(os.environ.get("LOAD_WAIT_MAX", "0.5"))
    return min_wait, max_wait
