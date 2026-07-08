# Implementation Tasks

This is the project task checklist. Keep it in milestone order and check items off as they are completed.

Rules:

- Do not start implementation tasks for a milestone until its spec gate is complete.
- Keep OpenAPI as the source of truth for backend clients.
- Use synthetic fixtures only.
- Update tests and documentation with implementation changes.
- Add or supersede ADRs when architecture, storage, security, runtime, or cross-component decisions change.

## Status Legend

- `[ ]` Not started
- `[x]` Complete

## M0: Repository and Specification Baseline

### Repository Baseline

- [x] Create monorepo directory scaffold.
- [x] Add component README files for apps, services, shared assets, database, Docker, and docs.
- [x] Initialize Git repository.
- [x] Add root README with product vision, architecture principles, and planned stack.
- [x] Add agent and development workflow documentation.
- [x] Add sensitive-data guardrails.

### Spec Kit Baseline

- [x] Draft master project brief.
- [x] Draft PRD.
- [x] Draft ADR index.
- [x] Add initial accepted ADRs.
- [x] Draft domain model.
- [x] Draft OpenAPI spec narrative.
- [x] Draft source-of-truth OpenAPI contract.
- [x] Draft database schema spec.
- [x] Draft security model.
- [x] Draft threat model.
- [x] Draft AI orchestration spec.
- [x] Draft mobile spec.
- [x] Draft Angular dashboard spec.
- [x] Draft Docker spec.
- [x] Draft milestone roadmap.
- [x] Add CI check for required spec files.
- [x] Review and accept the complete Spec Kit.
- [x] Record accepted Spec Kit state in the spec index.

## M1: Backend Skeleton

### Spec Gate

- [x] Define M1 scope.
- [x] Define M1 non-goals.
- [x] Confirm health endpoint behavior in OpenAPI.
- [x] Define database connection behavior for development and tests.
- [x] Define migration tooling expectations.
- [x] Define security impact for unauthenticated health checks and local development configuration.
- [x] Define M1 unit and integration test expectations.
- [x] Define M1 documentation updates.

### Implementation

- [x] Add Python project metadata for the API app.
- [x] Add FastAPI application entry point.
- [x] Add `/api/v1` route prefix.
- [x] Implement `GET /api/v1/health`.
- [x] Return health response matching `HealthResponse` in OpenAPI.
- [x] Add structured error response foundation.
- [x] Add application configuration loading.
- [x] Add logging configuration with sensitive-data redaction hooks.
- [x] Add PostgreSQL connection settings.
- [x] Add database connectivity check.
- [x] Add migration tooling.
- [x] Add initial empty migration baseline.
- [x] Add pytest test harness.
- [x] Add unit tests for health response shape.
- [x] Add integration test for `GET /api/v1/health`.
- [x] Add OpenAPI generation command.
- [x] Add contract drift check against `shared/openapi/family-cfo.v1.yaml`.
- [x] Add API lint, format, and test commands.
- [x] Update API README with run, test, and migration commands.
- [x] Update development workflow docs if commands change.
- [x] Run verification commands.
- [x] Commit M1 backend skeleton changes.

## M2: Financial Context and Deterministic Engine

### Spec Gate

- [x] Define M2 scope.
- [x] Define M2 non-goals.
- [x] Expand domain model for households, users, accounts, balances, transactions, bills, income, goals, and scenarios.
- [x] Update OpenAPI for any M2 endpoints not already covered.
- [x] Define database tables, indexes, constraints, and relationship rules for M2 entities.
- [x] Define role and authorization expectations for financial context endpoints.
- [x] Define audit requirements for deterministic calculations.
- [x] Define money precision and currency handling rules.
- [x] Define M2 unit and integration test expectations.
- [x] Define M2 documentation updates.

### Data Model and Persistence

- [x] Create migrations for households.
- [x] Create migrations for users.
- [x] Create migrations for household memberships.
- [x] Create migrations for auth sessions.
- [x] Create migrations for accounts.
- [x] Create migrations for account balances.
- [x] Create migrations for transactions.
- [x] Create migrations for transaction categories.
- [x] Create migrations for bills.
- [x] Create migrations for income sources.
- [x] Create migrations for goals.
- [x] Create migrations for scenarios.
- [x] Create migrations for financial calculations.
- [x] Add database constraints that prevent floating-point persisted money.
- [x] Add synthetic database fixtures.
- [x] Add migration rollback tests where practical.

### Backend APIs

