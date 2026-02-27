"""Parse `docker port` output used when Testcontainers cannot reach docker.sock."""

from __future__ import annotations

import pytest
from tests.docker_engine import docker_sdk_likely_available, parse_published_port


def test_parse_published_port_ipv4() -> None:
    assert parse_published_port("0.0.0.0:32768") == 32768


def test_parse_published_port_localhost() -> None:
    assert parse_published_port("127.0.0.1:54321\n") == 54321


def test_parse_published_port_prefers_first_line() -> None:
    stdout = "0.0.0.0:32768\n[::]:32768\n"
    assert parse_published_port(stdout) == 32768


def test_sdk_probe_is_false_without_unix_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(
        "tests.docker_engine.docker_unix_socket_present",
        lambda: False,
    )
    assert docker_sdk_likely_available() is False
