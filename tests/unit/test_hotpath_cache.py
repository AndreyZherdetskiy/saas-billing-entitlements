"""Unit: process-local auth / snapshot / org L1 TTL maps (ADR-003, ADR-015)."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from billing_platform.config import Settings, get_settings
from billing_platform.services.api_keys import AuthContext
from billing_platform.services.hotpath_cache import (
    cache_auth_context,
    clear_hotpath_caches,
    drop_l1_snapshot,
    get_cached_auth_context,
    get_l1_org,
    get_l1_snapshot,
    invalidate_auth_context,
    set_l1_org,
    set_l1_snapshot,
)


def _ctx(
    *,
    api_key_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
) -> AuthContext:
    return AuthContext(
        organization_id=7,
        role="product_service",
        key_prefix="bp_test01",
        api_key_id=api_key_id or uuid.uuid4(),
        organization_public_id=uuid.uuid4(),
        expires_at=expires_at,
    )


def test_settings_hotpath_ttl_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.auth_cache_ttl_seconds == 2
    assert settings.entitlement_l1_ttl_seconds == 1


def test_settings_hotpath_ttl_from_env() -> None:
    get_settings.cache_clear()
    with patch.dict(
        os.environ,
        {"AUTH_CACHE_TTL_SECONDS": "5", "ENTITLEMENT_L1_TTL_SECONDS": "3"},
    ):
        settings = Settings(_env_file=None)
    assert settings.auth_cache_ttl_seconds == 5
    assert settings.entitlement_l1_ttl_seconds == 3
    get_settings.cache_clear()


def test_auth_cache_hit_then_ttl_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1_000.0}
    monkeypatch.setattr(
        "billing_platform.services.hotpath_cache.time.monotonic",
        lambda: clock["t"],
    )
    get_settings.cache_clear()
    digest = "a" * 64
    ctx = _ctx()
    cache_auth_context(digest, ctx)
    assert get_cached_auth_context(digest) is ctx

    clock["t"] += 2.0
    assert get_cached_auth_context(digest) is None


def test_invalidate_auth_context_drops_digest_and_reverse() -> None:
    digest = "b" * 64
    ctx = _ctx()
    cache_auth_context(digest, ctx)
    assert get_cached_auth_context(digest) is ctx
    invalidate_auth_context(api_key_id=ctx.api_key_id)
    assert get_cached_auth_context(digest) is None


def test_expired_api_key_not_served_from_auth_cache() -> None:
    digest = "c" * 64
    ctx = _ctx(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    cache_auth_context(digest, ctx)
    assert get_cached_auth_context(digest) is None


def test_snapshot_l1_hit_miss_drop() -> None:
    snapshot = {"subscription_status": "active", "features": {}, "cache_version": 1}
    set_l1_snapshot(42, snapshot)
    assert get_l1_snapshot(42) == snapshot
    drop_l1_snapshot(42)
    assert get_l1_snapshot(42) is None


def test_snapshot_l1_expires_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 5_000.0}
    monkeypatch.setattr(
        "billing_platform.services.hotpath_cache.time.monotonic",
        lambda: clock["t"],
    )
    set_l1_snapshot(9, {"subscription_status": "active", "cache_version": 1})
    assert get_l1_snapshot(9) is not None
    clock["t"] += 1.0
    assert get_l1_snapshot(9) is None


def test_org_l1_roundtrip_and_drop_with_snapshot() -> None:
    public_id = uuid.uuid4()
    set_l1_org(public_id, 99)
    assert get_l1_org(public_id) == (99, public_id)
    set_l1_snapshot(99, {"subscription_status": "active", "cache_version": 2})
    drop_l1_snapshot(99)
    assert get_l1_snapshot(99) is None
    assert get_l1_org(public_id) is None


def test_clear_hotpath_caches_empties_all_maps() -> None:
    digest = "d" * 64
    public_id = uuid.uuid4()
    cache_auth_context(digest, _ctx())
    set_l1_snapshot(1, {"cache_version": 1})
    set_l1_org(public_id, 1)
    clear_hotpath_caches()
    assert get_cached_auth_context(digest) is None
    assert get_l1_snapshot(1) is None
    assert get_l1_org(public_id) is None
