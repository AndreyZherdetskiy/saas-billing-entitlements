"""Pin test: CI workflow exposes separate Locust load jobs."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
ENV_EXAMPLE = ROOT / ".env.example"

# Compose --env-file and GNU make `include` both reject uncommented prose.
_DOTENV_LINE = re.compile(
    r"^\s*(?:#.*|[A-Za-z_][A-Za-z0-9_]*=.*)?\s*$",
)

LOAD_JOBS = ("load-harness", "load-locust-smoke")
CORE_JOBS = ("lint", "typecheck", "test-unit", "test-integration")


def _load_ci() -> dict:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def _job_run_lines(job_name: str) -> list[str]:
    workflow = _load_ci()
    job = workflow["jobs"][job_name]
    lines: list[str] = []
    for step in job.get("steps", []):
        if "run" in step:
            run = step["run"]
            if isinstance(run, str):
                lines.append(run)
            else:
                lines.extend(str(line) for line in run)
    return lines


def _job_run_blob(job_name: str) -> str:
    return "\n".join(_job_run_lines(job_name))


def test_load_jobs_exist() -> None:
    jobs = _load_ci()["jobs"]
    for name in LOAD_JOBS:
        assert name in jobs, f"missing CI job {name!r}"


def test_core_jobs_still_exist() -> None:
    jobs = _load_ci()["jobs"]
    for name in CORE_JOBS:
        assert name in jobs, f"missing core CI job {name!r}"


def test_load_harness_syncs_load_group_and_runs_helper_tests() -> None:
    blob = _job_run_blob("load-harness")
    assert "uv sync --frozen" in blob
    assert "--group load" in blob
    assert "tests/unit/test_load_helpers.py" in blob
    assert "tests/unit/test_load_grafana_helpers.py" in blob
    assert "tests/unit/test_perf_overlay.py" in blob


def test_load_harness_lists_locustfile() -> None:
    blob = _job_run_blob("load-harness")
    assert "locust" in blob
    assert "loadtests/locustfile.py" in blob
    assert "--list" in blob


def test_env_example_is_valid_dotenv() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        assert _DOTENV_LINE.match(
            line
        ), f"{ENV_EXAMPLE.name}:{lineno} is not a comment or KEY=value: {line!r}"


def test_gnu_make_can_include_env_example(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "Makefile").write_text("include .env\nall:\n\t@true\n", encoding="utf-8")
    subprocess.run(["make", "-C", str(tmp_path)], check=True)


def test_load_locust_smoke_runs_compose_and_make_load_locust() -> None:
    job = _load_ci()["jobs"]["load-locust-smoke"]
    assert job.get("timeout-minutes") == 20
    blob = _job_run_blob("load-locust-smoke")
    assert "cp .env.example .env" in blob
    assert "make compose-core" in blob
    assert "-m live_compose" in blob
    assert "make load-locust" in blob


def test_load_locust_smoke_teardown_uses_profile_aware_compose_down() -> None:
    blob = _job_run_blob("load-locust-smoke")
    assert "make compose-down" in blob


def test_load_locust_smoke_logs_api_on_failure() -> None:
    workflow = _load_ci()
    steps = workflow["jobs"]["load-locust-smoke"]["steps"]
    failure_steps = [s for s in steps if s.get("if") == "failure()"]
    assert failure_steps, "expected at least one step with if: failure()"
    blob = "\n".join(
        s["run"] if isinstance(s.get("run"), str) else "\n".join(s.get("run", []))
        for s in failure_steps
        if "run" in s
    )
    assert "billing-api" in blob
    assert "logs" in blob


def test_core_jobs_do_not_run_locust_or_k6() -> None:
    for name in CORE_JOBS:
        blob = _job_run_blob(name).lower()
        assert "locust" not in blob, f"{name} must not run locust"
        assert "k6" not in blob, f"{name} must not run k6"
