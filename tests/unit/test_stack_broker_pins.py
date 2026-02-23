"""Assert Compose/Docker/testcontainer image pins for the stack-upgrade pass."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"
DOCKERFILE_KAFKA_INIT = ROOT / "deploy" / "docker" / "Dockerfile.kafka-init"
DOCKERFILE_POSTGRES = ROOT / "deploy" / "docker" / "Dockerfile.postgres"
DOCKERFILE_MOCK_STRIPE = ROOT / "deploy" / "docker" / "Dockerfile.mock-stripe"
DOCKER_ENGINE = ROOT / "tests" / "docker_engine.py"

# Split so this file does not contain the forbidden literal tags under tests/.
FORBIDDEN_TAGS = ("redis:" + "7.4", "apache/kafka:" + "3.7.0")
SCAN_DIRS = (ROOT / "deploy", ROOT / "tests")


def test_compose_broker_and_postgres_images() -> None:
    text = COMPOSE.read_text()
    assert "image: redis:8.10" in text
    assert "image: apache/kafka:4.3.1" in text
    assert "image: local/billing-platform-kafka-init:4.3.1" in text
    assert "image: postgres:16.15" in text


def test_dockerfile_kafka_init_from() -> None:
    assert "FROM apache/kafka:4.3.1" in DOCKERFILE_KAFKA_INIT.read_text()


def test_dockerfile_postgres_from() -> None:
    assert "FROM postgres:16.15" in DOCKERFILE_POSTGRES.read_text()


def test_dockerfile_mock_stripe_python_pins() -> None:
    text = DOCKERFILE_MOCK_STRIPE.read_text()
    assert "FROM python:3.12-slim" in text
    assert "fastapi>=0.141,<0.142" in text
    assert "uvicorn[standard]>=0.32,<0.53" in text
    assert "pydantic>=2.10,<3" in text
    assert "fastapi>=0.115" not in text
    assert "uvicorn[standard]>=0.32,<0.33" not in text
    assert "pydantic>=2.10,<2.11" not in text
    assert "--uid 10001" in text
    assert '"8001"' in text
    assert "USER app" in text


def test_redis_image_constant() -> None:
    text = DOCKER_ENGINE.read_text()
    assert 'REDIS_IMAGE = "redis:8.10"' in text
    assert ("redis:" + "7.4") not in text


def test_forbidden_old_tags_absent_under_deploy_and_tests() -> None:
    hits: list[str] = []
    for base in SCAN_DIRS:
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for tag in FORBIDDEN_TAGS:
                if tag in content:
                    hits.append(f"{path.relative_to(ROOT)}:{tag}")
    assert hits == [], hits
