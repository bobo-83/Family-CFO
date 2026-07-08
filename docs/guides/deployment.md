# Home-Server Deployment Guide

Family CFO runs as a Docker Compose stack on a machine you control. This guide
takes you from a clean checkout to a running, TLS-served dashboard.

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

## 4. Optional services

Off by default; enable per your hardware and needs:

```bash
docker compose --profile ai up -d        # local vLLM runtime (needs a GPU)
docker compose --profile vector up -d    # Qdrant (no consumer yet — scaffolding)
```

For the AI runtime, point a household's config at `http://vllm:8000` from the
dashboard's **AI Runtime** page. Without it, the purchase advisor and reports
use the deterministic explanation stub.

## 5. Updates

```bash
git pull
docker compose up -d --build
```

The API applies any new migrations on startup. Migrations are additive; a
rollback path is `docker compose run --rm api python -m alembic -c alembic.ini downgrade <rev>`.

## Operating the stack

- Logs: `docker compose logs -f api` (or `worker`, `web`, `db`).
- Stop: `docker compose down` (keeps data) / `docker compose down -v` (**deletes
  all data volumes** — only for a full reset).
- Data lives in named volumes: `postgres_data`, `import_staging`, `backups`.
  Back these up at the volume level in addition to the app's own encrypted
  backups (see [Backup and Restore](./backup-and-restore.md)).

See [Troubleshooting](./troubleshooting.md) if the stack doesn't come up.
