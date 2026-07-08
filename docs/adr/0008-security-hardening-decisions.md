# ADR 0008: Security Hardening Decisions

Status: Accepted

## Context

The threat model (`docs/security/threat-model.md`) carried four open questions
through M0–M12. M13 (Security Hardening) needs them resolved so the controls it
ships rest on recorded decisions rather than TBDs:

1. Exact database encryption approach.
2. Local certificate provisioning strategy.
3. Backup key recovery flow.
4. Whether optional external AI providers will ever be supported through explicit opt-in.

The product is a single-tenant, self-hosted home-server deployment on a trusted
local network (ADR 0006), not a multi-tenant cloud service. The decisions below
follow from that.

## Decision

### 1. Database encryption at rest — host/volume responsibility, not app-layer

At-rest encryption is delegated to the host: an encrypted volume/disk (e.g.
LUKS) under the PostgreSQL data directory. The application does **not** encrypt
individual columns.

Rejected: per-column application encryption. It would break the deterministic
money model (integer minor units must be queryable/aggregatable), the audit
requirements (calculation inputs must be inspectable), and every financial
calculation that reads those columns — for marginal benefit on a single-tenant
box where an attacker with database-file access has likely already compromised
the host. Backups are separately encrypted at the application layer (M8), which
is where portable, off-host data actually needs protecting.

### 2. Certificate provisioning — self-signed default, bring-your-own, external proxy

The `web` container generates a self-signed certificate on first start if none
is mounted, so `docker compose up -d` yields working TLS immediately (with the
expected first-run browser warning). Operators override it by mounting a real
cert/key at `/etc/nginx/certs`, or by fronting the stack with their own TLS
reverse proxy (Caddy/Traefik/nginx) that terminates a publicly-trusted
certificate.

Rejected as a default: built-in ACME/Let's Encrypt automation. A home server on
a LAN or behind a dynamic IP / CGNAT often cannot complete an ACME challenge,
so baking certbot in would fail for a large fraction of deployments. Automated
public-CA issuance remains available to operators through an external proxy and
is tracked as future work, not a v1 default.

### 3. Backup key recovery — operator-managed, no recovery by design

`FAMILY_CFO_BACKUP_ENCRYPTION_KEY` is operator-managed and has **no recovery
mechanism**: losing it makes existing backups permanently unrecoverable. This is
the intended security property — a key-escrow or recovery flow would be a second
copy of the key to steal. Operators are told (in `.env.example`,
`database/README.md`, and the backup docs) to store the key in their own secret
manager. Rotating the key only affects backups taken after the rotation.

### 4. External AI providers — local-only default, opt-in only via a future ADR

The default and only supported runtime remains local (vLLM behind the M4
adapter, ADR 0004). Sending household financial context to an external/cloud AI
provider is **not** supported and will only ever be enabled through a future
explicit opt-in ADR that spells out the data-egress consequences. Until then the
AI runtime config accepts only local/self-hosted endpoints and the app makes no
outbound AI calls unless an operator configures and enables a runtime.

## Consequences

- M13 ships TLS termination, a self-signed default, and bring-your-own-cert
  support; ACME automation is explicitly out of scope.
- No app-layer database encryption is built; deployment docs call out the
  encrypted-volume expectation for at-rest protection.
- The "no backup key recovery" property is documented as intentional, closing
  the question rather than implying a feature is missing.
- The local-only AI stance is now a recorded constraint; any future cloud
  opt-in must supersede this ADR with its own.
- `docs/security/threat-model.md`'s Open Questions section is replaced by a
  pointer to this ADR.
