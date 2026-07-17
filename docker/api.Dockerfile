# Family CFO API + worker image.
#
# Build context is the repo root so the sibling service packages, the API app,
# and the shared migration scripts are all available. The repo layout is
# preserved under /app so apps/api/alembic.ini's relative
# `%(here)s/../../database/migrations` path stays valid.
FROM python:3.12-slim

# postgresql-client provides pg_isready (DB wait in the entrypoints) and
# pg_dump/pg_restore (M8 encrypted backups). Its major version must match the
# DB server: a newer pg_dump emits GUCs an older server rejects on restore, and
# pg_dump refuses to dump a newer server. Debian trixie ships client 17, so
# docker-compose.yml pins postgres:17 to match.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY VERSION /app/VERSION
COPY services/ /app/services/
COPY apps/api/ /app/apps/api/
COPY database/ /app/database/

# Install the five service packages first, then the API (which imports them at
# runtime). Non-editable: the built image is a self-contained artifact.
RUN pip install --no-cache-dir \
        ./services/financial-engine \
        ./services/ai-orchestrator \
        ./services/ocr-worker \
        ./services/scheduler \
        ./services/backup \
    && pip install --no-cache-dir ./apps/api

COPY docker/entrypoint-api.sh docker/entrypoint-worker.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint-api.sh /usr/local/bin/entrypoint-worker.sh

WORKDIR /app/apps/api
EXPOSE 8000

CMD ["/usr/local/bin/entrypoint-api.sh"]
