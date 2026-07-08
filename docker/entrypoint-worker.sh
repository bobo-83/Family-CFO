#!/bin/sh
# Worker entrypoint: wait for PostgreSQL, then start the background jobs
# process. Compose orders this after the API is healthy (so migrations have
# already run); the DB wait here is a belt-and-suspenders check.
set -e

POSTGRES_HOST="${POSTGRES_HOST:-db}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-family_cfo}"

echo "Waiting for PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT}..."
until pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" >/dev/null 2>&1; do
  sleep 1
done

echo "Starting background worker..."
exec family-cfo-worker
