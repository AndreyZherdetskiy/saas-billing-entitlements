"""Optional CLI probes for integration tests.

Skip when an external tool is missing (pytest 8 skipping docs: skip if a
required external resource is unavailable). CI installs Helm so chart tests run.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.docker_engine import docker_cli_available


def require_helm() -> str:
    """Return a Helm 3+ binary path, or skip the test."""
    candidates = [
        shutil.which("helm"),
        str(Path.home() / ".local" / "bin" / "helm"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    pytest.skip("helm CLI not found; install Helm 3+ (https://helm.sh/docs/intro/install/)")


def require_docker_compose() -> None:
    """Skip when `docker compose config` cannot run."""
    if not docker_cli_available():
        pytest.skip("Docker CLI unavailable for `docker compose config`")
