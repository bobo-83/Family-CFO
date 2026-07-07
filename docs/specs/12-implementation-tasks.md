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

- [ ] Define M2 scope.
- [ ] Define M2 non-goals.
- [ ] Expand domain model for households, users, accounts, balances, transactions, bills, income, goals, and scenarios.
- [ ] Update OpenAPI for any M2 endpoints not already covered.
- [ ] Define database tables, indexes, constraints, and relationship rules for M2 entities.
- [ ] Define role and authorization expectations for financial context endpoints.
- [ ] Define audit requirements for deterministic calculations.
- [ ] Define money precision and currency handling rules.
- [ ] Define M2 unit and integration test expectations.
- [ ] Define M2 documentation updates.

### Data Model and Persistence

- [ ] Create migrations for households.
- [ ] Create migrations for users.
- [ ] Create migrations for household memberships.
- [ ] Create migrations for auth sessions.
- [ ] Create migrations for accounts.
- [ ] Create migrations for account balances.
- [ ] Create migrations for transactions.
- [ ] Create migrations for transaction categories.
- [ ] Create migrations for bills.
- [ ] Create migrations for income sources.
- [ ] Create migrations for goals.
- [ ] Create migrations for scenarios.
- [ ] Create migrations for financial calculations.
- [ ] Add database constraints that prevent floating-point persisted money.
- [ ] Add synthetic database fixtures.
- [ ] Add migration rollback tests where practical.

### Backend APIs

- [ ] Implement local authentication foundation needed for protected routes.
- [ ] Implement household context read API.
- [ ] Implement account list API.
- [ ] Implement goal list API.
- [ ] Implement goal create API.
- [ ] Add transaction APIs if accepted into M2 OpenAPI scope.
- [ ] Add bill APIs if accepted into M2 OpenAPI scope.
- [ ] Add income APIs if accepted into M2 OpenAPI scope.
- [ ] Add repository tests for financial context persistence.
- [ ] Add API integration tests for protected financial context routes.

### Financial Engine

- [ ] Add deterministic financial engine package or service boundary.
- [ ] Add money value type using integer minor units and explicit currency.
- [ ] Add calculation result contract with inputs, assumptions, version, warnings, and outputs.
- [ ] Implement net worth calculation.
- [ ] Implement cash flow calculation.
- [ ] Implement budget summary calculation.
- [ ] Implement emergency fund months calculation.
- [ ] Implement savings goal progress calculation.
- [ ] Add unit tests for money precision.
- [ ] Add unit tests for currency mismatch handling.
- [ ] Add unit tests for net worth calculation.
- [ ] Add unit tests for cash flow calculation.
- [ ] Add unit tests for budget calculation.
- [ ] Add unit tests for emergency fund calculation.
- [ ] Add unit tests for goal progress calculation.
- [ ] Persist calculation audit records.
- [ ] Document financial engine contracts and limitations.
- [ ] Run verification commands.
- [ ] Commit M2 financial context and engine changes.

## M3: Purchase Advisor

### Spec Gate

- [ ] Define M3 scope.
- [ ] Define M3 non-goals.
- [ ] Confirm purchase advisor request and recommendation response in OpenAPI.
- [ ] Define scenario input persistence.
- [ ] Define deterministic purchase impact calculations.
- [ ] Define LLM explanation adapter stub behavior.
- [ ] Define security impact for recommendation data and prompts.
- [ ] Define M3 unit and integration test expectations.
- [ ] Define M3 documentation updates.

### Implementation

