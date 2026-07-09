# Home-Server Deployment Guide

Family CFO runs as a Docker Compose stack on a machine you control. This guide
takes you from a clean checkout to a running, TLS-served dashboard.

## Quick start: one-command deploy

`scripts/deploy.sh` stands the whole stack up (dashboard + API + worker + DB +
vLLM) on a **local** or **remote** host and prints the dashboard URL. It
generates a `.env` with random secrets on first run.

```bash
scripts/deploy.sh                 # interactive: choose local or remote (SSH)
TARGET=local scripts/deploy.sh    # non-interactive local
TARGET=remote SSH_HOST=my-box SSH_USER=me scripts/deploy.sh
```

For a remote host it prompts for SSH host/user/port/key, verifies Docker (and
the NVIDIA Container Toolkit, since the AI runtime is on by default), rsyncs the
repo, and runs Compose there. The manual steps below are the same thing done by
hand, plus the configuration reference.

## Prerequisites

- Docker Engine 24+ and the Compose plugin (`docker compose version`).
- A host you trust on your local network. Family CFO is single-tenant and
  self-hosted by design (ADR 0006); it is not built to be exposed raw to the
  public internet — see [Security](./security.md).

## 1. Configure

```bash
git clone <your-fork-or-clone-url> Family-CFO
cd Family-CFO
cp .env.example .env
```

Edit `.env` and set, at minimum:

- `POSTGRES_PASSWORD` — a strong password. The stack refuses to start without it.
- `FAMILY_CFO_BACKUP_ENCRYPTION_KEY` — required before you can take backups.
  Generate one:

  ```bash
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

  Store this key in your own secret manager. **Losing it makes existing backups
  permanently unrecoverable** (ADR 0008).

Optional: `WEB_TLS_PORT` (default 8443), `WEB_PORT` (default 8080, HTTP→HTTPS
redirect), `FAMILY_CFO_SESSION_TTL_HOURS` (default 12).

## 2. Start

```bash
docker compose up -d
```

This builds and starts the core stack: PostgreSQL, the API (which runs database
migrations on startup), the background worker, and the nginx-served dashboard.
Wait for the API to become healthy:

```bash
docker compose ps
```

The dashboard is at **`https://localhost:8443`** (or your `WEB_TLS_PORT`). On
first start the web container generates a self-signed certificate, so your
browser will warn about it — expected. See [Security](./security.md) to install
a real certificate.

## 3. Create your household (first run)

There is no public sign-up. Create the first household and its owner with one
call (replace the values):

```bash
curl -sk -X POST https://localhost:8443/api/v1/households \
  -H 'content-type: application/json' \
  -d '{
    "display_name": "Our Household",
    "base_currency": "USD",
    "owner_email": "you@example.com",
    "owner_password": "choose-a-strong-password",
    "owner_display_name": "Your Name"
  }'
```

Then log in at the dashboard with that email and password. From **Users &
Devices** you can add adult/viewer/child members; from **Accounts**,
**Transactions**, **Bills**, and **Income** you can enter your financial data
(or import a CSV from **Imports**).

## 4. AI runtime and optional services

The local vLLM AI runtime is **on by default** (M17) — `docker compose up -d`
already started it, and every household uses it automatically. It needs a
GPU-capable host with the NVIDIA Container Toolkit. To run **without** AI (no
GPU), set `FAMILY_CFO_AI_ENABLED=false` in `.env` and start with:

```bash
docker compose up -d --scale vllm=0      # no AI; deterministic answers only
```

The vector store stays off (no consumer yet — scaffolding):

```bash
docker compose --profile vector up -d    # Qdrant
```

For choosing/swapping the model and confirming the agentic advisor engaged, see
the [AI Advisor guide](./ai-advisor.md).

## 5. Updates

The fast path — patch only the app containers, leaving the AI model and database
untouched:

```bash
git pull
scripts/patch.sh                 # rebuild api + worker + web
scripts/patch.sh web             # or just one service
TARGET=remote SSH_HOST=box scripts/patch.sh   # patch a remote host over SSH
```

`patch.sh` never rebuilds `vllm` or `db` and never removes a volume, so the
multi-GB model in `model_cache` is **not** re-downloaded. The full
`docker compose up -d --build` still works if you want to rebuild everything.

The API applies any new migrations on startup (so a schema change ships with an
`api` patch). Migrations are additive; a rollback path is
`docker compose run --rm api python -m alembic -c alembic.ini downgrade <rev>`.

## Operating the stack

- Health: `scripts/doctor.sh` — a read-only report on containers, the API/DB/
  web/vLLM endpoints, disk, and GPU. Run it any time to answer "is it working?".
- Smoke test a build: `scripts/e2e-deploy-test.sh` — builds images and boots an
  isolated core stack (no vLLM), logs in, exercises chat, and tears down.
- Logs: `docker compose logs -f api` (or `worker`, `web`, `db`).
- Stop: `docker compose down` (keeps data) / `docker compose down -v` (**deletes
  all data volumes** — only for a full reset).
- Data lives in named volumes: `postgres_data`, `import_staging`, `backups`.
  Back these up at the volume level in addition to the app's own encrypted
  backups (see [Backup and Restore](./backup-and-restore.md)).

See [Troubleshooting](./troubleshooting.md) if the stack doesn't come up.
