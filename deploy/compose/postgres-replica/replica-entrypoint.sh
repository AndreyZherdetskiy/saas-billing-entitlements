#!/usr/bin/env bash
set -euo pipefail

PRIMARY_HOST="${POSTGRES_PRIMARY_HOST:-postgres}"
PRIMARY_PORT="${POSTGRES_PRIMARY_PORT:-5432}"
REPLICATION_USER="${POSTGRES_REPLICATION_USER:?POSTGRES_REPLICATION_USER is required}"
REPLICATION_PASSWORD="${POSTGRES_REPLICATION_PASSWORD:?POSTGRES_REPLICATION_PASSWORD is required}"
DATA_DIR="/var/lib/postgresql/data"

# Do not export PGPASSWORD before wait: pg_isready -U billing + replicator password
# logs FATAL "password authentication failed for user billing" on the primary.
until pg_isready -h "${PRIMARY_HOST}" -p "${PRIMARY_PORT}" >/dev/null 2>&1; do
    echo "postgres-replica: waiting for primary at ${PRIMARY_HOST}:${PRIMARY_PORT}..."
    sleep 2
done

if [ ! -s "${DATA_DIR}/PG_VERSION" ]; then
    echo "postgres-replica: cloning data directory from primary..."
    export PGPASSWORD="${REPLICATION_PASSWORD}"
    rm -rf "${DATA_DIR:?}"/*
    pg_basebackup \
        -h "${PRIMARY_HOST}" \
        -p "${PRIMARY_PORT}" \
        -U "${REPLICATION_USER}" \
        -D "${DATA_DIR}" \
        -Fp -Xs -P -R
    chown -R postgres:postgres "${DATA_DIR}"
fi

exec docker-entrypoint.sh postgres