- [ ] Create scenario persistence model and migration.
- [ ] Implement purchase scenario input validation.
- [ ] Implement purchase impact calculation using the financial engine.
- [ ] Calculate discretionary cash flow impact.
- [ ] Calculate emergency fund impact.
- [ ] Calculate debt payoff impact where data exists.
- [ ] Calculate savings goal impact where data exists.
- [ ] Calculate net worth impact.
- [ ] Generate recommendation response with answer, assumptions, impacts, tradeoffs, alternatives, confidence, warnings, and calculation references.
- [ ] Add LLM explanation adapter interface.
- [ ] Add deterministic no-model explanation stub.
- [ ] Ensure numeric recommendation claims cite calculation references.
- [ ] Persist recommendation records.
- [ ] Implement `POST /api/v1/advisor/purchase`.
- [ ] Add unit tests for purchase impact calculations.
- [ ] Add unit tests for recommendation response structure.
- [ ] Add integration tests for purchase advisor API.
- [ ] Add prompt and response redaction tests for logged data.
- [ ] Update API README and financial engine docs.
- [ ] Run verification commands.
- [ ] Commit M3 purchase advisor changes.

## M4: Local AI Runtime

### Spec Gate

- [ ] Define M4 scope.
- [ ] Define M4 non-goals.
- [ ] Define AI runtime adapter interface.
- [ ] Define vLLM configuration requirements.
- [ ] Define model and prompt version tracking.
- [ ] Define guardrail behavior for missing calculation references and hallucinated financial facts.
- [ ] Define data retention expectations for prompts and model responses.
- [ ] Define M4 unit and integration test expectations.
- [ ] Define M4 documentation updates.

### Implementation

- [ ] Create AI orchestrator package or service boundary.
- [ ] Add AI runtime adapter interface.
- [ ] Add vLLM adapter behind the runtime interface.
- [ ] Add OpenAI-compatible request and response mapping.
- [ ] Add runtime timeout and retry policy.
- [ ] Add runtime configuration persistence.
- [ ] Implement `GET /api/v1/ai/runtime`.
- [ ] Implement `PUT /api/v1/ai/runtime`.
- [ ] Track model version with recommendation records.
- [ ] Track prompt version with recommendation records.
- [ ] Add prompt template versioning.
- [ ] Add guardrail that rejects numeric claims without calculation references.
- [ ] Add guardrail that exposes missing information instead of inventing facts.
- [ ] Add adapter contract tests.
- [ ] Add vLLM adapter tests with mocked runtime responses.
- [ ] Add recommendation guardrail tests.
- [ ] Document supported runtime configuration.
- [ ] Update threat model if prompt retention or runtime exposure changes.
- [ ] Run verification commands.
- [ ] Commit M4 local AI runtime changes.

## M5: Angular Dashboard

### Spec Gate

- [ ] Define M5 scope.
- [ ] Define M5 non-goals.
- [ ] Define onboarding flow behavior.
- [ ] Define dashboard information architecture for M5.
- [ ] Define generated Angular client workflow.
- [ ] Define browser-side security expectations.
- [ ] Define M5 unit and integration test expectations.
- [ ] Define M5 documentation updates.

### Implementation

- [ ] Add Angular project scaffold under `apps/web`.
- [ ] Add generated API client workflow from OpenAPI.
- [ ] Add dashboard app shell and routing.
- [ ] Add local authentication/session UI.
- [ ] Add onboarding flow.
- [ ] Add overview page.
- [ ] Add accounts page.
- [ ] Add goals page.
- [ ] Add reports shell.
- [ ] Add transaction review shell.
- [ ] Add import review shell.
- [ ] Add AI runtime settings page.
- [ ] Add backup management shell.
- [ ] Add user management shell.
- [ ] Add paired device revocation UI.
- [ ] Add error and loading states.
- [ ] Add form validation for supported M5 workflows.
- [ ] Add unit tests for dashboard components.
- [ ] Add integration tests for generated client usage.
- [ ] Add end-to-end smoke test for onboarding and health connectivity.
- [ ] Update web README with development commands.
- [ ] Run verification commands.
- [ ] Commit M5 Angular dashboard changes.

## M6: iPhone App

### Spec Gate

- [ ] Define M6 scope.
- [ ] Define M6 non-goals.
- [ ] Define pairing flow details.
- [ ] Define generated Swift client workflow.
- [ ] Define secure credential storage expectations.
- [ ] Define Face ID local unlock behavior.
- [ ] Define camera capture and structured image output rules.
- [ ] Define M6 unit and integration test expectations.
- [ ] Define M6 documentation updates.

### Implementation

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

