#!/bin/sh
# API entrypoint: wait for PostgreSQL, run migrations, then start uvicorn.
# The API is the only service that runs migrations, so there is no race with
# the worker (which waits for the API to be healthy before starting).
set -e

POSTGRES_HOST="${POSTGRES_HOST:-db}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-family_cfo}"

echo "Waiting for PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT}..."
until pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" >/dev/null 2>&1; do
  sleep 1
done

echo "Running database migrations..."
cd /app/apps/api
python -m alembic -c alembic.ini upgrade head

echo "Starting API..."
exec python -m uvicorn family_cfo_api.main:app --host 0.0.0.0 --port 8000
