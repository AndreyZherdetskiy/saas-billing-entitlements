"""Integration tests: Kafbat UI service in local Docker Compose."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
import yaml

from tests.cli_tools import require_docker_compose

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy" / "compose" / "docker-compose.yml"

BILLING_TOPICS = frozenset(
    {
        "billing.subscription.events",
        "billing.invoice.events",
        "billing.ledger.events",
        "billing.reconciliation.events",
        "billing.entitlement.events",
        "billing.dlq",
    },
)


def _rendered_compose_config() -> dict:
    require_docker_compose()
    env_file = REPO_ROOT / ".env.example"
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(COMPOSE_FILE),
            "config",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return yaml.safe_load(result.stdout)


def test_kafbat_ui_service_defined_with_pinned_image_and_kafka_cluster() -> None:
    config = _rendered_compose_config()
    assert "kafbat-ui" in config["services"]

    svc = config["services"]["kafbat-ui"]
    assert svc["image"] == "ghcr.io/kafbat/kafka-ui:v1.5.0"

    env = svc.get("environment", {})
    assert env["KAFKA_CLUSTERS_0_NAME"] == "local"
    assert env["KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS"] == "kafka:9092"

    published_ports = svc.get("ports", [])
    assert any("8081" in str(p) and "8080" in str(p) for p in published_ports)

    depends_on = svc.get("depends_on", {})
    assert "kafka" in depends_on
    assert depends_on["kafka"]["condition"] == "service_healthy"
    assert depends_on["kafka-init"]["condition"] == "service_completed_successfully"


def test_init_kafka_topics_script_lists_billing_topics() -> None:
    script = (REPO_ROOT / "deploy" / "compose" / "init-kafka-topics.sh").read_text()
    for topic in BILLING_TOPICS:
        assert topic in script


@pytest.mark.live_compose
def test_kafbat_ui_actuator_health_when_stack_running() -> None:
    port = 8081
    response = httpx.get(f"http://localhost:{port}/actuator/health", timeout=5.0)
    assert response.status_code == 200
    body = response.json()
    assert body.get("status") == "UP"
