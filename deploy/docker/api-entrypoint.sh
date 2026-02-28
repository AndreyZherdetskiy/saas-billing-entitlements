#!/bin/sh
# billing-api: migrations → deterministic demo seed → exec CMD.
# RUN_MIGRATIONS / RUN_DEMO_SEED: 0|false|no|off to skip.
set -eu

_truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off) return 1 ;;
    *) return 0 ;;
  esac
}

if _truthy "${RUN_MIGRATIONS:-true}"; then
  echo ">>> alembic upgrade head"
  alembic upgrade head
fi

if _truthy "${RUN_DEMO_SEED:-true}"; then
  echo ">>> demo seed (deterministic local catalog + tenant)"
  python -m billing_platform.bootstrap
fi

exec "$@"
