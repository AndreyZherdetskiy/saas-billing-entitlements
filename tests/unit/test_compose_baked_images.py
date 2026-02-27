"""File-based: observability configs baked into images — no WSL bind-mount seed."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy/compose/docker-compose.yml"
MAKEFILE = ROOT / "Makefile"
DASHBOARDS_YAML = ROOT / "deploy/observability/grafana/provisioning/dashboards/dashboards.yaml"

BAKED_OBS_SERVICES = (
    "prometheus",
    "loki",
    "tempo",
    "grafana",
    "alloy",
)

NO_ENV_FILE_SERVICES = (
    "billing-api",
    "billing-worker",
    "billing-beat",
    "outbox-relay",
    "mock-stripe",
    "demo-ui",
)

# Host config paths that must not be bind-mounted after bake.
FORBIDDEN_BIND_NEEDLES = (
    "observability/prometheus.yml",
    "observability/prometheus/alerts.yml",
    "observability/loki.yaml",
    "observability/tempo.yaml",
    "observability/alloy.alloy",
    "observability/grafana/provisioning",
)

DOCKERFILE_EXPECTATIONS = {
    "deploy/compose/prometheus/Dockerfile": (
        "FROM prom/prometheus:v3.2.1",
        "COPY deploy/observability/prometheus.yml",
        "/etc/prometheus/prometheus.yml",
        "COPY deploy/observability/prometheus/alerts.yml",
        "/etc/prometheus/alerts.yml",
    ),
    "deploy/compose/loki/Dockerfile": (
        "FROM grafana/loki:3.3.2",
        "COPY deploy/observability/loki.yaml",
        "/etc/loki/loki.yaml",
    ),
    "deploy/compose/tempo/Dockerfile": (
        "FROM grafana/tempo:2.6.1",
        "COPY deploy/observability/tempo.yaml",
        "/etc/tempo/tempo.yaml",
    ),
    "deploy/compose/alloy/Dockerfile": (
        "FROM grafana/alloy:v1.7.5",
        "COPY deploy/observability/alloy.alloy",
        "/etc/alloy/config.alloy",
    ),
    "deploy/compose/grafana/Dockerfile": (
        "FROM grafana/grafana:11.5.2",
        "COPY deploy/observability/grafana/provisioning",
        "/etc/grafana/provisioning",
        "mkdir -p",
        "plugins",
        "alerting",
    ),
}

GRAFANA_PROVISIONING_DASHBOARDS = "/etc/grafana/provisioning/dashboards"
FORBIDDEN_DASHBOARDS_PATH = "/var/lib/grafana/dashboards"


def _compose_doc() -> dict:
    assert COMPOSE.is_file(), f"missing compose: {COMPOSE}"
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_baked_obs_services_use_build_not_config_binds() -> None:
    doc = _compose_doc()
    raw = COMPOSE.read_text(encoding="utf-8")
    services = doc["services"]

    for name in BAKED_OBS_SERVICES:
        svc = services[name]
        build = svc.get("build")
        assert isinstance(build, dict), f"{name}: expected build dict"
        assert (
            build.get("context") == "../.."
        ), f"{name}: build.context must be repo root relative to compose file"
        dockerfile = build.get("dockerfile")
        assert isinstance(dockerfile, str), f"{name}: missing build.dockerfile"
        assert dockerfile.startswith(
            "deploy/compose/"
        ), f"{name}: unexpected dockerfile {dockerfile}"

        for vol in svc.get("volumes") or []:
            vol_s = str(vol)
            for needle in FORBIDDEN_BIND_NEEDLES:
                assert needle not in vol_s, f"{name}: forbidden config bind still present: {vol_s}"

    for needle in FORBIDDEN_BIND_NEEDLES:
        assert needle not in raw, f"compose still references host bind path: {needle}"


def test_no_env_file_on_app_services() -> None:
    doc = _compose_doc()
    for name in NO_ENV_FILE_SERVICES:
        svc = doc["services"][name]
        assert (
            "env_file" not in svc
        ), f"{name}: env_file must be removed (keep environment interpolation only)"


def test_app_env_keeps_database_url_interpolation() -> None:
    """PgBouncer profile switches DSN via .env — do not hardcode postgres host."""
    raw = COMPOSE.read_text(encoding="utf-8")
    assert "DATABASE_URL: ${DATABASE_URL}" in raw
    api_env = _compose_doc()["services"]["billing-api"].get("environment") or {}
    assert "DATABASE_URL" in api_env
    assert "postgres://" not in str(api_env.get("DATABASE_URL", ""))
    assert "postgresql" not in str(api_env.get("DATABASE_URL", "")).split("$")[0]


def test_bake_dockerfiles_exist_and_copy_configs() -> None:
    for rel, needles in DOCKERFILE_EXPECTATIONS.items():
        path = ROOT / rel
        assert path.is_file(), f"missing Dockerfile: {rel}"
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{rel}: missing {needle!r}"


def test_grafana_dashboards_path_not_under_data_volume() -> None:
    """Billing keeps dashboards under provisioning (outside grafana_data)."""
    dockerfile = ROOT / "deploy/compose/grafana/Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    assert "/etc/grafana/provisioning" in text
    assert FORBIDDEN_DASHBOARDS_PATH not in text

    assert DASHBOARDS_YAML.is_file(), f"missing {DASHBOARDS_YAML}"
    doc = yaml.safe_load(DASHBOARDS_YAML.read_text(encoding="utf-8"))
    providers = doc.get("providers") or []
    assert providers, "dashboards.yaml: expected providers"
    path = providers[0].get("options", {}).get("path")
    assert path == GRAFANA_PROVISIONING_DASHBOARDS, (
        f"dashboards.yaml options.path must be {GRAFANA_PROVISIONING_DASHBOARDS}, " f"got {path!r}"
    )
    assert not path.startswith("/var/lib/grafana")


def test_billing_api_keeps_ready_healthcheck() -> None:
    doc = _compose_doc()
    hc = doc["services"]["billing-api"].get("healthcheck") or {}
    test = hc.get("test") or []
    joined = " ".join(str(part) for part in test)
    assert "python" in joined
    assert "/health/ready" in joined
    assert hc.get("start_period")


def test_makefile_observability_up_uses_wait() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "_compose_up_build:" in text
    # Shared up path used by observability-up must pass --wait.
    assert "up -d --build" in text and "--wait" in text
    assert "observability-up:" in text


def test_no_gitkeep_under_grafana_provisioning() -> None:
    provisioning = ROOT / "deploy/observability/grafana/provisioning"
    gitkeeps = list(provisioning.rglob(".gitkeep"))
    assert gitkeeps == [], f"found .gitkeep under provisioning: {gitkeeps}"
