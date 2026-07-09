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

Local LLM runtime. **On by default** (M17): no longer behind a Compose profile,
so `docker compose up -d` starts it and the api/worker are wired to use it
(`FAMILY_CFO_AI_*`). This assumes a GPU-capable host with the NVIDIA Container
Toolkit; the command enables tool-calling (`--enable-auto-tool-choice
--tool-call-parser`) required by the M16 agentic advisor. GPU-less hosts opt out
with `FAMILY_CFO_AI_ENABLED=false` and `docker compose up -d --scale vllm=0`.
Only the on-box local model is defaulted on; external/cloud runtimes remain
opt-in (ADR 0008). `scripts/deploy.sh` stands the whole stack up on a local or
remote host with one command — see the [AI Advisor guide](../guides/ai-advisor.md).

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
