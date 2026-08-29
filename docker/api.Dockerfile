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

COPY services/ /app/services/
# Copy only files that affect the installed API package before pip. BUILD is
# deliberately excluded from this layer, so a release-number-only change can
# reuse the dependency/package install cache below.
COPY apps/api/pyproject.toml apps/api/README.md /app/apps/api/
COPY apps/api/src/ /app/apps/api/src/

# Install the five service packages first, then the API (which imports them at
# runtime). Non-editable: the built image is a self-contained artifact.
RUN pip install --no-cache-dir \
        ./services/financial-engine \
        ./services/ai-orchestrator \
        ./services/ocr-worker \
        ./services/scheduler \
        ./services/backup \
    && pip install --no-cache-dir ./apps/api

# Runtime-only files do not affect the installed wheel and therefore belong
# after pip's expensive layer.
COPY apps/api/alembic.ini /app/apps/api/alembic.ini
COPY database/ /app/database/

# The version this image reports at /health (ADR 0074): the repo-wide
# MAJOR.MINOR contract plus the api's own BUILD, composed so the running
# container reads one file and nothing downstream has to know the scheme.
#
# LAST, deliberately. These two files change on every release, and anything
# after a changed COPY is rebuilt — putting them above `pip install` would make
# each build bump reinstall the whole dependency tree. That was survivable when
# one version covered the whole repo and bumps were rare; per-component builds
# make them routine.
COPY VERSION /tmp/CONTRACT
COPY apps/api/BUILD /tmp/BUILD
RUN printf '%s.%s\n' "$(tr -d '[:space:]' < /tmp/CONTRACT)" \
                     "$(tr -d '[:space:]' < /tmp/BUILD)" > /app/VERSION \
    && rm /tmp/CONTRACT /tmp/BUILD

COPY docker/entrypoint-api.sh docker/entrypoint-worker.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint-api.sh /usr/local/bin/entrypoint-worker.sh

WORKDIR /app/apps/api
EXPOSE 8000

CMD ["/usr/local/bin/entrypoint-api.sh"]
