"""Pin tests for .pre-commit-config.yaml and dev dependency wiring."""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _precommit_config() -> dict:
    assert PRE_COMMIT_CONFIG.is_file(), ".pre-commit-config.yaml must exist"
    return yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))


def _repo_hooks(repo_url: str) -> list[dict]:
    for repo in _precommit_config()["repos"]:
        if repo.get("repo") == repo_url:
            return repo.get("hooks", [])
    return []


def test_precommit_config_exists() -> None:
    assert PRE_COMMIT_CONFIG.is_file()


def test_ruff_precommit_rev_matches_uv_lock() -> None:
    config = _precommit_config()
    ruff_repo = next(
        r for r in config["repos"] if r["repo"] == "https://github.com/astral-sh/ruff-pre-commit"
    )
    assert ruff_repo["rev"] == "v0.8.6"


def test_ruff_hook_order_check_before_format() -> None:
    hooks = _repo_hooks("https://github.com/astral-sh/ruff-pre-commit")
    hook_ids = [hook["id"] for hook in hooks]
    # v0.8.6 ruff-pre-commit: hook id `ruff` (runs ruff check); v0.12+ uses `ruff-check`.
    assert hook_ids == ["ruff", "ruff-format"]

    check = hooks[0]
    assert check["args"] == ["--fix"]


def test_locustfile_import_local_hook() -> None:
    local_repo = next(r for r in _precommit_config()["repos"] if r["repo"] == "local")
    hook = next(h for h in local_repo["hooks"] if h["id"] == "locustfile-import")
    assert hook["entry"] == 'uv run --group load python -c "import loadtests.locustfile"'
    assert hook["language"] == "system"
    assert hook["pass_filenames"] is False
    assert hook["files"] == r"^(loadtests/.*\.py|pyproject.toml|uv.lock)$"


def test_precommit_in_pyproject_dev_group() -> None:
    dev = _pyproject()["dependency-groups"]["dev"]
    assert "pre-commit>=4,<5" in dev
