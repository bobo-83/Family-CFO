# Spec Kit

Family CFO uses specification-driven development. Implementation begins only after the relevant spec exists and is accepted.

## Required Order

1. [PRD](./01-prd.md)
2. [ADRs](./02-adrs.md)
3. [Domain Model](./03-domain-model.md)
4. [OpenAPI](./04-openapi.md)
5. [Database Schema](./05-database-schema.md)
6. [Security Model](./06-security-model.md)
7. [AI Orchestration](./07-ai-orchestration.md)
8. [Mobile Spec](./08-mobile-spec.md)
9. [Angular Dashboard Spec](./09-angular-dashboard-spec.md)
10. [Docker Spec](./10-docker-spec.md)
11. [Milestone Roadmap](./11-milestone-roadmap.md)

## Task Tracking

Implementation tasks are tracked in [Implementation Tasks](./12-implementation-tasks.md).

## Acceptance State

**Release 0.1.0** — M1–M13 implemented and verified; feature-complete backend,
dashboard, containerized deployment, and security hardening. Deferrals (iOS app,
real vLLM/OCR, OFX/QFX, vector store, and other backlog) are documented in
`docs/RELEASE-CHECKLIST.md`. See the [guides](../guides/README.md) to run it.

- M0 repository and specification baseline: accepted.
- M1 backend skeleton: accepted for implementation.
- M2 financial context and deterministic engine: implemented (cash flow and budget summary calculations are not yet exposed through an API endpoint; transaction/bill/income write APIs remain out of scope).
- M3 purchase advisor: implemented (deterministic explanation stub only; no real LLM call). Debt payoff/retirement projection is tracked as backlog, not owned by any milestone yet — see `docs/specs/12-implementation-tasks.md`.
- M4 local AI runtime: implemented (vLLM adapter only; no Ollama/llama.cpp adapters, no real vLLM deployment — tests mock the HTTP layer).
- M5 Angular dashboard: implemented (real pages for every M2–M4 API that exists; reports/transactions/imports/backups/users shipped as placeholder shells, now superseded by M11).
- M6 backend/API support: implemented for pairing sessions, paired-device revocation, and bounded deterministic chat, plus the dashboard-side pairing/device-management UI. Swift/iOS implementation remains pending a macOS Swift/Xcode environment per `AGENTS.md`.
- M7 imports and OCR: implemented (CSV import is real; PDF pipeline does real text extraction but no line-item parsing; no real OCR engine ships — deterministic test adapter only; OFX/QFX are planning-only).
- M8 reports and backups: implemented for weekly/monthly report generation (real wins/risks/unusual-spending heuristics, real goal progress, narrative explanation via the same guardrail-validated LLM/deterministic-stub pattern as the purchase advisor) and encrypted on-demand/scheduled backups (`BackupAdapter` protocol, real `PgDumpBackupAdapter` command/error-handling tested only — no PostgreSQL server in this environment — and a `SqliteFileBackupAdapter` exercising the same encrypt/retention/restore paths against a real file). No annual report (tracked as backlog); no Angular Reports/Backups page upgrade (deferred to M11); no backup-key recovery/rotation.
- M9 household setup, data management, and audit: spec accepted, implementation pending. Closes the M2-deferred write-API gap (account/transaction/bill/income CRUD, household bootstrap, membership management) and the unbuilt `audit_events` table.
- M10 conversation history: spec accepted, implementation pending. Closes the unbuilt `conversations`/`conversation_messages` gap that M4/M6 deferred to "a later milestone" that did not exist.
- M11 dashboard data entry and review UIs: implemented. The M5 Transactions/Imports/Reports/Backups placeholder shells are now real pages, the Accounts and Users pages gained create/edit/delete and member-management, and all write actions are role-gated (UI convenience; the API remains the authority). Vitest covers each page's happy path and role gating; an opt-in Playwright e2e covers login → create account → add transaction → generate report. A dashboard chat UI and a first-run setup wizard remain tracked backlog.

- M12 Docker deployment: implemented. `docker compose up -d` runs the core stack (PostgreSQL, API, worker, nginx-served dashboard); the API entrypoint waits for the DB and runs migrations; secrets come from a gitignored `.env`. vLLM and Qdrant are opt-in Compose profiles (off by default — vLLM needs a GPU, Qdrant has no consumer yet). Verified end-to-end against real PostgreSQL 17, including a backup/restore round trip (the first exercise of M8's `PgDumpBackupAdapter` on real Postgres). Reverse proxy/TLS, monitoring, and a backup sidecar remain Release-Readiness "Future Containers."

- M13 security hardening: implemented. HTTPS/TLS at the web tier (nginx on 443, HTTP→HTTPS redirect, security headers, self-signed cert bootstrap + bring-your-own), session logout and token rotation (`DELETE`/`POST /api/v1/auth/sessions[/refresh]`), configurable session TTL, a consolidated security test suite (authz matrix, redaction, no-telemetry), and CI hardening (gitleaks secret scanning, pip-audit dependency audit, and the service/web suites + client-drift check wired into CI). ADR 0008 resolves the four threat-model open questions (DB encryption, cert provisioning, backup-key recovery, external-AI opt-in). Verified end-to-end over TLS. Reverse proxy/monitoring/rate-limiting remain future Release-Readiness work.

- M14 debt payoff and retirement projections: implemented. Persists per-account debt terms (`accounts.annual_interest_rate`/`minimum_payment_minor`), makes the purchase advisor's `debt` impact real via `calculate_debt_payoff` (replacing the warning-only placeholder), adds `calculate_retirement_projection`, and a `POST /api/v1/advisor/retirement` scenario endpoint returning a grounded recommendation. Closes most of the M3-deferred debt/retirement backlog; an open-ended scenario-planning API remains backlog.

A post-M8 spec-kit audit surfaced M9–M11 (write APIs, audit log, conversation history, dashboard shell upgrades) as promised-but-unowned work, plus the deferred follow-ups and vector-store/retrieval work now tracked in `docs/specs/12-implementation-tasks.md`. All are documented before implementation, per the spec-driven rule above.

Before coding a milestone, update the relevant documents with:

- Scope
- Non-goals
- API behavior
- Data model changes
- Security impact
- Test expectations
- Documentation impact
