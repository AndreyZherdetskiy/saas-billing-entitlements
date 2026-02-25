"""Integration tests: Helm chart renders four Stage 3 workloads."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from tests.cli_tools import require_helm

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART_PATH = REPO_ROOT / "deploy" / "helm" / "billing-platform"

EXPECTED_DEPLOYMENTS = frozenset(
    {
        "billing-platform-api",
        "billing-platform-worker",
        "billing-platform-beat",
        "billing-platform-outbox-relay",
    }
)


def _helm_template() -> list[dict]:
    helm = require_helm()
    result = subprocess.run(
        [helm, "template", "billing-platform", str(CHART_PATH)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        pytest.fail(
            "helm template failed:\n" f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    documents: list[dict] = []
    for doc in yaml.safe_load_all(result.stdout):
        if isinstance(doc, dict):
            documents.append(doc)
    return documents


def test_helm_template_renders_four_workload_deployments() -> None:
    documents = _helm_template()
    deployment_names = {
        doc["metadata"]["name"] for doc in documents if doc.get("kind") == "Deployment"
    }
    assert deployment_names == EXPECTED_DEPLOYMENTS


def test_helm_outbox_relay_replica_count_is_two() -> None:
    documents = _helm_template()
    relay_deployments = [
        doc
        for doc in documents
        if doc.get("kind") == "Deployment"
        and doc["metadata"]["name"] == "billing-platform-outbox-relay"
    ]
    assert len(relay_deployments) == 1
    assert relay_deployments[0]["spec"]["replicas"] == 2


def test_helm_template_wires_configmap_and_secret_refs() -> None:
    documents = _helm_template()
    deployments = [doc for doc in documents if doc.get("kind") == "Deployment"]

    assert len(deployments) == 4
    for deployment in deployments:
        containers = deployment["spec"]["template"]["spec"]["containers"]
        assert containers, f"{deployment['metadata']['name']} has no containers"

        env_from = containers[0].get("envFrom", [])
        refs = {
            (item.get("configMapRef") or item.get("secretRef") or {}).get("name")
            for item in env_from
        }
        assert "billing-platform-config" in refs
        assert "billing-platform-secrets" in refs