- [x] Implement local authentication foundation needed for protected routes.
- [x] Implement household context read API.
- [x] Implement account list API.
- [x] Implement goal list API.
- [x] Implement goal create API.
- [x] Add transaction APIs if accepted into M2 OpenAPI scope. (Read-only list API; create/update/delete remain out of scope per the M2 spec gate.)
- [x] Add bill APIs if accepted into M2 OpenAPI scope. (Read-only list API.)
- [x] Add income APIs if accepted into M2 OpenAPI scope. (Read-only list API.)
- [x] Add repository tests for financial context persistence.
- [x] Add API integration tests for protected financial context routes.

### Financial Engine

- [x] Add deterministic financial engine package or service boundary.
- [x] Add money value type using integer minor units and explicit currency.
- [x] Add calculation result contract with inputs, assumptions, version, warnings, and outputs.
- [x] Implement net worth calculation.
- [x] Implement cash flow calculation.
- [x] Implement budget summary calculation.
- [x] Implement emergency fund months calculation.
- [x] Implement savings goal progress calculation.
- [x] Add unit tests for money precision.
- [x] Add unit tests for currency mismatch handling.
- [x] Add unit tests for net worth calculation.
- [x] Add unit tests for cash flow calculation.
- [x] Add unit tests for budget calculation.
- [x] Add unit tests for emergency fund calculation.
- [x] Add unit tests for goal progress calculation.
- [x] Persist calculation audit records.
- [x] Document financial engine contracts and limitations.
- [x] Run verification commands.
- [x] Commit M2 financial context and engine changes. (`1490f67`)

## M3: Purchase Advisor

### Spec Gate

- [x] Define M3 scope.
- [x] Define M3 non-goals.
- [x] Confirm purchase advisor request and recommendation response in OpenAPI.
- [x] Define scenario input persistence.
- [x] Define deterministic purchase impact calculations.
- [x] Define LLM explanation adapter stub behavior.
- [x] Define security impact for recommendation data and prompts.
- [x] Define M3 unit and integration test expectations.
- [x] Define M3 documentation updates.

### Implementation

- [x] Create scenario persistence model and migration. (`scenarios` table added in M2; `recommendations` table added in M3 migration `0015`.)
- [x] Implement purchase scenario input validation. (Non-positive price and currency mismatch return `400`.)
- [x] Implement purchase impact calculation using the financial engine.
- [x] Calculate discretionary cash flow impact.
- [x] Calculate emergency fund impact.
- [x] Calculate debt payoff impact where data exists. (No interest/payment data exists in the M2 schema, so this surfaces as a documented warning rather than a fabricated number — see the M3 Non-Goals and "Backlog: Debt Payoff and Retirement Projections" below.)
- [x] Calculate savings goal impact where data exists. (Top-priority goal only.)
- [x] Calculate net worth impact.
- [x] Generate recommendation response with answer, assumptions, impacts, tradeoffs, alternatives, confidence, warnings, and calculation references.
- [x] Add LLM explanation adapter interface.
- [x] Add deterministic no-model explanation stub.
- [x] Ensure numeric recommendation claims cite calculation references.
- [x] Persist recommendation records.
- [x] Implement `POST /api/v1/advisor/purchase`.
- [x] Add unit tests for purchase impact calculations.
- [x] Add unit tests for recommendation response structure.
- [x] Add integration tests for purchase advisor API.
- [x] Add prompt and response redaction tests for logged data.
- [x] Update API README and financial engine docs.
- [x] Run verification commands.
- [x] Commit M3 purchase advisor changes. (`dc356fa`)

## M4: Local AI Runtime

### Spec Gate

- [x] Define M4 scope.
- [x] Define M4 non-goals.
- [x] Define AI runtime adapter interface.
- [x] Define vLLM configuration requirements.
- [x] Define model and prompt version tracking.
- [x] Define guardrail behavior for missing calculation references and hallucinated financial facts.
- [x] Define data retention expectations for prompts and model responses.
- [x] Define M4 unit and integration test expectations.
- [x] Define M4 documentation updates.

### Implementation

