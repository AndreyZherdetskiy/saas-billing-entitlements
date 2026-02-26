"""Unit: get_auth_context is cache-first and does not Depends(get_read_session)."""

from __future__ import annotations

import inspect
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, get_args, get_origin, get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.params import Depends as DependsParam
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from billing_platform.api.deps import get_auth_context
from billing_platform.api.v1.entitlements import (
    CheckRequest,
    EvaluateRequest,
    get_redis,
    post_evaluate,
)
from billing_platform.db import get_read_session
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.services.api_keys import AuthContext, hash_api_key
from billing_platform.services.entitlements import EvaluateResponse
from billing_platform.services.hotpath_cache import (
    cache_auth_context,
    clear_hotpath_caches,
    get_l1_org,
    set_l1_snapshot,
)


def _annotated_depends_on(func: Any, dependency: Any) -> bool:
    hints = get_type_hints(func, include_extras=True)
    for annotation in hints.values():
        if get_origin(annotation) is not Annotated:
            continue
        for extra in get_args(annotation)[1:]:
            if isinstance(extra, DependsParam) and extra.dependency is dependency:
                return True
    return False


def test_get_auth_context_does_not_depend_on_get_read_session() -> None:
    assert "session" not in inspect.signature(get_auth_context).parameters
    assert _annotated_depends_on(get_auth_context, get_read_session) is False


def test_post_evaluate_does_not_depend_on_get_read_session() -> None:
    assert "session" not in inspect.signature(post_evaluate).parameters
    assert _annotated_depends_on(post_evaluate, get_read_session) is False


def test_post_evaluate_does_not_depend_on_get_redis() -> None:
    assert "redis" not in inspect.signature(post_evaluate).parameters
    assert _annotated_depends_on(post_evaluate, get_redis) is False


def _request_with_overrides(app: FastAPI) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/entitlements/evaluate",
            "raw_path": b"/v1/entitlements/evaluate",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("test", 80),
            "app": app,
        }
    )


def _ctx() -> AuthContext:
    return AuthContext(
        organization_id=11,
        role="product_service",
        key_prefix="bp_cached",
        api_key_id=uuid.uuid4(),
        organization_public_id=uuid.uuid4(),
        expires_at=None,
    )


@pytest.mark.asyncio
async def test_get_auth_context_cache_hit_does_not_open_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setenv("API_RATE_LIMIT_PLATFORM_ADMIN_PER_MINUTE", "0")
    from billing_platform.config import get_settings

    get_settings.cache_clear()
    clear_hotpath_caches()

    bearer = "bp_cached_test_key_value_001"
    ctx = _ctx()
    cache_auth_context(hash_api_key(bearer), ctx)

    opened = {"count": 0}

    async def override_get_read_session() -> AsyncIterator[AsyncSession]:
        opened["count"] += 1
        yield AsyncMock()  # type: ignore[misc]

    app = FastAPI()
    app.dependency_overrides[get_read_session] = override_get_read_session
    request = _request_with_overrides(app)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=bearer)

    with patch("billing_platform.api.deps.authenticate", new_callable=AsyncMock) as auth:
        result = await get_auth_context(request, credentials=credentials)

    auth.assert_not_called()
    assert opened["count"] == 0
    assert result.api_key_id == ctx.api_key_id
    assert result.organization_id == ctx.organization_id


def _tenant_ctx(*, organization_id: int, organization_public_id: uuid.UUID) -> AuthContext:
    return AuthContext(
        organization_id=organization_id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
        key_prefix="bp_tenant",
        api_key_id=uuid.uuid4(),
        organization_public_id=organization_public_id,
        expires_at=None,
    )


@pytest.mark.asyncio
async def test_post_evaluate_cross_tenant_returns_403_without_org_lookup() -> None:
    ctx = _tenant_ctx(organization_id=1, organization_public_id=uuid.uuid4())
    body = EvaluateRequest(
        organization_public_id=uuid.uuid4(),
        checks=[CheckRequest(feature_key="api_calls", quantity=1)],
    )
    with (
        patch(
            "billing_platform.api.v1.entitlements.get_organization_by_public_id",
            new_callable=AsyncMock,
        ) as org_spy,
        patch(
            "billing_platform.api.v1.entitlements.evaluate",
            new_callable=AsyncMock,
        ) as eval_spy,
        pytest.raises(HTTPException) as exc_info,
    ):
        await post_evaluate(MagicMock(), body, ctx)

    assert exc_info.value.status_code == 403
    assert "cross-tenant" in str(exc_info.value.detail)
    org_spy.assert_not_called()
    eval_spy.assert_not_called()


@pytest.mark.asyncio
async def test_post_evaluate_matching_tenant_skips_org_select_and_org_l1() -> None:
    public_id = uuid.uuid4()
    org_id = 11
    ctx = _tenant_ctx(organization_id=org_id, organization_public_id=public_id)
    body = EvaluateRequest(
        organization_public_id=public_id,
        checks=[CheckRequest(feature_key="api_calls", quantity=1)],
    )
    set_l1_snapshot(
        org_id,
        {
            "subscription_status": "active",
            "grace_active": False,
            "features": {},
            "cache_version": 1,
        },
    )
    fake = EvaluateResponse(
        organization_public_id=str(public_id),
        subscription_status="active",
        results=[],
        cache_hit=True,
        evaluated_at=datetime.now(UTC),
        version=1,
    )
    with (
        patch(
            "billing_platform.api.v1.entitlements.get_organization_by_public_id",
            new_callable=AsyncMock,
        ) as org_spy,
        patch(
            "billing_platform.api.v1.entitlements.evaluate",
            new_callable=AsyncMock,
            return_value=fake,
        ) as eval_spy,
    ):
        result = await post_evaluate(MagicMock(), body, ctx)

    org_spy.assert_not_called()
    eval_spy.assert_called_once()
    assert eval_spy.await_args is not None
    assert eval_spy.await_args.args[0] is None
    assert eval_spy.await_args.kwargs["session"] is None
    assert result.cache_hit is True
    assert get_l1_org(public_id) is None
