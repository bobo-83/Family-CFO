# Docker

Run the whole Family CFO stack on a home server:

```bash
cp .env.example .env      # then edit .env — at minimum set POSTGRES_PASSWORD
docker compose up -d
```

The dashboard is then at `https://localhost:8443` (override with `WEB_TLS_PORT`); plain HTTP on `WEB_PORT` (8080) redirects to HTTPS.

## TLS / HTTPS

The `web` container terminates TLS and adds security headers (HSTS, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy`). On first start it generates a **self-signed** certificate (so you get working HTTPS immediately, with the expected browser warning). For a real deployment, either:

- mount your own certificate/key as `tls.crt` / `tls.key` into the `web_certs` volume (at `/etc/nginx/certs`), or
- front the stack with your own TLS reverse proxy (Caddy/Traefik/nginx) terminating a publicly-trusted certificate.

Automated public-CA issuance (ACME/Let's Encrypt) is intentionally not built in — see [ADR 0008](../docs/adr/0008-security-hardening-decisions.md).

## Core Services (default)

`docker compose up -d` brings these up:

- **db** — PostgreSQL 16, data in the `postgres_data` volume.
- **api** — FastAPI backend. Its entrypoint waits for PostgreSQL, runs `alembic upgrade head`, then starts uvicorn. It is the only service that runs migrations.
- **worker** — the background jobs process (`family-cfo-worker`): pending-import processing, scheduled weekly/monthly reports, and the daily backup. Starts only after the API is healthy.
- **web** — the Angular dashboard built to static files and served by nginx, which also proxies `/api` to the `api` container. The only service that publishes a host port by default.

Only `web` is reachable from outside the Docker network. The database, API, and worker are internal-only.

## AI runtime (on by default) and optional services

- **vllm** — a local LLM runtime, **on by default** (M17): it starts with `docker compose up -d` and the api/worker are wired to use it (`FAMILY_CFO_AI_*`). Needs a GPU (passed through via the NVIDIA Container Toolkit) and a multi-GB model download. The command enables tool-calling (`--enable-auto-tool-choice --tool-call-parser`), which the M16 agentic chat advisor requires; the model (`VLLM_MODEL`) and parser (`VLLM_TOOL_PARSER`) are set in `.env`. Households use it automatically; a household can override or disable via `PUT /api/v1/ai/runtime`. Never published to the host. See the [AI Advisor guide](../docs/guides/ai-advisor.md) for an end-to-end test. To run without a GPU:

  ```bash
  # in .env: FAMILY_CFO_AI_ENABLED=false
  docker compose up -d --scale vllm=0     # deterministic answers only
  ```

- **vllm-vision** — a small vision model (default Qwen2.5-VL-7B-Instruct) that describes chat photo attachments (ADR 0011). On by default alongside `vllm`; both share the GPU via `VLLM_GPU_FRACTION`/`VLLM_VISION_GPU_FRACTION`. Opt out with `FAMILY_CFO_AI_VISION_ENABLED=false` + `--scale vllm-vision=0`.

- **model-manager** — THE one privileged sidecar (ADR 0013): Docker socket + project mount, exposing a single validated operation (swap served models via `scripts/swap-model.sh`) so the dashboard's **Apply** button works. Internal network only, never published; the owner-gated API is its only caller. Remove with `--scale model-manager=0` to fall back to the CLI swap flow.

- **searxng** — self-hosted metasearch powering the chat `web_search` tool (live prices/public facts, ADR 0014). On by default; `FAMILY_CFO_SEARXNG_URL` defaults to `http://searxng:8080`. Remove with `--scale searxng=0` and the tool simply isn't offered to the model.

- **qdrant** — a vector store (ADR 0017) with `qdrant_data` + `embedding_cache` volumes. On by default; retrieval/embeddings wiring is still limited, so treat it as scaffolding. Remove with `--scale qdrant=0`.

- **tts** — on-box Kokoro text-to-speech for spoken advisor replies (M87a, ADR 0018); the iOS app falls back to a system voice if it's off. Remove with `--scale tts=0`.

- **prometheus + node-exporter + grafana** — the monitoring stack. Grafana dashboards are served under `/grafana/` behind the same TLS as the dashboard; Prometheus scrapes the API and host metrics. Remove the trio with `--scale prometheus=0 --scale grafana=0 --scale node-exporter=0` if you don't want it.

## Local Development

Publishes the API port, enables DEBUG logging, and bind-mounts the API source with `uvicorn --reload` so backend edits reload without a rebuild:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

## Volumes

Persistent named volumes (per `docs/specs/10-docker-spec.md`):

- `postgres_data` — PostgreSQL data.
- `import_staging` — uploaded import/document files, shared by `api` and `worker`.
- `backups` — encrypted backup archives, shared by `api` and `worker`.
- `model_cache` — vLLM model cache; `embedding_cache` — fastembed model cache.
- `qdrant_data` — Qdrant storage.
- `web_certs` — the web tier's TLS cert/key.
- `ios_ota` — published OTA iOS build (served at `/ota/`).
- `prometheus_data`, `grafana_data` — monitoring state.

The `api` and `worker` also bind-mount the host's `/mnt` (rslave) so off-box
backups can write to a mounted Synology/SMB share (M98).

## Secrets

All configuration comes from the gitignored `.env` (see `.env.example`) — nothing secret is baked into an image or the compose file. Set at minimum:

- `POSTGRES_PASSWORD` — required; the stack refuses to start without it.
- `FAMILY_CFO_BACKUP_ENCRYPTION_KEY` — required for backups (generate with `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`). Losing it makes existing backups unrecoverable.

## Not Included

The `web` container already terminates **HTTPS** (self-signed by default; mount a
real cert into `web_certs`, or front with your own proxy — see the TLS section
above), and the **monitoring** stack (prometheus/node-exporter/grafana) ships.
Automated public-CA issuance (ACME/Let's Encrypt) is intentionally left to an
external reverse proxy, and there is no dedicated backup *sidecar* — backups run
in the API/worker image and write to the `backups` volume (and, optionally,
off-box over SMB).

## Images

- `docker/api.Dockerfile` — the API and worker share this image (two entrypoints). Build context is the repo root so the `services/*` packages, `apps/api`, and `database/migrations` are all available; the repo layout is preserved under `/app` so alembic's relative migration path stays valid.
- `docker/web.Dockerfile` — multi-stage: Node builds the Angular app, nginx serves it.