- [x] Create AI orchestrator package or service boundary.
- [x] Add AI runtime adapter interface.
- [x] Add vLLM adapter behind the runtime interface.
- [x] Add OpenAI-compatible request and response mapping.
- [x] Add runtime timeout and retry policy.
- [x] Add runtime configuration persistence.
- [x] Implement `GET /api/v1/ai/runtime`.
- [x] Implement `PUT /api/v1/ai/runtime`.
- [x] Track model version with recommendation records.
- [x] Track prompt version with recommendation records.
- [x] Add prompt template versioning.
- [x] Add guardrail that rejects numeric claims without calculation references. (String-based unattributed-number check; see `services/ai-orchestrator/README.md` for its limitations.)
- [x] Add guardrail that exposes missing information instead of inventing facts. (Falls back to the deterministic stub — which only states facts already computed — rather than an unvalidated model claim.)
- [x] Add adapter contract tests.
- [x] Add vLLM adapter tests with mocked runtime responses.
- [x] Add recommendation guardrail tests.
- [x] Document supported runtime configuration.
- [x] Update threat model if prompt retention or runtime exposure changes.
- [x] Run verification commands.
- [x] Commit M4 local AI runtime changes. (`01d6661`)

## M5: Angular Dashboard

### Spec Gate

- [x] Define M5 scope.
- [x] Define M5 non-goals.
- [x] Define onboarding flow behavior.
- [x] Define dashboard information architecture for M5.
- [x] Define generated Angular client workflow.
- [x] Define browser-side security expectations.
- [x] Define M5 unit and integration test expectations.
- [x] Define M5 documentation updates.

### Implementation

