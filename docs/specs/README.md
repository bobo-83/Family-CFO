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

- M0 repository and specification baseline: accepted.
- M1 backend skeleton: accepted for implementation.
- M2 financial context and deterministic engine: implemented (cash flow and budget summary calculations are not yet exposed through an API endpoint; transaction/bill/income write APIs remain out of scope).
- M3 purchase advisor: implemented (deterministic explanation stub only; no real LLM call). Debt payoff/retirement projection is tracked as backlog, not owned by any milestone yet — see `docs/specs/12-implementation-tasks.md`.
- M4 local AI runtime: implemented (vLLM adapter only; no Ollama/llama.cpp adapters, no real vLLM deployment — tests mock the HTTP layer).
- M5 Angular dashboard: implemented (real pages for every M2–M4 API that exists; reports/transactions/imports/backups/users are placeholder shells pending M6/M7/M8).
- M6 backend/API support: implemented for pairing sessions, paired-device revocation, and bounded deterministic chat, plus the dashboard-side pairing/device-management UI. Swift/iOS implementation remains pending a macOS Swift/Xcode environment per `AGENTS.md`.
- M7 imports and OCR: implemented (CSV import is real; PDF pipeline does real text extraction but no line-item parsing; no real OCR engine ships — deterministic test adapter only; OFX/QFX are planning-only).

Before coding a milestone, update the relevant documents with:

- Scope
- Non-goals
- API behavior
- Data model changes
- Security impact
- Test expectations
- Documentation impact
