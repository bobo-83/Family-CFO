# ADR 0010: Security hardening pass and deployment tooling (M18)

## Status

Accepted.

## Context

A manual security review of the codebase before wider use surfaced several
findings, and turnkey deployment (M17) raised the bar for "an operator can stand
this up safely and know it is working." This ADR records the non-obvious
decisions; the mechanical fixes (upload caps, CSPRNG pairing secret, env-gated
docs) are self-explanatory and just implemented.

The threat model is unchanged (ADR 0006/0008): single-tenant, self-hosted, run
on a host the family controls, ideally on a trusted LAN. The hardening below
raises the floor for the case where the stack is nonetheless reachable from a
wider network, without adding operational burden a home operator can't meet.

## Decisions

### 1. AI runtime `base_url` is allowlisted, not free-form (SSRF)

`PUT /api/v1/ai/runtime` let an owner set an arbitrary `base_url` that the server
then POSTs household financial context to — a server-side request forgery and
data-exfiltration vector (cloud metadata endpoints, internal services, external
collectors).

We reject any `base_url` not in a configured allowlist
(`FAMILY_CFO_AI_ALLOWED_BASE_URLS`, defaulting to the deployment's own
`FAMILY_CFO_AI_BASE_URL`). This preserves the hostname-based default
(`http://vllm:8000`) — a blanket "block private IPs" rule would wrongly reject
the legitimate internal service — while making "point the model at an arbitrary
URL" a deliberate operator act (edit the allowlist in `.env`) rather than
something any owner session can do. Scheme is restricted to http/https.

Rejected alternative: IP-range denylisting (metadata/link-local/private). It
breaks the internal-hostname default and is a perpetual cat-and-mouse (DNS
rebinding, IPv6 forms). An allowlist is smaller, safer, and matches the
single-operator model.

### 2. Auth throttling is in-app, in-memory, best-effort

`POST /auth/sessions` had no brute-force protection. We add a per-IP and
per-account fixed-window limiter with a temporary lockout, configurable and
on by default.

It is intentionally **in-process/in-memory**: the stack runs a single `api`
container, so a shared store (Redis) would be infrastructure a home server
shouldn't need. The tradeoff — counters reset on restart and don't span
replicas — is acceptable at this scale and documented. Operators who expose the
stack should still front it with an authenticating reverse proxy (ADR 0008);
this limiter is defense in depth, not a substitute.

### 3. Deployment must be observable and testable

Turnkey deploy is only trustworthy if the operator can (a) see whether every
component is healthy and (b) prove a build+boot actually works. We add
`scripts/doctor.sh` (a read-only health report across Docker, the containers,
the API/web/DB/vLLM endpoints, disk, and GPU) and `scripts/e2e-deploy-test.sh`
(a real build + core-stack boot + login + chat smoke test + teardown).

The e2e test deliberately excludes the vLLM service: pulling a multi-GB model
and requiring a GPU is not something a CI/smoke run can assume. The AI path is
covered by the stubbed-runtime tests (M16) and by `doctor.sh` at runtime; a full
GPU-backed model boot remains an operator verification.

## Consequences

- One new `.env` knob group (`FAMILY_CFO_AI_ALLOWED_BASE_URLS`, auth-limit and
  upload-cap settings). All have safe defaults; existing deployments keep working.
- The in-memory limiter's limitations are explicit; if multi-instance API ever
  becomes a goal it must move to a shared store (revisit under a new ADR).
- `doctor.sh` / `e2e-deploy-test.sh` give operators and CI a concrete
  "is it working?" answer without hand-running curl.
