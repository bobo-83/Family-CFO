# Docker Spec

## Goal

Family CFO should run on a home server with:

```bash
docker compose up -d
```

## Planned Containers

### family-cfo-web

Angular dashboard.

### family-cfo-api

FastAPI backend.

### family-cfo-db

PostgreSQL.

### family-cfo-vector

Qdrant.

### family-cfo-vllm

Local LLM runtime.

### family-cfo-worker

OCR, imports, scheduled reports, and background tasks.

## Future Containers

- Reverse proxy
- Monitoring
- Backup

## Volumes

Persistent data:

- PostgreSQL data
- Qdrant data
- Model cache
- Encrypted backups
- Import staging

## Network Rules

- Internal services communicate on a private Docker network.
- Only intended UI/API ports are exposed.
- vLLM should not be exposed publicly by default.

## Acceptance Criteria

- Compose file supports local development and home-server deployment profiles.
- Secrets are provided through environment files or Docker secrets.
- Volumes are documented.
- Backup and restore strategy is documented before release.
