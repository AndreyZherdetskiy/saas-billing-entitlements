"""Process-local TTL maps for evaluate auth, snapshot, and org identity."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from billing_platform.config import get_settings

if TYPE_CHECKING:
    from billing_platform.services.api_keys import AuthContext

# digest → (AuthContext, monotonic expiry)
_auth_by_digest: dict[str, tuple[AuthContext, float]] = {}
# api_key_id → digest (no independent TTL; dropped with the digest entry)
_auth_by_key_id: dict[UUID, str] = {}
# organization_id → (snapshot dict, monotonic expiry)
_snapshot_l1: dict[int, tuple[dict[str, Any], float]] = {}
# public_id → (organization_id, public_id, monotonic expiry)
_org_l1: dict[UUID, tuple[int, UUID, float]] = {}
# organization_id → public_id (for drop_l1_snapshot)
_org_l1_by_id: dict[int, UUID] = {}


def clear_hotpath_caches() -> None:
    _auth_by_digest.clear()
    _auth_by_key_id.clear()
    _snapshot_l1.clear()
    _org_l1.clear()
    _org_l1_by_id.clear()


def cache_auth_context(digest: str, ctx: AuthContext) -> None:
    ttl = get_settings().auth_cache_ttl_seconds
    if ttl <= 0:
        return
    previous = _auth_by_key_id.get(ctx.api_key_id)
    if previous is not None and previous != digest:
        _auth_by_digest.pop(previous, None)
    _auth_by_digest[digest] = (ctx, time.monotonic() + ttl)
    _auth_by_key_id[ctx.api_key_id] = digest


def get_cached_auth_context(digest: str) -> AuthContext | None:
    item = _auth_by_digest.get(digest)
    if item is None:
        return None
    ctx, expires_at = item
    if time.monotonic() >= expires_at:
        _auth_by_digest.pop(digest, None)
        if _auth_by_key_id.get(ctx.api_key_id) == digest:
            _auth_by_key_id.pop(ctx.api_key_id, None)
        return None
    key_expires = getattr(ctx, "expires_at", None)
    if key_expires is not None and key_expires <= datetime.now(UTC):
        invalidate_auth_context(api_key_id=ctx.api_key_id)
        return None
    return ctx


def invalidate_auth_context(*, api_key_id: UUID) -> None:
    digest = _auth_by_key_id.pop(api_key_id, None)
    if digest is not None:
        _auth_by_digest.pop(digest, None)


def get_l1_snapshot(organization_id: int) -> dict[str, Any] | None:
    item = _snapshot_l1.get(organization_id)
    if item is None:
        return None
    snapshot, expires_at = item
    if time.monotonic() >= expires_at:
        _snapshot_l1.pop(organization_id, None)
        return None
    return snapshot


def set_l1_snapshot(organization_id: int, snapshot: dict[str, Any]) -> None:
    ttl = get_settings().entitlement_l1_ttl_seconds
    if ttl <= 0:
        return
    _snapshot_l1[organization_id] = (snapshot, time.monotonic() + ttl)


def drop_l1_snapshot(organization_id: int) -> None:
    _snapshot_l1.pop(organization_id, None)
    public_id = _org_l1_by_id.pop(organization_id, None)
    if public_id is not None:
        _org_l1.pop(public_id, None)


def get_l1_org(public_id: UUID) -> tuple[int, UUID] | None:
    item = _org_l1.get(public_id)
    if item is None:
        return None
    organization_id, stored_public_id, expires_at = item
    if time.monotonic() >= expires_at:
        _org_l1.pop(public_id, None)
        if _org_l1_by_id.get(organization_id) == public_id:
            _org_l1_by_id.pop(organization_id, None)
        return None
    return organization_id, stored_public_id


def set_l1_org(public_id: UUID, organization_id: int) -> None:
    ttl = get_settings().entitlement_l1_ttl_seconds
    if ttl <= 0:
        return
    previous = _org_l1_by_id.get(organization_id)
    if previous is not None and previous != public_id:
        _org_l1.pop(previous, None)
    _org_l1[public_id] = (organization_id, public_id, time.monotonic() + ttl)
    _org_l1_by_id[organization_id] = public_id
