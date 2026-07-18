# Security Hardening Guide

How Family CFO protects your data and what you, the operator, are responsible
for. The decisions behind these controls are recorded in
[ADR 0008](../adr/0008-security-hardening-decisions.md); the model is in
[the threat model](../security/threat-model.md) and
[security spec](../specs/06-security-model.md).

## Transport (TLS)

The `web` container terminates HTTPS on 443, redirects HTTP→HTTPS, and sets
security headers (HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options:
DENY`, `Referrer-Policy`, a `Content-Security-Policy`).

- **Default:** a self-signed certificate generated on first start (browser
  warning expected).
- **Real certificate:** mount your cert/key as `tls.crt` / `tls.key` into the
  `web_certs` volume (`/etc/nginx/certs`), **or** front the stack with your own
  TLS reverse proxy (Caddy/Traefik/nginx) terminating a publicly-trusted cert.
- Automated public-CA issuance (ACME/Let's Encrypt) is intentionally not built
  in; use an external proxy for that.

## Authentication and sessions

- Local email/password auth; passwords hashed with PBKDF2-SHA256. There is no
  public sign-up — the owner creates members.
- Bearer tokens are opaque and stored **hashed** at rest. TTL is configurable
  (`FAMILY_CFO_SESSION_TTL_HOURS`, default 12) and expiry is enforced.
- **Log out:** `DELETE /api/v1/auth/sessions` revokes the current token.
- **Rotate:** `POST /api/v1/auth/sessions/refresh` issues a new token and
  invalidates the old one.
- **Device revocation:** paired mobile devices can be revoked by the owner,
  which also kills that device's sessions.
- **Brute-force limiter:** `POST /api/v1/auth/sessions` is throttled per-IP and
  per-account with a temporary lockout (ADR 0010). It is in-memory/single-
  instance (counters reset on restart, don't span replicas) — defense in depth,
  not a substitute for fronting an internet-exposed deployment with an
  authenticating proxy. Tune with `FAMILY_CFO_AUTH_RATE_LIMIT_*`.
- **Pairing secret:** a pairing session id is a CSPRNG token (not a uuid),
  single-use, and short-lived; it is the QR-borne bearer secret for the
  otherwise-unauthenticated `POST /api/v1/pairing/confirm`.

## AI runtime (SSRF guard)

The server sends household context to the configured AI runtime, so the
`base_url` a household may set (`PUT /api/v1/ai/runtime`) is restricted to an
allowlist (`FAMILY_CFO_AI_ALLOWED_BASE_URLS`, default = the deployment's
`FAMILY_CFO_AI_BASE_URL`). Pointing the model at a new URL is a deliberate
operator edit, not something an owner session can do — this closes an SSRF /
data-exfiltration path (ADR 0010).

## Uploads and API surface

- **Upload caps:** import/document uploads are bounded at both nginx
  (`client_max_body_size`) and the API (`FAMILY_CFO_MAX_UPLOAD_BYTES`, default
  10 MB) to prevent memory-exhaustion DoS. Raise both together if needed.
- **Docs in production:** set `FAMILY_CFO_ENV=production` to disable the Swagger
  UI and `openapi.json` so the API surface isn't published.

## Authorization (roles)

`owner` > `adult` > `viewer` > `child`. Household-data writes (accounts,
transactions, bills, income, goals, imports) require `owner`/`adult`;
administrative actions (member management, backups, audit log, AI runtime
config) require `owner`. The API is the authority — the dashboard's gating is
convenience only.

## Auditing

Every sensitive mutation writes a non-sensitive `audit_events` row (actor,
action, entity, summary — never amounts, passwords, or tokens). Read it via
`GET /api/v1/audit` (owner) or the dashboard.

## Data at rest

- **Database:** not encrypted at the application layer by design — put the
  PostgreSQL data volume on an encrypted disk (e.g. LUKS) for at-rest
  protection (ADR 0008).
- **Backups:** always encrypted at the application layer with your Fernet key —
  see [Backup and Restore](./backup-and-restore.md).
- **Documents/imports:** stored on the `import_staging` volume; protect it the
  same way as the database volume.

## Secrets

- All configuration/secrets come from `.env` (gitignored). Only `.env.example`
  (placeholders) is committed. Nothing secret is baked into an image.
- CI runs gitleaks (secret scanning) and pip-audit (dependency vulnerabilities).
- No personal identifiers or environment specifics in the repo (Apple team ids,
  real private IPs, home paths) — enforced by `scripts/check-repo-hygiene.sh` in
  CI and a pre-commit hook (ADR 0030). List your own literals to block locally in
  a gitignored `.repo-hygiene-deny`.

## Logging and privacy

- Structured logs pass through a redaction filter that scrubs
  password/token/secret/credential values.
- Raw financial documents, credentials, and AI prompts/completions are never
  logged or persisted; only guardrail-validated explanation text is stored.
- No telemetry, analytics, or ads — asserted by a test that scans first-party
  source for any analytics SDK.

## AI privacy

The default and only supported runtime is **local** (vLLM behind the M4
adapter). Household financial context is never sent to an external/cloud AI
provider; that would require a future explicit opt-in ADR superseding ADR 0008.

## Operator responsibilities checklist

- [ ] Set a strong `POSTGRES_PASSWORD` and a unique `FAMILY_CFO_BACKUP_ENCRYPTION_KEY`, stored in a secret manager.
- [ ] Install a real TLS certificate or front with a TLS proxy before exposing beyond localhost.
- [ ] Put the database and staging volumes on an encrypted disk.
- [ ] Keep the host and Docker images updated.
- [ ] Take (and periodically test) backups; keep the encryption key safe and separate.
- [ ] Do not expose the API, database, or vLLM ports publicly — only the dashboard, behind TLS.
