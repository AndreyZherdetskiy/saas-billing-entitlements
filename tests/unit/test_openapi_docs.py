"""OpenAPI / Swagger documentation contract tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from billing_platform.main import create_app

_SKIP_SCHEMAS = frozenset({"HTTPValidationError", "ValidationError"})


def _collect_properties_missing_descriptions(spec: dict) -> list[str]:
    missing: list[str] = []
    schemas = spec.get("components", {}).get("schemas", {})
    for schema_name, schema in schemas.items():
        if schema_name in _SKIP_SCHEMAS:
            continue
        properties = schema.get("properties", {})
        for prop_name, prop_schema in properties.items():
            if "description" not in prop_schema:
                missing.append(f"{schema_name}.{prop_name}")
    return missing


def _operation_has_request_examples(spec: dict, path: str, method: str) -> bool:
    operation = spec.get("paths", {}).get(path, {}).get(method.lower())
    if operation is None:
        return False
    request_body = operation.get("requestBody", {})
    content = request_body.get("content", {})
    json_content = content.get("application/json", {})
    return bool(json_content.get("examples") or json_content.get("example"))


def _any_operation_documents_status(spec: dict, status_code: str) -> bool:
    for path_item in spec.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses", {})
            if status_code in responses:
                return True
    return False


@pytest.fixture
def openapi_spec() -> dict:
    return create_app().openapi()


def test_info_summary_and_description(openapi_spec: dict) -> None:
    info = openapi_spec["info"]
    assert info.get("summary")
    assert info.get("description")


def test_tags_non_empty(openapi_spec: dict) -> None:
    tags = openapi_spec.get("tags", [])
    assert tags


def test_all_schema_properties_have_descriptions(openapi_spec: dict) -> None:
    missing = _collect_properties_missing_descriptions(openapi_spec)
    assert missing == [], f"properties missing description: {missing}"


def test_post_organizations_has_request_examples(openapi_spec: dict) -> None:
    assert _operation_has_request_examples(openapi_spec, "/v1/organizations", "post")


def test_evaluate_openapi_has_no_allow_stale_parameter(openapi_spec: dict) -> None:
    operation = openapi_spec["paths"]["/v1/entitlements/evaluate"]["post"]
    param_names = [param["name"] for param in operation.get("parameters", [])]
    assert "allow_stale" not in param_names, param_names


def test_at_least_one_operation_documents_404(openapi_spec: dict) -> None:
    assert _any_operation_documents_status(openapi_spec, "404")


@pytest.mark.asyncio
async def test_docs_endpoint_returns_swagger_html() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()
