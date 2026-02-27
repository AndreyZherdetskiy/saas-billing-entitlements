"""Assert pyproject ranges and uv.lock majors for the stack-upgrade pass."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _lock_version(name: str) -> str:
    text = (ROOT / "uv.lock").read_text()
    needle = f'name = "{name}"\nversion = "'
    idx = text.find(needle)
    assert idx != -1, name
    rest = text[idx + len(needle) :]
    return rest.split('"', 1)[0]


def test_pyproject_runtime_ranges() -> None:
    deps = _pyproject()["project"]["dependencies"]
    joined = "\n".join(deps)
    assert "fastapi>=0.141,<0.142" in joined
    assert "uvicorn[standard]>" in joined and "<0.53" in joined
    assert "pydantic>=2.10,<3" in joined
    assert "celery>=5.6,<5.7" in joined
    assert "celery[redis]" not in joined
    assert "redis>=8.1,<9" in joined
    assert "override-dependencies" not in _pyproject().get("tool", {}).get("uv", {})
    assert "aiokafka>=0.14,<0.15" in joined
    assert "alembic>=1.14,<2" in joined
    assert "structlog>=26,<27" in joined
    assert "asyncpg>=0.31,<0.32" in joined
    assert "opentelemetry-api>=1.44,<1.45" in joined
    assert "opentelemetry-sdk>=1.44,<1.45" in joined
    assert "opentelemetry-exporter-otlp-proto-http>=1.44,<1.45" in joined
    assert "opentelemetry-instrumentation-fastapi>=0.65b0,<0.66" in joined
    assert "sqlalchemy[asyncio]>=2.0,<2.1" in joined
    assert "httpx>=0.28,<0.29" in joined
    assert "pydantic-settings>=2.6,<3" in joined


def test_uv_lock_majors() -> None:
    assert _lock_version("fastapi").startswith("0.141.")
    assert _lock_version("celery").startswith("5.6.")
    assert _lock_version("redis").startswith("8.")
    assert _lock_version("aiokafka").startswith("0.14.")
    assert _lock_version("opentelemetry-api").startswith("1.44.")
    instr = _lock_version("opentelemetry-instrumentation-fastapi")
    assert instr.startswith("0.65b")
    assert _lock_version("structlog").startswith("26.")
    assert not any(x.startswith("fastapi>=0.115") for x in _pyproject()["project"]["dependencies"])
