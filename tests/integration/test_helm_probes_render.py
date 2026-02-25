"""Integration tests: Helm chart renders probes and HPA stub."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from tests.cli_tools import require_helm

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART_PATH = REPO_ROOT / "deploy" / "helm" / "billing-platform"


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


def _deployment_by_component(documents: list[dict], component: str) -> dict:
    for doc in documents:
        if doc.get("kind") != "Deployment":
            continue
        labels = doc.get("metadata", {}).get("labels", {})
        if labels.get("app.kubernetes.io/component") == component:
            return doc
    pytest.fail(f"Deployment with component={component!r} not found")


def _container_probes(deployment: dict) -> dict:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    assert containers, f"{deployment['metadata']['name']} has no containers"
    return containers[0]


def test_api_deployment_has_http_probes() -> None:
    documents = _helm_template()
    api = _deployment_by_component(documents, "api")
    probes = _container_probes(api)

    liveness = probes.get("livenessProbe")
    readiness = probes.get("readinessProbe")
    assert liveness is not None, "api missing livenessProbe"
    assert readiness is not None, "api missing readinessProbe"

    assert liveness["httpGet"]["path"] == "/health/live"
    assert liveness["httpGet"]["port"] == 8000
    assert readiness["httpGet"]["path"] == "/health/ready"
    assert readiness["httpGet"]["port"] == 8000


def test_worker_deployment_has_exec_probes() -> None:
    documents = _helm_template()
    worker = _deployment_by_component(documents, "worker")
    probes = _container_probes(worker)

    assert probes.get("livenessProbe", {}).get("exec") is not None
    assert probes.get("readinessProbe", {}).get("exec") is not None


def test_outbox_relay_deployment_has_exec_probes() -> None:
    documents = _helm_template()
    relay = _deployment_by_component(documents, "outbox-relay")
    probes = _container_probes(relay)

    assert probes.get("livenessProbe", {}).get("exec") is not None
    assert probes.get("readinessProbe", {}).get("exec") is not None


def test_hpa_stub_renders_for_api() -> None:
    documents = _helm_template()
    hpas = [doc for doc in documents if doc.get("kind") == "HorizontalPodAutoscaler"]
    assert len(hpas) == 1

    hpa = hpas[0]
    assert hpa["spec"]["scaleTargetRef"]["kind"] == "Deployment"
    assert hpa["spec"]["scaleTargetRef"]["name"] == "billing-platform-api"
    assert hpa["spec"]["minReplicas"] >= 1
    assert hpa["spec"]["maxReplicas"] >= hpa["spec"]["minReplicas"]