- [x] Add Angular project scaffold under `apps/web`. (Standalone components, signals, zoneless, Vitest — Angular 22 CLI defaults.)
- [x] Add generated API client workflow from OpenAPI. (`@hey-api/openapi-ts`, not `openapi-generator-cli` — this sandbox's Java 8 is too old for the latter.)
- [x] Add dashboard app shell and routing.
- [x] Add local authentication/session UI.
- [x] Add onboarding flow. (Login only — no signup API exists.)
- [x] Add overview page.
- [x] Add accounts page.
- [x] Add goals page.
- [x] Add reports shell.
- [x] Add transaction review shell.
- [x] Add import review shell.
- [x] Add AI runtime settings page.
- [x] Add backup management shell.
- [x] Add user management shell.
- [x] Add paired device revocation UI. (Placeholder inside the Users shell; no pairing API until M6.)
- [x] Add error and loading states.
- [x] Add form validation for supported M5 workflows.
- [x] Add unit tests for dashboard components. (21 Vitest tests; components depend on an injectable `ApiService` so tests use `TestBed` DI mocking, since Angular's Vitest integration blocks `vi.mock()` on relative imports.)
- [x] Add integration tests for generated client usage. (Covered by the Playwright e2e test against a real backend, not a mocked-`fetch` unit test — see Test Expectations for why.)
- [x] Add end-to-end smoke test for onboarding and health connectivity. (`e2e/onboarding.spec.ts`, 3 Playwright tests, verified passing against a real backend.)
- [x] Update web README with development commands.
- [x] Run verification commands.
- [x] Commit M5 Angular dashboard changes. (`eb71999`)

## M6: iPhone App

### Spec Gate

- [x] Define M6 scope. (See `docs/specs/11-milestone-roadmap.md`.)
- [x] Define M6 non-goals. (See `docs/specs/11-milestone-roadmap.md`.)
- [x] Define pairing flow details. (See `docs/specs/11-milestone-roadmap.md`.)
- [x] Define generated Swift client workflow. (See `docs/specs/11-milestone-roadmap.md`.)
- [x] Define secure credential storage expectations. (See `docs/specs/11-milestone-roadmap.md`.)
- [x] Define Face ID local unlock behavior. (See `docs/specs/11-milestone-roadmap.md`.)
- [x] Define camera capture and structured image output rules. (See `docs/specs/11-milestone-roadmap.md`.)
- [x] Define M6 unit and integration test expectations. (See `docs/specs/11-milestone-roadmap.md`.)
- [x] Define M6 documentation updates. (See `docs/specs/11-milestone-roadmap.md`.)

### Implementation

#### Backend API Support (Linux-safe)

- [x] Create migrations for pairing sessions and paired devices.
- [x] Add nullable `auth_sessions.device_id` for device-backed session revocation.
- [x] Implement `POST /api/v1/pairing/sessions`.
- [x] Implement `POST /api/v1/pairing/confirm`.
- [x] Implement `GET /api/v1/pairing/devices`.
- [x] Implement `DELETE /api/v1/pairing/devices/{device_id}`.
- [x] Implement bounded deterministic `POST /api/v1/chat/messages`.
- [x] Update OpenAPI for M6 pairing, paired-device, and chat behavior.
- [x] Regenerate the Angular client from OpenAPI.
- [x] Add repository tests for pairing lifecycle and paired-device revocation.
- [x] Add API integration tests for pairing, paired-device revocation, and chat.
- [x] Update API and database documentation for M6 backend support.
- [x] Run backend verification commands for M6 backend support.

#### Dashboard Integration (Linux-safe)

- [x] Add `createPairingSession`, `listPairedDevices`, `revokePairedDevice` to `ApiService`.
- [x] Add a client-side QR code renderer for the pairing `qr_payload`.
- [x] Implement the "Pair a device" flow on the Users & Devices page (owner/adult), showing the QR code, raw payload, and expiration.
- [x] Implement the paired-device list and revoke action (owner only) on the Users & Devices page.
- [x] Add unit tests for the pairing and device-revocation flows, including the role-restricted paths.
- [x] Update `apps/web/README.md` with the pairing/device-management dashboard behavior.
- [x] Run frontend verification commands for the dashboard integration slice. (Build, 26 Vitest unit tests, and a live end-to-end smoke test against a real backend — session creation, QR rendering, simulated mobile confirmation, list, and revoke all verified.)
- [ ] Commit M6 dashboard integration changes.

#### iPhone App Implementation (requires macOS)

- [ ] Add SwiftUI project scaffold under `apps/ios`.
- [ ] Add generated Swift API client workflow from OpenAPI.
- [ ] Add app navigation shell.
- [ ] Implement QR pairing scan flow.
- [ ] Implement server identity and household confirmation screen.
- [ ] Implement pairing confirmation API call.
- [ ] Store device credential securely.
- [ ] Implement token refresh or re-auth flow if accepted into the security model.
- [ ] Implement Face ID local unlock.
- [ ] Implement chat UI.
- [ ] Implement chat API integration.
- [ ] Implement camera capture UI.
- [ ] Implement receipt capture flow.
- [ ] Implement store item capture flow.
- [ ] Implement Vision-based structured JSON extraction where available.
- [ ] Send structured image output to the server when possible.
- [ ] Keep photos on device when structured extraction is sufficient.
- [ ] Add paired-device revocation handling.
- [ ] Add unit tests for client request mapping.
- [ ] Add UI tests for pairing, unlock, chat, and capture flows.
- [ ] Update iOS README with development and testing commands.
- [ ] Run verification commands.
- [ ] Commit M6 iPhone app changes.

## M7: Imports and OCR

### Spec Gate

- [x] Define M7 scope.
- [x] Define M7 non-goals.
- [x] Define import job lifecycle.
- [x] Define CSV import schema and mapping behavior.
- [x] Define PDF pipeline behavior.
- [x] Define OFX and QFX planning scope.
- [x] Define OCR adapter interface.
- [x] Define review queue behavior before imported data affects financial state.
- [x] Define worker scheduling expectations.
- [x] Define security impact for document storage, extraction, and logs.
- [x] Define M7 unit and integration test expectations.
- [x] Define M7 documentation updates.

### Implementation

- [x] Create migrations for imports. (`0019`)
- [x] Create migrations for import files. (`0020`)
- [x] Create migrations for documents. (`0021`, includes `import_id` for PDF-import-linked documents)
- [x] Create migrations for document extractions. (`0022`)
- [x] Add import staging storage. (Local disk under `FAMILY_CFO_IMPORT_STAGING_DIR`; also added `transactions.import_id`/`possible_duplicate` via `0023`, an additive migration not a rewrite of M2's original `transactions` migration.)
- [x] Implement `GET /api/v1/imports`.
- [x] Implement `POST /api/v1/imports`.
- [x] Add CSV parser with synthetic fixtures.
- [x] Add CSV mapping and validation.
- [x] Add CSV import preview. (No separate pre-commit preview endpoint: parsed rows are written directly as `pending`-review transactions, which *are* the preview — see the M7 Import Job Lifecycle in the roadmap doc for this scope decision.)
- [x] Add import review queue persistence. (`transactions.review_state`, already added in M2, now populated by imports.)
- [x] Add reviewed import apply workflow. (`POST /api/v1/imports/{id}/apply`.)
- [x] Add duplicate transaction detection. (Flagged via `possible_duplicate`, not silently dropped.)
- [x] Add PDF ingestion pipeline. (Real text extraction via `pypdf`; produces a `document_extractions` row, not transactions.)
- [x] Add OCR engine adapter interface. (`DocumentExtractionAdapter` in `family_cfo_ocr_worker`.)
- [x] Add first OCR adapter or deterministic test adapter. (`DeterministicOcrAdapter` — no real OCR engine ships in M7, by design; see the M7 Non-Goals.)
- [x] Add structured extraction confidence scoring. (`ExtractionResult.confidence`.)
- [x] Add OFX planning documentation. (See "OFX and QFX Planning" in the roadmap doc.)
- [x] Add QFX planning documentation. (Same section.)
- [x] Add background worker service scaffold. (`family_cfo_scheduler` package.)
- [x] Add scheduled job runner. (`Scheduler`/`Job`, wired to the import job via `family-cfo-worker`.)
- [x] Add worker retry and failure handling. (`RetryPolicy`/`run_with_retry`, 3 attempts then `status = "failed"`.)
- [x] Add worker integration tests.
- [x] Add import API integration tests.
- [x] Add OCR adapter contract tests.
- [x] Add log redaction tests for document contents.
- [x] Update import and worker documentation.
- [x] Run verification commands.
- [ ] Commit M7 imports and OCR changes.

## M8: Reports and Backups

### Spec Gate

- [x] Define M8 scope.
- [x] Define M8 non-goals.
- [x] Define weekly report content.
- [x] Define monthly report content.
- [x] Define annual report content if included in M8. (Decided: not included — tracked as backlog below, roadmap bullets name only weekly/monthly.)
- [x] Define report generation schedule.
- [x] Define encrypted backup format.
- [x] Define backup key handling.
- [x] Define restore test expectations.
- [x] Define M8 security impact.
- [x] Define M8 unit and integration test expectations.
- [x] Define M8 documentation updates.

### Reports

- [x] Create migrations for reports.
- [x] Implement report generation service.
- [x] Implement weekly report generator.
- [x] Implement monthly report generator.
- [x] Implement annual report generator if accepted into M8 scope. (Not accepted into scope — see backlog.)
- [x] Include wins, risks, unusual spending, goal progress, and recommended actions in reports.
- [x] Attach explanation text to calculation references.
- [x] Implement `POST /api/v1/reports/generate`, `GET /api/v1/reports`, `GET /api/v1/reports/{id}`.
- [x] Add scheduled report job.
- [x] Add report unit tests.
- [x] Add report API integration tests.
- [x] Add report scheduler tests.

### Backups

- [x] Create migrations for backup jobs.
- [x] Implement backup job persistence.
- [x] Implement `BackupAdapter` protocol, `PgDumpBackupAdapter`, and `SqliteFileBackupAdapter`.
- [x] Implement encrypted PostgreSQL backup.
- [x] Implement encrypted document backup.
- [x] Implement encrypted Qdrant backup if vector data is used. (Non-goal — no vector database exists in this codebase yet.)
- [x] Implement backup retention policy.
- [x] Implement backup restore workflow.
- [x] Add restore verification test.
- [x] Add backup failure alert or dashboard status. (Dashboard status via `GET /api/v1/backups`; alerting is future work.)
- [x] Implement `POST /api/v1/backups`, `GET /api/v1/backups`, `POST /api/v1/backups/{id}/restore`.
- [x] Add scheduled daily backup job.
- [x] Document backup volumes.
- [x] Document backup key handling.
- [x] Document restore procedure.
- [x] Run verification commands.
- [ ] Commit M8 reports and backups changes.

## Backlog: Debt Payoff and Retirement Projections

The PRD (`docs/specs/01-prd.md`) promises "deterministic projections for cash flow, retirement, debt payoff, net worth, and savings goals" and a Scenario Planning journey ("Can we retire at 55?", "Should we refinance?"). The domain model (`docs/specs/03-domain-model.md`) lists "Refinance a mortgage" and "Accelerate debt payoff" as scenario examples. No milestone from M3 through M8 currently owns building this — it was silently dropped from M3's scope rather than deferred to a tracked task. This section tracks it until it is assigned to a milestone.

- [x] Add a deterministic `calculate_debt_payoff` calculation to the financial engine, unit tested with mocked `DebtInput` values (balance, annual interest rate, minimum payment, optional extra payment). This does not require database changes — it is a pure function like every other engine calculation.
- [ ] Add `annual_interest_rate` and `minimum_payment` columns (or a separate `debt_terms` table) to `accounts` for liability account types, with a migration.
- [ ] Wire `calculate_debt_payoff` into the purchase advisor's `debt` impact once account-level interest/payment data is persisted, replacing today's warning-only placeholder.
- [ ] Add a `calculate_retirement_projection` calculation to the financial engine.
- [ ] Add a general scenario-planning API (beyond the purchase advisor) covering "can we retire at 55" and "should we refinance" style questions, per the PRD's Scenario Planning journey.
- [ ] Assign the remaining items above to a specific milestone.

## Backlog: Annual Report

The PRD (`docs/specs/01-prd.md`) lists "weekly, monthly, and annual reports" as a functional requirement, but the M8 roadmap bullets (`docs/specs/11-milestone-roadmap.md`) name only weekly and monthly, so M8's spec gate scoped annual out rather than silently dropping it. No milestone currently owns it.

- [ ] Add an `annual` `report_type` to the M8 `reports` table/generation service (same wins/risks/unusual-spending/goal-progress/recommended-actions shape, year-scoped instead of week/month-scoped).
- [ ] Add a scheduled annual report job (e.g. January 1st) once the above lands.
- [ ] Assign to a specific milestone.

## Release Readiness

### Security and Privacy

- [ ] Resolve threat model open question for database encryption.
- [ ] Resolve threat model open question for local certificate provisioning.
- [ ] Resolve threat model open question for backup key recovery.
- [ ] Decide whether optional external AI providers are allowed only through a future explicit opt-in ADR.
- [ ] Add HTTPS configuration for app-to-server communication.
- [ ] Add token rotation.
- [ ] Add session expiration enforcement.
- [ ] Add role-based authorization tests.
- [ ] Add paired device revocation tests.
- [ ] Add structured logging redaction tests.
- [ ] Add no-telemetry verification.
- [ ] Add secret scanning to CI.
- [ ] Add dependency vulnerability checks.

### Docker and Deployment

- [ ] Add Dockerfile for API.
- [ ] Add Dockerfile for Angular dashboard.
- [ ] Add Dockerfile for worker.
- [ ] Add Docker Compose service for web.
- [ ] Add Docker Compose service for API.
- [ ] Add Docker Compose service for PostgreSQL.
- [ ] Add Docker Compose service for Qdrant.
- [ ] Add Docker Compose service for vLLM.
- [ ] Add Docker Compose service for worker.
- [ ] Add development Compose profile.
- [ ] Add home-server Compose profile.
- [ ] Configure private Docker network.
- [ ] Expose only intended UI and API ports.
- [ ] Keep vLLM private by default.
- [ ] Document environment variables and secrets.
- [ ] Document persistent volumes.
- [ ] Test `docker compose up -d`.
- [ ] Test backup and restore in Docker environment.

### Contracts and Generated Clients

- [ ] Add OpenAPI linting to CI.
- [x] Add backend OpenAPI drift check to CI.
- [ ] Add Angular client generation check to CI.
- [ ] Add Swift client generation check to CI.
- [ ] Document generated client regeneration workflow.
- [ ] Ensure clients do not hand-maintain DTOs that should come from OpenAPI.

### Quality Gates

- [x] Add backend linting to CI.
- [x] Add backend unit tests to CI.
- [x] Add backend integration tests to CI.
- [ ] Add financial engine unit tests to CI.
- [ ] Add AI orchestrator tests to CI.
- [ ] Add worker tests to CI.
- [ ] Add Angular linting and tests to CI.
- [ ] Add iOS test instructions and CI plan.
- [ ] Add end-to-end smoke test for local deployment.
- [ ] Add synthetic fixture policy check.
- [ ] Add documentation link check.

### Documentation

- [ ] Update root README with implemented setup path.
- [ ] Add local development quickstart.
- [ ] Add home-server deployment guide.
- [ ] Add onboarding guide.
- [ ] Add API development guide.
- [ ] Add database migration guide.
- [ ] Add financial engine guide.
- [ ] Add AI runtime configuration guide.
- [ ] Add import guide.
- [ ] Add backup and restore guide.
- [ ] Add security hardening guide.
- [ ] Add troubleshooting guide.
- [ ] Add release checklist.

### Final Acceptance

- [ ] Verify all milestone tasks are complete or explicitly deferred.
- [ ] Verify all accepted specs match implemented behavior.
- [ ] Verify all OpenAPI endpoints match backend behavior.
- [ ] Verify generated clients build against current OpenAPI.
- [ ] Verify no sensitive sample data is committed.
- [ ] Verify Docker deployment works from a clean checkout.
- [ ] Verify backup restore works from a clean environment.
- [ ] Verify documentation is current.
- [ ] Tag first release.
