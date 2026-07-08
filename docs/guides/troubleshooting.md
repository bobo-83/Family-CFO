# Troubleshooting Guide

Common problems bringing up or operating the stack, and how to diagnose them.

## The stack won't start

**`docker compose up` errors: `POSTGRES_PASSWORD` variable is not set`**
You have no `.env` or didn't set the password. `cp .env.example .env` and set
`POSTGRES_PASSWORD`.

**`api` container is unhealthy / restarts**
Check its logs:
```bash
docker compose logs api | tail -50
```
Usual causes:
- **Migration error** — the API runs `alembic upgrade head` on start. A
  migration failure stops it. Read the traceback; if it mentions
  `value too long for type character varying(32)`, a migration revision id
  exceeds Postgres's `alembic_version` width (revision ids must be ≤ 32 chars).
- **Can't reach the database** — the entrypoint waits for Postgres; if `db`
  never becomes healthy, check `docker compose logs db`.

**`web` container exits immediately**
`docker compose logs web`. If it complains about the `api` upstream, that's only
fatal on older configs — the shipped nginx config resolves `api` at request
time, so `web` should start regardless. A cert-generation failure would also
show here.

## Can't log in / 401 everywhere

- The demo fixtures are **not** seeded in a real deployment. Create your
  household first (see [Deployment](./deployment.md) step 3).
- A `401` after a while is a normal expired session — log in again, or use
  `POST /api/v1/auth/sessions/refresh` before it expires. TTL is
  `FAMILY_CFO_SESSION_TTL_HOURS`.
- After `DELETE /api/v1/auth/sessions` (logout) or a device revocation, the old
  token is intentionally `401`.

## Browser warns about the certificate

Expected on first run — the web container self-signs a certificate. Install a
real cert or front with a TLS proxy (see [Security](./security.md)).

## Backups fail

- `FAMILY_CFO_BACKUP_ENCRYPTION_KEY` is unset — backups never write plaintext,
  so they fail without it. Set it (see [Backup and Restore](./backup-and-restore.md)).
- **Restore fails on a version-specific setting** (e.g. `unrecognized
  configuration parameter`) — the `pg_dump`/`pg_restore` client and the
  PostgreSQL server majors differ. Keep them in step (the shipped compose pins
  postgres:17 to match the client).

## Imports never move past `pending`

Imports are processed by the **worker**, not the API request. Confirm the worker
is up (`docker compose ps`) and check `docker compose logs worker` — it polls
every 30 seconds. A file that fails 3 times becomes `failed` with a
non-sensitive `error_message`.

## AI explanations look "deterministic," not model-generated

That's the fallback. The purchase advisor and reports use the deterministic
explanation stub unless a household has an **enabled** AI runtime config
pointing at a reachable vLLM (start it with `docker compose --profile ai up -d`
and set it on the AI Runtime page). The system also falls back whenever a model
response fails guardrail validation — by design.

## Reports/backups pages say I can't do something

Role gating: report generation and imports need `owner`/`adult`; backups, member
management, and the audit log need `owner`. Check your member's role under
**Users & Devices**.

## Getting more detail

- Turn up logging: set `FAMILY_CFO_LOG_LEVEL=DEBUG` in `.env` and restart, or use
  the dev override (`docker-compose.dev.yml`).
- Inspect the database directly:
  ```bash
  docker compose exec db psql -U family_cfo -d family_cfo
  ```
- Health: `curl -sk https://localhost:8443/api/v1/health`.

If a problem looks like a bug rather than a configuration issue, capture the
relevant `docker compose logs` (they contain no secrets — redaction is enforced)
and open an issue.
