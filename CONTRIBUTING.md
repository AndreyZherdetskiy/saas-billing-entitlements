# CONTRIBUTING (local git-flow)

## Scope

This repository is developed **locally**. Remote GitHub / `git push` / PR / staging deploy — **outside mandatory DoD** until the human explicitly requests it.

## Before you start

1. Read [`AGENTS.md`](AGENTS.md) and the current plan in `docs/plans/`.
2. Follow spec invariants (`spec.md`) and project coding standards in [`AGENTS.md`](AGENTS.md) §2.
3. Task execution — via `superpowers:subagent-driven-development`: Implementer ≠ Reviewer.

## Local cycle

```bash
uv sync --group dev
uv run pre-commit install
docker compose -p billing-platform -f deploy/compose/docker-compose.yml up -d --build
uv run alembic upgrade head
make lint typecheck test-unit
# Before commit: `uv run pre-commit run --all-files` (CI still runs `make lint`).
# test-unit requires Docker daemon (PostgresContainer); without it tests skip and coverage gate fails.
# test-integration: host pytest + Testcontainers (Docker required). Helm chart tests skip if helm is missing.
# Live compose HTTP probes: after make compose-core, `uv run pytest -m live_compose`.
make test-integration
```

## Before first push

Before `git add` / first commit:

1. `git status` — ensure paths from `.gitignore` are not staged.
2. Do **not** use `git add -f` for: `.env`, `.local/`, `.coverage`, or other local artifacts listed in `.gitignore`.
3. Do not commit keys, PII dumps, or compose secrets.

## Commits

- Commit **only on explicit request** from the repository owner.
- Messages — explain "why", not a diff recap (e.g. `feat: idempotent webhook processor`).
- Do not commit `.env`, keys, or PII dumps.
- Do not use `--no-verify`, force push, or amend others' commits.

## Branches (when git remote exists)

Recommended local style: `task/N-short-name` from `main`. Merge/PR — only on human command.

## ADR

Record architectural forks in `docs/adr/` **before** or together with code that depends on them. Critical for stage 1: 001, 002, 004, 005, 006, 010.

## Documentation

All tracked documentation (`README.md`, `AGENTS.md`, `CONTRIBUTING.md`, `spec.md`, `docs/**`) is professional English for operators. Code identifiers, paths, and API names stay as-is.
