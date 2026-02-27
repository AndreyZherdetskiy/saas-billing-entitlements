"""Pin tests: integration suite runs on the Docker host, not compose.test."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"


def _ci() -> dict:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def test_makefile_test_integration_is_host_pytest() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "docker-compose.test.yml" not in text
    assert "pytest tests/integration" in text
    assert "not live_compose" in text


def test_compose_test_overlay_removed() -> None:
    assert not (ROOT / "deploy/compose/docker-compose.test.yml").is_file()
    assert not (ROOT / "deploy/docker/Dockerfile.test").is_file()


def test_ci_test_integration_installs_helm_and_runs_make() -> None:
    job = _ci()["jobs"]["test-integration"]
    uses = [str(step.get("uses", "")) for step in job["steps"]]
    assert any("setup-helm" in item for item in uses)
    runs = [str(step.get("run", "")) for step in job["steps"]]
    blob = "\n".join(runs)
    assert "make test-integration" in blob
    assert "docker-compose.test" not in blob


def test_conftest_restores_alembic_usage_partitions_between_tests() -> None:
    text = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    assert "ensure_current_and_next_partitions" in text
    assert "format('DROP TABLE %I'" in text
