"""Start ephemeral Docker engines via the `docker` CLI.

On Docker Desktop + WSL the Python SDK looks for `/var/run/docker.sock`, which
is often absent; the CLI still works (Windows engine). Testcontainers then
skips and coverage collapses. Prefer the SDK when it works; otherwise CLI.
"""

from __future__ import annotations

import functools
import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_DOCKER_RUN_TIMEOUT_S = 120
_READY_TIMEOUT_S = 60

REDIS_IMAGE = "redis:8.10"


def parse_published_port(docker_port_stdout: str) -> int:
    """Return the host TCP port from `docker port` stdout (first IPv4 mapping)."""
    for line in docker_port_stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("["):
            continue
        host_port = line.rsplit(":", 1)[-1]
        return int(host_port)
    raise ValueError(f"unparseable docker port output: {docker_port_stdout!r}")


def docker_unix_socket_present() -> bool:
    """True when the Docker Python SDK can use the default Unix socket."""
    return Path("/var/run/docker.sock").exists()


def docker_sdk_likely_available() -> bool:
    """Cheap probe: skip Testcontainers when the Unix socket is missing.

    Docker Desktop on WSL often exposes only the Windows engine via the `docker`
    CLI; the SDK then errors or hangs on `/var/run/docker.sock`.
    """
    docker_host = os.environ.get("DOCKER_HOST", "")
    if docker_host.startswith(("tcp://", "http://", "https://", "npipe://")):
        return True
    return docker_unix_socket_present()


@functools.lru_cache(maxsize=1)
def docker_cli_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, TimeoutError, OSError):
        return False
    return result.returncode == 0


def _run_docker(
    *args: str, timeout: int = _DOCKER_RUN_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@dataclass
class CliMappedContainer:
    container_id: str
    host: str
    port: int

    def stop(self) -> None:
        _run_docker("rm", "-f", self.container_id)


def _wait_exec(container_id: str, command: list[str]) -> None:
    deadline = time.monotonic() + _READY_TIMEOUT_S
    while time.monotonic() < deadline:
        probe = _run_docker("exec", container_id, *command, timeout=20)
        if probe.returncode == 0:
            return
        time.sleep(0.5)
    raise TimeoutError(
        f"container {container_id} not ready: {command!r} last={probe.stderr or probe.stdout}"
    )


def _run_mapped(
    *,
    image: str,
    container_port: int,
    env: dict[str, str],
    ready_exec: list[str],
) -> CliMappedContainer:
    name = f"bp-test-{uuid.uuid4().hex[:12]}"
    args = ["run", "-d", "--name", name, "-p", f"127.0.0.1::{container_port}"]
    for key, value in env.items():
        args.extend(["-e", f"{key}={value}"])
    args.append(image)
    started = _run_docker(*args)
    if started.returncode != 0:
        raise RuntimeError(started.stderr or started.stdout)
    container_id = started.stdout.strip() or name
    try:
        mapped = _run_docker("port", container_id, f"{container_port}/tcp")
        if mapped.returncode != 0:
            raise RuntimeError(mapped.stderr or mapped.stdout)
        port = parse_published_port(mapped.stdout)
        _wait_exec(container_id, ready_exec)
    except Exception:
        _run_docker("rm", "-f", container_id)
        raise
    return CliMappedContainer(container_id=container_id, host="127.0.0.1", port=port)


@contextmanager
def postgres_via_docker_cli() -> Iterator[tuple[str, str, str, str]]:
    """Yield (host, port, user, password) for an ephemeral Postgres 16."""
    user = "test"
    password = "test"
    dbname = "test"
    container = _run_mapped(
        image="postgres:16",
        container_port=5432,
        env={
            "POSTGRES_USER": user,
            "POSTGRES_PASSWORD": password,
            "POSTGRES_DB": dbname,
        },
        ready_exec=["pg_isready", "-U", user, "-d", dbname],
    )
    try:
        yield container.host, str(container.port), user, password
    finally:
        container.stop()


@contextmanager
def redis_via_docker_cli() -> Iterator[tuple[str, str]]:
    """Yield (host, port) for an ephemeral Redis."""
    container = _run_mapped(
        image=REDIS_IMAGE,
        container_port=6379,
        env={},
        ready_exec=["redis-cli", "ping"],
    )
    try:
        yield container.host, str(container.port)
    finally:
        container.stop()