- [ ] Define M7 scope.
- [ ] Define M7 non-goals.
- [ ] Define import job lifecycle.
- [ ] Define CSV import schema and mapping behavior.
- [ ] Define PDF pipeline behavior.
- [ ] Define OFX and QFX planning scope.
- [ ] Define OCR adapter interface.
- [ ] Define review queue behavior before imported data affects financial state.
- [ ] Define worker scheduling expectations.
- [ ] Define security impact for document storage, extraction, and logs.
- [ ] Define M7 unit and integration test expectations.
- [ ] Define M7 documentation updates.

### Implementation

- [ ] Create migrations for imports.
- [ ] Create migrations for import files.
- [ ] Create migrations for documents.
- [ ] Create migrations for document extractions.
- [ ] Add import staging storage.
- [ ] Implement `GET /api/v1/imports`.
- [ ] Implement `POST /api/v1/imports`.
- [ ] Add CSV parser with synthetic fixtures.
- [ ] Add CSV mapping and validation.
- [ ] Add CSV import preview.
- [ ] Add import review queue persistence.
- [ ] Add reviewed import apply workflow.
- [ ] Add duplicate transaction detection.
- [ ] Add PDF ingestion pipeline.
- [ ] Add OCR engine adapter interface.
- [ ] Add first OCR adapter or deterministic test adapter.
- [ ] Add structured extraction confidence scoring.
- [ ] Add OFX planning documentation.
- [ ] Add QFX planning documentation.
- [ ] Add background worker service scaffold.
- [ ] Add scheduled job runner.
- [ ] Add worker retry and failure handling.
- [ ] Add worker integration tests.
- [ ] Add import API integration tests.
- [ ] Add OCR adapter contract tests.
- [ ] Add log redaction tests for document contents.
- [ ] Update import and worker documentation.
- [ ] Run verification commands.
- [ ] Commit M7 imports and OCR changes.

## M8: Reports and Backups

### Spec Gate

- [ ] Define M8 scope.
- [ ] Define M8 non-goals.
- [ ] Define weekly report content.
- [ ] Define monthly report content.
- [ ] Define annual report content if included in M8.
- [ ] Define report generation schedule.
- [ ] Define encrypted backup format.
- [ ] Define backup key handling.
- [ ] Define restore test expectations.
- [ ] Define M8 security impact.
- [ ] Define M8 unit and integration test expectations.
- [ ] Define M8 documentation updates.

### Reports

- [ ] Create migrations for reports.
- [ ] Implement report generation service.
- [ ] Implement weekly report generator.
- [ ] Implement monthly report generator.
- [ ] Implement annual report generator if accepted into M8 scope.
- [ ] Include wins, risks, unusual spending, goal progress, and recommended actions in reports.
- [ ] Attach explanation text to calculation references.
- [ ] Implement `GET /api/v1/reports`.
- [ ] Add scheduled report job.
- [ ] Add report unit tests.
- [ ] Add report API integration tests.
- [ ] Add report scheduler tests.

### Backups

- [ ] Create migrations for backup jobs.
- [ ] Implement backup job persistence.
- [ ] Implement encrypted PostgreSQL backup.
- [ ] Implement encrypted document backup.
- [ ] Implement encrypted Qdrant backup if vector data is used.
- [ ] Implement backup retention policy.
- [ ] Implement backup restore workflow.
- [ ] Add restore verification test.
- [ ] Add backup failure alert or dashboard status.
- [ ] Document backup volumes.
- [ ] Document backup key handling.
- [ ] Document restore procedure.
- [ ] Run verification commands.
- [ ] Commit M8 reports and backups changes.

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
- [ ] Add backend OpenAPI drift check to CI.
- [ ] Add Angular client generation check to CI.
- [ ] Add Swift client generation check to CI.
- [ ] Document generated client regeneration workflow.
- [ ] Ensure clients do not hand-maintain DTOs that should come from OpenAPI.

### Quality Gates

- [ ] Add backend linting to CI.
- [ ] Add backend unit tests to CI.
- [ ] Add backend integration tests to CI.
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
