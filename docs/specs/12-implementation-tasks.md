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
- [x] Commit M6 dashboard integration changes. (Committed in `4f1bce0`.)

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
- [x] Commit M8 reports and backups changes. (Committed in `a02f430`.)

## M9: Household Setup, Data Management, and Audit

### Spec Gate

- [x] Define M9 scope, non-goals, and referential-integrity/delete behavior.
- [x] Define M9 API behavior and role gating.
- [x] Define M9 data model changes (`audit_events`).
- [x] Define M9 security impact.
- [x] Define M9 test expectations.
- [x] Define M9 documentation updates.

### Implementation

- [x] Create migration for `audit_events`.
- [x] Add `audit_events` model and repository (create + list).
- [x] Add an audit helper that writes a non-sensitive row per sensitive mutation.
- [x] Implement `POST /api/v1/households` bootstrap (household + owner + membership + session).
- [x] Implement membership management: `GET`/`POST /api/v1/household/members`, `PATCH`/`DELETE /api/v1/household/members/{user_id}` with last-owner/self-demotion guards.
- [x] Implement account writes: `POST`/`PATCH`/`DELETE /api/v1/accounts` and `POST /api/v1/accounts/{id}/balances`.
- [x] Implement transaction writes: `POST`/`PATCH`/`DELETE /api/v1/transactions`.
- [x] Implement bill writes: `POST`/`PATCH`/`DELETE /api/v1/bills`.
- [x] Implement income writes: `POST`/`PATCH`/`DELETE /api/v1/income`.
- [x] Implement `GET /api/v1/audit` (owner only).
- [x] Enforce referential-integrity `409` conflicts (account in use, last owner).
- [x] Update `shared/openapi/family-cfo.v1.yaml` with write paths and new schemas.
- [x] Add repository unit tests (writes, balance append, membership guards, audit rows).
- [x] Add API integration tests (create/read/update/delete, role gating, `404`/`409`, bootstrap, audit non-sensitivity).
- [x] Update `apps/api/README.md` and `database/README.md`.
- [x] Add a tracked follow-up item for extending audit coverage to pre-M9 mutations.
- [x] Run verification commands.
- [x] Commit M9 changes.

## M10: Conversation History

### Spec Gate

- [x] Define M10 scope, non-goals, and conversation/message behavior.
- [x] Define M10 API behavior and role gating.
- [x] Define M10 data model changes (`conversations`, `conversation_messages`).
- [x] Define M10 security impact.
- [x] Define M10 test expectations.
- [x] Define M10 documentation updates.

### Implementation

- [x] Create migrations for `conversations` and `conversation_messages`.
- [x] Add models and repository (create conversation, append message, list, get, delete).
- [x] Persist user + assistant turns in `POST /api/v1/chat/messages`, creating/appending a conversation.
- [x] Link assistant messages to their `recommendation_id`.
- [x] Implement `GET /api/v1/conversations` and `GET /api/v1/conversations/{id}`.
- [x] Implement `DELETE /api/v1/conversations/{id}` (owner/adult).
- [x] Update `shared/openapi/family-cfo.v1.yaml` with the `Conversations` paths and schemas.
- [x] Add repository unit tests (create-on-first, append, ordering, delete-cascade, scoping).
- [x] Add API integration tests (thread lifecycle, grounding link, cross-household `404`, role gating).
- [x] Update `apps/api/README.md` and `database/README.md`.
- [x] Run verification commands.
- [x] Commit M10 changes.

## M11: Dashboard Data Entry and Review UIs

### Spec Gate

- [x] Define M11 scope, non-goals, and UI behavior/role gating.
- [x] Define M11 test expectations.
- [x] Define M11 documentation updates.

### Implementation

- [x] Regenerate the Angular OpenAPI client for the M9 (and any M10) shapes.
- [x] Add `ApiService` wrapper methods for the new write/read endpoints.
- [x] Upgrade the Accounts page with create/edit/delete and record-balance actions.
- [x] Build the real Transactions page (list + create/edit/delete, plus bill/income management).
- [x] Build the real Imports review page (register, upload, status, apply/discard, documents).
- [x] Build the real Reports page (generate + list/detail rendering).
- [x] Build the real Backups page (list, create, restore with confirmation).
- [x] Add member management to the Users & Devices page (list/create/role-edit/remove).
- [x] Gate write actions in the UI to the matching roles.
- [x] Convert money major→minor units in entry forms.
- [x] Add Vitest tests for each page (happy path + role-gated controls + money conversion + restore confirm).
- [x] Extend the Playwright e2e smoke test (login → create account → add transaction → generate report → list).
- [x] Update `apps/web/README.md` and `docs/specs/README.md`.
- [x] Run frontend verification commands.
- [x] Commit M11 changes.

## M16: Agentic Tool-Calling (Conversational Advisor)

### Spec Gate

- [x] Define M16 scope, non-goals, tool library, and the tool-calling loop / trust boundary. (See `docs/specs/11-milestone-roadmap.md` and ADR 0009.)
- [x] Define M16 API behavior, data model, security, test, and documentation impact.

### Implementation

- [x] Add a `future_value` / opportunity-cost calculation to the financial engine with unit tests. (`services/financial-engine/.../future_value.py`, migration `0031`.)
- [x] Add a tool-descriptor layer: JSON-schema descriptors + argument validation (type/range/currency; household scoping via context, no model-supplied entity ids) wrapping the engine calculations. (`apps/api/.../ai_tools.py`.)
- [x] Extend `VLLMAdapter`/`RuntimeAdapter` with tool/function-calling (pass tool schemas, parse `tool_calls`). (`services/ai-orchestrator/.../runtime.py`, `vllm_adapter.py`.)
- [x] Build the tool-calling orchestration loop (bounded iterations, execute tools, feed results back, extract grounded final answer, missing-fact "ask back"). (`services/ai-orchestrator/.../tool_calling.py`.)
- [x] Route `POST /api/v1/chat/messages` through the loop when an enabled tool-calling runtime exists; keep the deterministic snapshot fallback otherwise; persist via M10 conversations. (`apps/api/.../api/chat.py`.)
- [x] Extend guardrails to validate tool arguments (in `ai_tools.py`) and to verify the final answer's figures trace to tool outputs (`grounded_values` + `validate_recommendation`; fails closed to the deterministic snapshot).
- [x] Add tests: engine primitive; per-tool argument validation incl. foreign-currency rejection; stubbed-runtime multi-step loop; iteration cap; missing-fact path; no-model + ungrounded-number fallback; chat API integration.
- [x] Update docs (ai-orchestrator, apps/api, financial-engine READMEs; acceptance state).
- [x] Run verification commands.
- [x] Commit M16 changes. (`feat(m16): agentic tool-calling advisor for open-ended chat`.)

## M17: Turnkey Deployment (AI on by default)

### Spec Gate

- [x] Define M17 scope, non-goals, security impact, and test expectations. (See `docs/specs/11-milestone-roadmap.md`.)

### Implementation

- [x] Add deployment settings for a default AI runtime (`FAMILY_CFO_AI_ENABLED/PROVIDER/BASE_URL/MODEL`) in `apps/api/.../config.py`; code default off.
- [x] Resolve a household's effective AI config (own row, else settings default) in `ai_runtime_selection.py`; both `select_tool_runtime` and `select_explanation_adapter` use it; `GET /ai/runtime` returns the deployment default.
- [x] Make vLLM run by default (remove the `ai` Compose profile) and wire api/worker env to it; keep a GPU-less escape hatch (`FAMILY_CFO_AI_ENABLED=false` + `--scale vllm=0`).
- [x] Add `scripts/deploy.sh`: interactive local/remote (SSH) one-command full-stack deploy that rsyncs the repo, generates `.env` secrets on first deploy, runs `docker compose up -d --build`, and prints the dashboard URL.
- [x] Add tests: runtime-selection default/override/unusable cases; existing chat/advisor/ai-runtime tests unchanged; `bash -n scripts/deploy.sh`.
- [x] Update docs (`10-docker-spec.md`, `.env.example`, `docker/README.md`, deployment + AI-advisor guides, acceptance state).
- [x] Run verification commands.
- [x] Commit M17 changes. (`feat(m17): turnkey deployment with local AI on by default`.)

## M18: Security Hardening Pass & Deployment Tooling

### Spec Gate

- [x] Define M18 scope, non-goals, security impact, and test expectations. (See `docs/specs/11-milestone-roadmap.md` and ADR 0010.)

### Implementation

- [x] SSRF: allowlist AI runtime `base_url` (`FAMILY_CFO_AI_ALLOWED_BASE_URLS`); reject others on `PUT /ai/runtime`. Tests.
- [x] Auth throttling: per-IP + per-account limiter + lockout on `POST /auth/sessions` (`ratelimit.py`). Tests.
- [x] Upload cap: bounded read + max bytes in imports/documents handlers + `client_max_body_size` in nginx. Tests.
- [x] Pairing: CSPRNG pairing session id (`security.generate_pairing_secret`). Test.
- [x] Prod docs gating: disable Swagger/openapi.json under `FAMILY_CFO_ENV=production`. Test.
- [x] `scripts/doctor.sh` health report. `bash -n`.
- [x] `scripts/patch.sh` — fast app-only redeploy (rebuild api/worker/web local or remote; refuses vllm/db so the model/DB are untouched; api auto-migrates on start). `bash -n`; verified live (2s no-op patch, vllm uptime unchanged).
- [x] `scripts/e2e-deploy-test.sh` real build + core-stack boot + login + chat smoke + teardown; run it for real.
- [x] System requirements (per-model RAM/VRAM + storage min/recommended) in README + deployment guide; deploy-script preflight.
- [x] Update docs (ADR 0010, README, guides, `.env.example`, docker README, nginx, acceptance state).
- [x] Run verification commands.
- [ ] Commit M18 changes.

## M19: Dashboard AI Chat & Self Sign-up

### Spec Gate

- [x] Define M19 scope, non-goals, and test expectations. (See `docs/specs/11-milestone-roadmap.md`.)

### Implementation

- [x] Add `GET /ai/runtime/status` (`getAiRuntimeStatus`) probing the runtime; add `AiRuntimeStatus` schema; update shared contract (+ fix the missing `POST /households` request body); regenerate client. Backend tests.
- [x] Extend `ApiService` with `createHousehold`, `createChatMessage`, `listConversations`, `getConversation`, `getAiRuntimeStatus`; add `AuthService.signup`.
- [x] Sign-up page (`/signup`, public) + link from login; update login copy.
- [x] AI Chat page (`/chat`, authed): send message, render recommendation, conversation history, new-conversation; AI status banner; confidence chip; shell nav link.
- [x] Vitest component tests for both pages; `ng test` (47) + production build type-check pass.
- [x] Update `apps/web/README.md` and acceptance state.
- [ ] Commit M19 changes.

## M20: Dashboard Redesign & Mobile Support

### Spec Gate

- [x] Define M20 scope, non-goals, and test expectations. (See `docs/specs/11-milestone-roadmap.md`.)

### Implementation

- [x] Design tokens + global element baseline in `styles.scss`; head metadata (title, theme-color, viewport-fit).
- [x] Responsive shell: top app bar + slide-in drawer under the breakpoint; refined desktop sidebar; safe-area insets. Shell menu component test (3 tests).
- [x] Responsive pages: global scrollable tables, chat history chip-strip, auth-card widths, tokenized page accents.
- [x] `ng test` (50) + production build pass; patched onto the live deployment.
- [x] Update `apps/web/README.md` + acceptance state.
- [ ] Commit M20 changes.

## M21: Chat Photo Attachments (Vision Routing)

### Spec Gate

- [x] Define M21 scope, non-goals, and test expectations. (See `docs/specs/11-milestone-roadmap.md` and ADR 0011.)

### Implementation

- [x] Orchestrator: image support in `RuntimeMessage`/`VLLMAdapter` (multimodal content parts) + `describe_image` helper. Tests (24 pass).
- [x] Contract: `ChatRequest.image_base64`/`image_media_type`; `AiRuntimeStatus.vision_ready`/`vision_model`; client regenerated; contract test green.
- [x] API: describe-then-ground flow (main-vision / describer / graceful-warning paths), validation (cap 413 / type 422), grounded description numbers, in-memory-only image handling. Tests (206 pass).
- [x] Compose: `vllm-vision` service (Qwen2.5-VL-7B) + `VLLM_GPU_FRACTION`/`VLLM_VISION_GPU_FRACTION`; `.env.example`.
- [x] Web chat: attach/camera button (`capture=environment`), canvas downscale->JPEG, preview/remove, photo marker, vision status chip. Tests (52 pass).
- [x] Docs (ADR 0011, README requirements row, docker README, AI-advisor guide, mobile-spec backlog note, acceptance state).
- [x] Verification: all suites + build; deployed to the live stack.
- [ ] Commit M21 changes.

## M22: Model Selection, Hardware Planning & Status Clarity

### Spec Gate

- [x] Define M22 scope, non-goals, and test expectations. (See roadmap and ADR 0012.)

### Implementation

- [x] `GET /ai/models` catalog + `GET /ai/hardware` profile + `AiRuntimeStatus.vision_enabled`; contract + client regen. Tests (211 api pass).
- [x] Chat banner: separate main/vision states; camera button alignment fix.
- [x] AI Runtime page rebuild: pickers, live fit metrics, save, mismatch notice + swap command. Tests (57 web pass).
- [x] `scripts/swap-model.sh` + deploy-script GPU memory detection.
- [x] Docs (ADR 0012, AI-advisor guide, acceptance state); verification; live patch.
- [ ] Commit M22 changes.

## M23: Hugging Face Model Search & One-Click Apply

### Spec Gate

- [x] Define M23 scope, non-goals, and test expectations. (See roadmap and ADR 0013.)

### Implementation

- [x] `services/model-manager` sidecar (validated /swap running swap-model.sh, /status) + Dockerfile + compose entry (socket + project mount, internal-only). Tests (5 pass).
- [x] API: `GET /ai/models/search` (HF proxy + estimates), `POST /ai/runtime/apply`, `GET /ai/runtime/apply/status`; contract + client regen. Tests (217 api pass).
- [x] Web AI Runtime: HF search box, Apply button, live apply/status panel with 5s polling. Tests (60 web pass).
- [x] Docs (ADR 0013, docker README, AI-advisor guide, acceptance state); verification; live deploy.
- [ ] Commit M23 changes.

## M24: Live-Data Chat Tools

### Spec Gate

- [x] Define M24 scope, non-goals, and test expectations. (See roadmap and ADR 0014.)

### Implementation

- [x] `get_exchange_rate` + optional `web_search` tools in the registry (settings-gated), prompt update. Tests (222 api pass).
- [x] `searxng` compose profile + env knobs (`FAMILY_CFO_LIVE_DATA_ENABLED`, `FAMILY_CFO_SEARXNG_URL`).
- [x] Docs (ADR 0014, .env.example, docker README, AI-advisor guide, acceptance state); verification; live deploy.
- [ ] Commit M24 changes.

## M25: Per-Response Model Attribution

- [x] Spec gate (roadmap).
- [x] Persist model_version on chat recommendations; `answered_by` + `photo_described_by` in contract + response (photo attribution also persisted as an assumption line); UI caption showing chat model and photo-reader model; tests (226 api / 63 web); deployed; committed.

## M26: Chat Usability Pass

- [x] Spec gate (roadmap).
- [x] Zoom hardening (touch-action: manipulation globally + explicit 16px chat input); conversation delete UI with confirm + owner/adult gating (existing M10 endpoint) clearing the open thread; history restyled as cards with title + date; tests (66 web); deployed; committed.

## M27: Institution Connections & Transaction Dedupe

### Spec Gate

- [x] Define M27 scope, non-goals, and test expectations. (See roadmap and ADR 0015.)

### Implementation

- [x] Migration 0033: connections tables + transactions.external_id/import_hash + unique index (cycle green via test_migrations).
- [x] BankConnector protocol + SimpleFINConnector + Fernet credential encryption. Tests (7).
- [x] Dedupe core (provider-id hard + content-hash fallback) wired into sync AND the CSV pipeline (re-upload now imports 0, reports skipped count).
- [x] Connection endpoints + sync + auto-account mapping + audit events; contract + client regen. Tests (237 api total).
- [x] Scheduled daily sync job (worker, same cadence as backups; per-connection errors recorded, others continue).
- [x] Imports page "Linked institutions" UI (link via setup token, sync-now with counts, unlink with confirm; owner/adult). Tests (67 web total).
- [x] Docs; verification; live deploy; commit.

## M28: Live Price Search On By Default

- [x] Spec note (amends ADR 0014): SearXNG un-profiled with JSON-enabled config (`docker/searxng-settings.yml`: formats+json, limiter off) + `SEARXNG_SECRET`; `FAMILY_CFO_SEARXNG_URL` defaults to the bundled instance; deploy.sh generates the secret; opt-out documented; deployed + real price-question e2e; commit.

## M29: Inference Performance (AWQ)

- [x] Diagnosed slow responses: GPU active (verified via nvidia-smi + CUDA/FlashAttention logs); bottleneck = memory bandwidth (measured 3.2 tok/s decoding 65GB bf16 on ~273GB/s unified memory). Swapped the live main model to Qwen2.5-32B-Instruct-AWQ via swap-model.sh (M23 tooling): measured 7.9 tok/s (~2.5×; currency question 6.2s end-to-end, attribution confirming the AWQ model answered). Added 32B-AWQ + 14B-AWQ to the curated catalog with unified-memory guidance; README perf note with measured numbers.

## M30: Conversational Memory

- [x] Prior turns of the active conversation are sent to the model (bounded: last 8 messages, 1500 chars each) so follow-ups have context — M10 persisted the thread but the loop never saw it. History numbers join the grounded set (they passed the guardrail when produced). Tests: history included between system and current message; earlier grounded figures can be echoed without tripping the guardrail. Verified live with the exact reported failure ("Mac mini at Best Buy" → "How about at Apple.com?" now answers in context).

## M31: Advisor Personality

- [x] Persona layer over the invariant grounding rules (`FAMILY_CFO_AI_TONE`: playful default / professional; unknown -> playful). Also fixed a real guardrail precision bug surfaced in live testing: tool floats like 9.6470588 now ground their rounded forms (9.6 / 9.65 / 10), so honest rounding no longer forces deterministic fallbacks. Tests; live-verified both.

## M32: Single-Household Lockout, Full Audit Coverage & v0.2.0

- [x] Spec gate (roadmap).
- [x] Bootstrap lockout (403 once a household exists; FAMILY_CFO_ALLOW_MULTIPLE_HOUSEHOLDS opt-out; first run unaffected). Tests.
- [x] Audit events for login, pairing confirm, device revoke, AI config/apply, import apply/discard, report generation, backup create/restore (summaries secret-free, asserted). Tests.
- [x] Version bumps to 0.2.0 (API + contract + regenerated client), RELEASE-CHECKLIST v0.2.0 section, full cross-package verification (65/24/5/247/67), tag v0.2.0.

## M33: Asset Spendability & Accounts Page Organization

- [x] Spec gate: (a) the net-worth tool gains a spendability breakdown (liquid / investments / retirement / education / property / debts) and the grounding rules state retirement+529 funds are not spendable for purchases — big-purchase affordability must be reasoned from liquid (and cautiously taxable investment) assets; (b) `GET /accounts` gains nullable `institution` + `last_synced_at` from the connection mapping (additive contract), and the Accounts page groups accounts by category with institution + last-synced shown.
- [x] Implemented + tested (250 api / 67 web) + live re-test: the 850k-house answer now reasons from liquid assets and flags the emergency-fund breach instead of spending net worth; Accounts page grouped (Cash/Investments/Retirement/Education/Property/Debts) with institution + last-synced columns.

## M34: Real Document Pipeline (OFX/QFX, PDF Line-Items, OCR)

- [x] Spec gate: (a) OFX/QFX imports parse STMTTRN blocks (tolerant SGML/XML regex parser, no new deps) into pending transactions, with **FITID feeding the M27 external_id hard-dedupe** — re-importing an OFX is idempotent; (b) PDF imports gain a heuristic statement line-item parser (date + amount + payee lines → pending transactions for review, content-hash deduped, unparseable lines skipped and counted; the document extraction is kept as before); (c) ocr-worker gains a TesseractOcrAdapter used automatically when the tesseract binary is present (image documents get real OCR; the deterministic adapter remains the fallback and the test default); tesseract-ocr added to the api/worker image. UI: OFX/QFX options in the import form.
- [x] Implement + tests (incl. OFX re-import idempotency; tesseract test skipped when binary absent) + deploy + commit. Verified: 253 api + 10 ocr-worker tests pass, patched deployment reports `tesseract 5.5.0` inside the api image with the real adapter active.

## M35: Connected Account Typing (401k Was "checking")

Bank sync hardcoded `type="checking"` for every auto-created connected account — SimpleFIN's protocol carries no account-type field. A 401k typed as checking is worse than cosmetic: M33 spendability classifies checking as **liquid**, so the advisor would treat retirement money as spendable.

- [x] Spec gate: (a) `infer_account_type(name)` in banksync — conservative name-pattern inference (401k/403b/457/IRA/Roth/pension/TSP → retirement; HSA; 529/college savings → 529; brokerage/investment → brokerage; savings/money market → savings; credit card → credit_card; mortgage; auto/car loan → auto_loan; student loan → student_loan; anything else stays checking) applied only when a connection account is first auto-created — never retypes an existing account, so manual corrections are preserved; (b) the Accounts page lets owners/adults change an account's type inline (per-row select → existing `updateAccount` PATCH, already in the contract) — this is also how already-mislabeled accounts get fixed. No contract or migration changes.
- [x] Implement + tests (inference table; sync creates "Acme 401k Plan" as retirement; manual retype survives re-sync; UI select patches on change) + deploy + commit. Verified: 255 api + 67 web tests pass, patched deployment healthy.

## M36: Emergency Fund Designation

Users need to earmark money for emergencies across **multiple accounts** — either a percentage of an account's balance or a fixed amount — and the advisor must treat that reservation as untouchable when answering affordability questions. Today the closest thing is a savings goal, which reserves nothing.

- [x] Spec gate: (a) schema — nullable `emergency_fund_percent` (0–100) and `emergency_fund_minor` (≥0) on `accounts`, mutually exclusive (CHECK: not both set), migration `0034`; the reserved amount is **derived**: `percent × latest balance` (round half-up) or `min(fixed amount, balance)` — never negative, never more than the balance; (b) contract — `Account` gains nullable `emergency_fund_percent`, `emergency_fund_amount` (Money, the configured fixed value) and computed `emergency_fund_reserved` (Money); `AccountUpdateRequest` gains `emergency_fund_percent`/`emergency_fund_amount` with an explicit `clear_emergency_fund` boolean (PATCH can't distinguish absent from null otherwise); 400 when both are set; (c) advisor — the net-worth tool payload gains `emergency_fund_reserved` and the spendability note subtracts it from liquid funds; grounding rules updated: emergency-fund money is reserved, not spendable for purchases; the emergency-fund coverage calculation measures the **designated** fund when one exists (falling back to the legacy all-liquid approximation when none is set); (d) UI — Accounts page rows get an emergency-fund editor (mode: none / % / fixed) patching on change, plus a "reserved for emergencies" total in the page header, and each category group shows a balance rollup (e.g. Debts = sum of all debt; per-currency). Additive contract change only.
- [x] Implement + tests (mutual exclusion 400; reservation math incl. cap-at-balance; net-worth tool exposes and advisor prompt forbids spending it; UI patch + group rollups) + deploy + commit. Verified: 259 api + 68 web tests pass; migration `0034` applied on the live deployment with the exclusivity CHECK in place.

## M37: Bills Page (Missing UI for an Existing API)

The `bills` API (list/create/update/delete) shipped in **M9** and `api.service.ts` has wrapped it since then, but no Angular page or nav entry was ever built — M11's page sweep covered Transactions/Imports/Reports/Backups/Accounts/Users but missed Bills. The practical symptom: with no way to enter recurring monthly expenses, the Overview page's "Emergency fund" card always shows "Not enough data" (`emergency_fund_months` needs a monthly-bills denominator) — reported live by a user who had correctly set an emergency-fund designation (M36) but had no bills to divide it by.

- [x] Spec gate: add a `/bills` page (list + create form: name, amount, frequency, optional next due date; delete) mirroring the existing Goals page pattern; add the nav entry and route. No API/contract changes — every endpoint this needs already exists and is already wrapped in `ApiService`.
- [x] Implement + tests (page renders bills, create round-trips, delete removes, role-gated for viewers) + deploy + commit. Verified: 72 web tests pass; live reproduction — deleting the demo household's bills flips `emergency_fund_months` from `0.96` to `None` ("Not enough data"), and re-adding them via the new page's exact API path restores `0.96`.

## M38: Overview Dashboard Enrichment (Emergency-Fund Target + Financial Summary)

The Overview page shows only three numbers (household name, net worth, emergency-fund months), and the emergency-fund figure answers "how long would it last" without saying whether that is good or what closing the gap costs. User request: show the target comparison and enrich the overview.

- [x] Spec gate: (a) contract — `HouseholdContext` gains additive fields: `emergency_fund` (months, the fund balance used [`reserved`], `using_designations` flag, `monthly_expenses`, `target_months_min` = 3 / `target_months_recommended` = 6 [standard guidance, constants in the payload so the UI never hardcodes them], `gap_to_recommended` Money [0 when funded], `status` enum `no_bills|no_fund|getting_started|on_track|fully_funded`), `monthly_cash_flow` (income, bills, net — recurring sources normalized monthly; discretionary spending intentionally excluded, matching the M2 engine assumption), `asset_breakdown` (ordered category totals reusing the M33 spendability categories, moved to `finance_service` so ai_tools and the API share one map), and `total_debt` (positive sum of negative balances). Legacy `emergency_fund_months` kept for compatibility. (b) UI — Overview becomes a card grid: net worth, emergency fund (months + status label + reserved amount + dollar gap to the 6-month recommendation, with actionable empty states linking to Bills/Accounts), monthly cash flow (income − bills = net, negative highlighted), assets by category, and total debt.
- [x] Implement + tests (context fields incl. status/gap math across no-bills/no-fund/funded cases; overview renders target gap and empty states) + deploy + commit. Verified: 263 api + 73 web tests pass; live deployment returns the full enriched payload (status `getting_started`, USD 10,480.00 gap for the demo household's $2,000 designation).

## M39: Upcoming Bills (surface `next_due_date`, add to Overview)

Bills carry a `next_due_date` and the `Bill` schema declares the field, but the bills `_to_schema` never populates it — so the due date is invisible everywhere, and there is no "what's due soon" view. This is the top backlog item from M38.

- [x] Spec gate: (a) fix — `RecurringRecord` gains `next_due_date`, `list_bills` maps it, and the bills `_to_schema` returns it (closing the latent drop); the Bills create form gains an optional due-date input and the list shows each bill's next due date. (b) roll-forward — a pure `next_bill_occurrence(next_due_date, frequency, today)` helper advances a stored due date to the next occurrence on/after today (day-based for weekly/biweekly/semimonthly, calendar-month arithmetic with end-of-month clamping for monthly/quarterly/annual), so stale dates never show as overdue. (c) Overview — `HouseholdContext` gains additive `upcoming_bills`: bills whose next occurrence falls within the next 14 days, sorted ascending, each with `id`, `name`, `amount`, `due_date`, `days_until`; a new Overview card lists them (with a "nothing due" empty state). No schema migration — every column already exists.
- [x] Implement + tests (roll-forward across frequencies incl. end-of-month + already-future dates; `_to_schema` round-trips the due date; upcoming window includes/excludes correctly; Overview renders the card) + deploy + commit. Verified: 268 api + 73 web tests pass; live deployment round-trips a due date (previously always null) and lists it under `upcoming_bills` with the correct `days_until`.

## M40: Net-Worth History (snapshots + Overview sparkline)

Net worth is only shown as today's number; there's no trend. This is the next backlog item — persist a periodic snapshot and visualize the trajectory.

- [x] Spec gate: (a) schema — a `net_worth_snapshots` table (`id`, `household_id` FK, `as_of` DATE, `net_worth_minor` BIGINT, `currency`, `created_at`), with a unique `(household_id, as_of)` so at most one snapshot per household per day; migration `0035`. (b) capture — a `net_worth_history.record_snapshot_once(engine)` that iterates households, computes net worth, and **upserts** today's row (idempotent — re-running the same day overwrites, not appends); wired into the worker as a daily job **and** run once at worker startup so history begins immediately. (c) read — `repository.list_net_worth_snapshots(household_id, limit)` (most recent N, returned oldest-first for charting); `HouseholdContext` gains additive `net_worth_history` (last 30 snapshots as `{as_of, net_worth}`). (d) UI — the Overview net-worth card renders an inline SVG sparkline of the history plus the change since the earliest point shown; a single/no-point history renders no sparkline gracefully.
- [x] Implement + tests (upsert idempotency same-day; multi-day ordering oldest-first; snapshot job persists per household; context returns the series; Overview renders a sparkline path) + deploy + commit. Verified: 273 api + 73 web tests pass; live deploy applied migration `0035`, the worker captured a snapshot per household at startup, re-running left exactly one row per day, and the context returns the series oldest-first (seeded 3-day demo trend shows a +$70,000 change).

## M41: Goal Progress on the Overview

Goals exist (create/list, priority-ordered) but never appear on the Overview. Surface the highest-priority goal with a progress bar — the next backlog item.

- [x] Spec gate: (a) read — `HouseholdContext` gains additive `top_goal` (nullable): the highest-priority goal (`list_goals` already orders by priority then name, so it's the first) as `GoalProgress { id, name, type, current, target, percent_complete, target_date }`, where `percent_complete` is `min(100, round(current/target*100))` with a zero-target guard (0). No migration — goals already persist. (b) UI — an Overview card shows the goal's name and type, a progress bar filled to `percent_complete`, "current of target", the percent, and the target date when set; a "no goals yet" empty state links to the Goals page. Contract addition only.
- [x] Implement + tests (top-of-priority selection; percent math incl. zero-target and over-100 capping; context returns it; Overview renders the bar + empty state) + deploy + commit. Verified: 276 api + 73 web tests pass; live deployment returns the demo household's priority-1 Emergency fund goal at 83%.

## M42: Spending Insights on the Overview

Transactions carry signed amounts, dates, and merchants, but the Overview shows nothing about spending. Add a month-to-date spending summary vs the same period last month, plus the top merchants — the next backlog item.

- [x] Spec gate: (a) read — two generic repository aggregates over `transactions` in the base currency: `sum_spending(household_id, start, end)` (positive total of outflows — the absolute value of negative `amount_minor`; income excluded) and `top_spending_merchants(household_id, start, end, limit)` (grouped by merchant, `NULL`→"Other", descending). (b) fair comparison — the endpoint compares **month-to-date** (1st→today) against the **same day range of last month** (1st→same day, clamped to the prior month's length), not partial-vs-full, so early-month numbers aren't misleading. (c) `HouseholdContext` gains additive `spending_insights` (nullable): `{ this_month, last_month, change_percent (null when last_month is 0), top_merchants: [{ merchant, amount }] (top 5) }`. (d) UI — an Overview card shows this-month spending, the % change vs last month (red when up, green when down — spending less is good), and the top merchants with amounts. No migration.
- [x] Implement + tests (spending sum excludes income and sums outflows; MTD vs same-period window math incl. month-length clamp; top-merchants ordering + NULL grouping; change_percent zero-guard; Overview renders) + deploy + commit. Verified: 281 api + 73 web tests pass; live deployment returns the demo household's month-to-date spending ($175 across Whole Foods + Trader Joe's) with a null change (no prior-period spending).

## M43: Configurable Emergency-Fund Target

The 3/6-month emergency-fund guidance (M38) is hardcoded. Let each household set its own target — some want 3 months, others 12.

- [x] Spec gate: (a) schema — `households.emergency_fund_target_months` (nullable Float; `NULL` means "use the default 6"), migration `0036`. (b) API — a new `PATCH /household` (owner/adult) accepting `emergency_fund_target_months` (1–60, or `null` to reset to default); audited. `get_household`/`HouseholdRecord` carry the value. (c) summary — `_emergency_fund_summary` uses the household's target as `target_months_recommended` (default 6 when unset); the "getting started → on track" threshold becomes `min(3, target)` so a sub-3-month target still makes sense; `gap_to_recommended` and `status` compute against the configured target. (d) UI — the Overview emergency-fund card gains an inline target editor (owner/adult only): a number input + Save that PATCHes and reloads. No behavior change for households that never set one.
- [x] Implement + tests (target persisted + returned; summary/gap/status recompute against a custom target incl. sub-3 threshold; PATCH validation bounds + null reset; role-gated; Overview editor saves) + deploy + commit. Verified: 287 api + 75 web tests pass; live deploy applied migration `0036`, PATCH target=3 recomputed the gap to $4,240 (3×$2,080 bills − $2,000 reserved) and status getting_started, and clear reset to 6.

## M44: Savings-Rate Metric

The Overview shows monthly net cash flow in dollars but not as a rate. Add a recognizable savings-rate percentage over the trailing 3 months — the last of the lighter backlog items.

- [x] Spec gate: (a) definition — savings rate = `(monthly_income − average_monthly_spending) / monthly_income`, where `monthly_income` is the recurring monthly income (M38) and `average_monthly_spending` is actual outflow (M42 `sum_spending`) over the **last 3 complete calendar months** ÷ 3 (the current partial month is excluded for stability). Can be negative when spending exceeds income; null when income is 0. This intentionally pairs recurring income with actual tracked spending — documented as the metric's basis. (b) `HouseholdContext` gains additive `savings_rate` (nullable): `{ percent, monthly_income, average_monthly_spending }`. (c) UI — the Overview cash-flow card shows the savings-rate percent (green when positive, red when negative) with the trailing-3-month average spending as context. No migration.
- [x] Implement + tests (3-complete-month window excludes the current month; average = total/3; percent sign + zero-income null; context returns it; Overview renders) + deploy + commit. Verified: 290 api + 75 web tests pass; live deploy returns the demo household's 100% rate ($6,000 income, $0 avg over the last 3 complete months — its transactions fall in the excluded current month).

## M45: Category Management (prerequisite for budgets)

Budget envelopes (M46) attach to spending categories, but today there is no way to create categories or assign them: the `transaction_categories` table exists and reads expose a category **name**, yet create/update transaction requests have no category field and `create_transaction` hardcodes `category_id=None`. This milestone adds lightweight category tooling (per the product decision on 2026-07-09).

- [x] Spec gate: (a) schema — a unique `(household_id, name)` index on `transaction_categories` so names don't duplicate; migration `0037`. (No new table — it already exists, household-scoped with an optional `parent_category_id` we leave unused for now, i.e. flat categories.) (b) API — `GET/POST/DELETE /categories` (create/delete owner/adult, audited; delete nulls the `category_id` on any transactions referencing it rather than failing); `POST/PATCH /transactions` gain an optional `category_id` (validated to belong to the household, else 404), and the `Transaction` response gains `category_id` alongside the existing `category` name. (c) UI — a `Categories` page (list + create + delete, mirroring the Bills page) and a category `<select>` on the Transactions create form and inline per-row on the list. Categories are flat and household-scoped; a brand-new household starts empty (no auto-seed) — users add what they need.
- [x] Implement + tests (category CRUD + household scoping; duplicate-name 409; transaction create/update sets + validates category; delete nulls references; Transactions/Categories pages) + deploy + commit. Verified: 296 api + 78 web tests pass; live deploy applied migration `0037`, and the full flow worked — create category, duplicate 409, assign on transaction create, delete category leaves the transaction uncategorized.

## M46: Budget Envelopes (monthly, per-category, threshold alerts)

With categories in place (M45), add monthly budget envelopes. Product decisions (2026-07-09): monthly periods, soft tracking (a recording app can't block spend) with approaching-limit warnings, no rollover.

- [x] Spec gate: (a) schema — a `budgets` table (`id`, `household_id`, `category_id` FK, `limit_minor`, `currency`, `created_at`, `updated_at`) with a unique `(household_id, category_id)` (one envelope per category); migration `0038`. (b) API — `GET/POST/PATCH/DELETE /budgets`; the list computes each envelope's **current calendar-month** spend (reuse M42 category-scoped `sum_spending`), `remaining`, `percent_used` (capped display but raw value drives status), and `status` (`under` / `warning` at ≥80% / `over` at >100%). (c) `HouseholdContext` gains additive `budget_summary` (nullable): counts of over/warning envelopes + total budgeted vs spent, for an Overview alert card. (d) UI — a `Budgets` page (per-category limit CRUD with spent/limit progress bars, colored by status) and an Overview summary card that surfaces over/at-risk envelopes.
- [x] Implement + tests (per-category month spend vs limit; status thresholds 80/100; summary counts; CRUD + one-per-category 409; category delete removes its budget; Budgets page + Overview card) + deploy + commit. Verified: 302 api + 88 web tests pass; live deploy applied migration `0038`, and the demo household's real $175 categorized Groceries spend against a $200 limit reads 88% → warning, mirrored in the Overview summary.

## M47: AI Runtime Page Redesign (user-reported UX fixes)

User-reported problems (2026-07-10): the HF search input is small and triggers iPhone focus-zoom (its 0.9rem font overrides the global 16px iOS fix); the picker renders every model as a large card with no filtering ("too many items"); a model applied from HF search becomes **invisible after reload** — `selectedMain` only resolves against curated + current-search results, so the whole fit/apply section silently disappears; and the Advanced section only exposes the main model even when a main+vision combination is active (the vision model is deployment-level, changeable only via the swap path).

- [x] Spec gate (all frontend; no contract change): (a) **active/selected always visible first** — a "Now serving" card (served main + vision from status) and a pinned "Your selection" summary; selections resolve against curated + search results **+ synthesized stub entries** for any active/selected id not otherwise loaded (specs estimated from the name, marked as such) — fixes the invisibility bug. (b) **filterable browse list** — one unified list replacing the two grids: full-width ≥16px search input (kills iOS zoom), quick-filter chips (✨ Recommended for this server = main-role models that fit the memory budget, strongest first; 📷 Biggest vision; 📷 Smallest vision; 🏦 Best for finances = tool-calling-capable mains, strongest first, fit-flagged; All), facet controls (role, "only models that fit", sort by size/memory), compact rows with a per-model fit badge and a role-aware Select button, capped at 6 rows with "Show more". Quick filters operate on curated + loaded search results (offline-first; search pulls more from HF). (c) **Advanced section shows the combination** — read-only "currently serving" main+vision line, the existing raw config form (main), plus a vision-model mini-form that applies via the swap endpoint with an explicit "restarts the vision container" warning (vLLM provider only, matching ADR 0013 semantics).
- [x] Implement + tests (stub synthesis keeps an off-catalog active model visible; quick filters select/sort correctly incl. fit gating; role-aware select; vision apply from Advanced posts the swap; 16px input) + deploy + commit. Verified: 84 web tests pass (6 new; all 9 pre-existing runtime tests kept green), build clean, patched deployment serves the new chunk (quick-filter markup + 16px rule confirmed in the served assets).

## M48: AI Runtime — Live Filtered Catalog + Expandable Rows (feedback on M47)

User feedback (2026-07-10): the pinned "Your selection" block shouldn't sit above the list, quick filters must fetch a **live filtered list from the Hugging Face catalog** (M47 only filtered already-loaded models), and tapping a model should **expand it in place** with fit details and an Apply button — making the separate selection section redundant.

- [x] Spec gate: (a) API — `GET /ai/models/search` loosens `q` to optional and gains optional `pipeline` (`text-generation` | `image-text-to-text` | `any`, default any) and `limit` (1–30, default 10); an empty `q` returns the pipeline's most-downloaded models, which is exactly what live quick-filter lists need. Additive contract change. (b) quick filters go live — Recommended fetches top text-generation models (then fit-filters, strongest first, curated merged in); Best-for-finances fetches `q=finance` text-generation (+ curated tool-capable mains); Biggest/Smallest vision fetch image-text-to-text sorted by size; every filter falls back to curated-only with the existing unreachable note. (c) UI — the "Your selection" section (chips, combined fit box, Apply/Save buttons, swap hint, mismatch note) is **removed**; "Now serving" stays first with the live apply status under it. Tapping a row expands an inline detail panel: full id, estimated specs, memory/disk fit verdicts **for what would actually be served** (a photo-blind main keeps the current photo model in the total; a vision-capable main replaces both; a vision row pairs with the current chat model), gated/notes, and an "Apply — download & switch" button that posts the swap directly. Save-only moves out of the main flow (raw config remains in Advanced).
- [x] Implement + tests (search endpoint pipeline/limit/optional-q; live quick-filter fetch wiring; combined fit math per row role; apply payload preserves the current counterpart model; expansion toggle; curated fallback when HF is unreachable) + deploy + commit. Verified: 304 api + 87 web tests pass; live deploy returns real most-downloaded HF lists for both pipelines through the extended endpoint.

## M49: Honest "Biggest Vision That Fits" (estimate + pool + gating fixes)

User question (2026-07-10): "why is this the biggest vision model my computer can handle if it's only 85 GB required?" Three real flaws: the vision quick filters draw from HF's top-20 *most-downloaded* models (popularity ≠ size, so genuinely large models never enter the pool); the filters don't fit-gate, so "biggest" was never "biggest my computer can handle"; and size estimates assume bf16 (2 bytes/param) even for names carrying quantization markers — the screenshot's FP8 model was estimated at double its real memory.

- [x] Spec gate: (a) quant-aware estimates — `_estimate_from_hf` (and the dashboard's stub synthesizer) detect quant markers in the name (`fp8`/`int8` → ~1.1 GB/B, `awq`/`gptq`/`int4`/`4bit` → ~0.65 GB/B, else bf16 2.1 GB/B; disk scaled likewise) and say so in the notes. (b) bigger pool — the vision-big filter fans out parallel live fetches (top downloads + size-hinted queries `72B` and `vision`) merged and deduped, so large models that aren't download-chart leaders still appear; curated gains `Qwen2.5-VL-72B-Instruct` (bf16, won't fit ~120 GB boxes — honest ❌) and `Qwen2.5-VL-72B-Instruct-AWQ` (~45 GB, the true "biggest vision that fits" on GB10-class hardware). (c) fit-gating — both vision quick filters drop models whose fit verdict is ❌ and the chip reads "Biggest vision that fits"; the role facet still shows everything.
- [x] Implement + tests (quant estimate table; fan-out merge; fit-gated vision filters; curated 72B entries listed) + deploy + commit. Verified: 306 api + 90 web tests pass; live deploy estimates FP8 models at 8-bit rates, lists both curated 72B vision entries (145 GB bf16 / 45 GB AWQ), and the size-hinted vision fetch returns genuinely large models (VL-72B, NVLM-D-72B) absent from the download charts.

## M50: Real Loading Status (+ fix the VL-72B crash loop)

User report (2026-07-10): "loading" for 10+ minutes with no detail. Diagnosis: vLLM was **crash-looping**, not loading — Qwen2.5-VL-72B ships a 128k default context needing ~39 GiB of KV cache when only ~12.6 GiB remained after the weights; the status pipeline had no way to say so.

- [x] Spec gate: (a) deploy fix — the main vLLM service gains `--max-model-len ${VLLM_MAX_MODEL_LEN:-32768}` (32k is ample for chat + tools and keeps KV under ~10 GiB; models shipping 128k defaults no longer crash-loop). (b) model-manager (the docker-socket holder) gains `GET /logs?service=vllm|vllm-vision&tail=N` — allowlisted service names only, read-only `docker compose logs`. (c) the API's `GET /ai/runtime/status`, when vLLM isn't ready and the manager is reachable, fetches the log tail and classifies it into additive `loading_phase` (`downloading` / `loading` / `warming_up` / `error` / `starting`) + human `loading_detail` (download %, shard-load %, or the trimmed crash line — e.g. today's KV-cache ValueError would have read as an error instead of "loading"). Pure, unit-tested classifier. (d) UI — the Now-serving chat row shows the phase detail while loading and turns red with the error line on a crash.
- [x] Implement + tests (log classifier incl. the real KV-cache error; manager endpoint allowlist; status wiring with manager mocked; UI renders detail + error) + deploy + commit. Verified: 311 api + 7 model-manager + 90 web tests pass; live — the crash-looping VL-72B-AWQ started cleanly with the 32k cap, the status reported "Loading weights into memory — 100%" during the load, and the model now answers chat (answered_by confirms).

## M51: VL Mains Can't Call Tools (catalog honesty + split-role apply)

User report (2026-07-10): the chat "doesn't have my details" after applying Qwen2.5-VL-72B-AWQ as the chat model. Root cause verified by a direct probe: **Qwen2.5-VL's chat template does not render tool definitions** — the model never sees the financial tools, so it asks the user for numbers it should fetch itself (the grounding guardrail correctly blocks anything it invents). The catalog listed VL mains with `tool_parser="hermes"`, misrepresenting them as agentic-capable.

- [x] Spec gate: (a) live remediation — the deployment is reconfigured to the split that gives both strengths: `Qwen2.5-32B-Instruct-AWQ` main (proven tool caller, GPU fraction 0.35) + `Qwen2.5-VL-72B-Instruct-AWQ` as the photo describer (fraction 0.45). (b) catalog honesty — the Qwen2.5-VL "both"-role entries get `tool_parser=None` and a note ("cannot call the financial tools; best as the photo model"); the 🏦 finance filter (already tool-parser-gated) now correctly excludes them. (c) UI — the expanded row warns when a model that can't call tools would be applied as the chat model ("answers fall back to deterministic snapshots"), and vision-capable models gain a second button, "Apply as photo model only", which pairs them with the current chat model. GPU-fraction management stays manual (.env) — dynamic budgeting goes to the backlog.
- [x] Implement + tests (catalog flags; warning rendered; apply-as-photo payload keeps the current main) + deploy + commit. Verified: 311 api + 91 web tests pass; live — 32B-AWQ chat + VL-72B-AWQ photos both serving, and the exact reported question ("Can I afford a $1,000 phone?") now answers from the household's own data with tool grounding and answered_by attribution.
- [x] Follow-up (user audit request): never run two instances of the same model. The apply endpoint collapses identical main+vision ids to the single-instance path (vision dropped; the swap script's vision-capable branch serves ONE container); `applyAsVision` on the current chat model routes to the plain apply; the Advanced vision note states the same-model case runs one container. Verified: 312 api tests pass; live audit confirms two containers serve two DIFFERENT models (32B-AWQ ≈44 GB at fraction 0.35, VL-72B-AWQ ≈57 GB at 0.45).

## M52: Capability-Aware Recommendations + Guided Chat→Photo Pairing

User feedback (2026-07-10): "Recommended for this server" surfaced a vision model that can't call tools as the top *chat* suggestion. Recommendations must be capability-aware, and the flow should mirror how the stack actually works: pick a tool-calling chat model first, then pick a photo model — with a dedicated filter for the rare model that genuinely does both.

- [x] Spec gate (frontend-only): (a) **✨ Recommended chat models** — gains a tool-calling gate: `(main|both role) AND tool_parser AND fits`, strongest first (tool-less VL models can no longer top the chat recommendation). (b) **🪄 One model for both** — a new quick filter for dual-capable models (`supports_vision AND tool_parser AND fits`); with today's catalog it is honestly empty, showing "No available model reliably does both chat (tool calling) and photos — pick a chat model first, then a photo model." (future dual models appear automatically). (c) **guided pairing** — the expanded panel of a photo-blind chat model gains "Next: pick a photo model →" (jumps to the Biggest-vision-that-fits filter), and the Now-serving card nudges with the same action when the chat model is active but no photo model is serving. No backend change.
- [x] Implement + tests (recommended excludes tool-less mains; all-in-one includes a dual-capable model and shows the honest empty state otherwise; next-step button switches filter; now-serving nudge) + deploy + commit. Verified: 93 web tests pass; the served bundle contains the new chips (an interim patch had silently shipped a stale bundle because the scss crossed the 8 KB error budget — dead styles from the M48 redesign were removed to fix the build).
- [x] Follow-up (user challenge: "are you sure no model does both?"): verified against the repos' chat-template files that **Qwen3-VL renders tools** (Qwen2.5-VL confirmed not to) — the "no dual model" claim held only for the 2.5 generation. Curated catalog gains three verified all-in-one entries (Qwen3-VL-30B-A3B-FP8 ~33 GB MoE/fast, Qwen3-VL-32B-FP8 ~35 GB dense, Qwen3-VL-8B ~17 GB); the 🪄 filter now has real content. Verified: 313 api tests pass; live catalog lists all three as vision+tools.

## M53: Optimal-for-This-Server Deep Search

User feedback (2026-07-10): the "recommended" pool is HF's top-20-by-downloads plus a couple of fixed size hints — big-but-unpopular models never enter it, so the picker under-recommends relative to what the hardware fits (a ~122 GB box can serve ~95B in FP8 / ~160B-class in 4-bit). HF's API cannot sort by size, so an "optimal" recommendation must fan out deliberately.

- [x] Spec gate: (a) API — `GET /ai/models/search` gains `deep=true`: a threaded fan-out per pipeline over quantization- and size-hinted queries (`""`, `AWQ`, `FP8`, `70B`, `72B`, `90B`, `110B`, `A22B`, plus the user's `q` when given), merged/deduped, capped; per-request timeout keeps worst-case wall time to roughly one timeout. Additive param. (b) UI — the Recommended-chat, All-in-one, and Biggest-vision filters use `deep=true`; fitting models rank by parameters desc with a bytes-per-param tiebreak (higher precision wins at equal size). (c) honesty — MoE models (fast on bandwidth-limited unified memory) are already noted in the catalog; the deep pool lets the truly-largest fitting models surface instead of the merely-popular.
- [x] Implement + tests (deep fan-out issues the hinted queries; dedupe; cap; UI passes deep + tiebreak ordering) + deploy + verify + commit. Verified: 314 api + 93 web tests pass; the live deep search now surfaces the box's true ceiling — e.g. Qwen3-Next-80B-A3B-FP8 (~88 GB, tools template verified) and Qwen2.5-72B-AWQ (~47 GB) for chat, Qwen2.5-VL-72B variants for vision — none of which the top-downloads pool ever showed. Known limit: ranking is size-within-pool; it cannot judge model GENERATION (an old 110B ranks above a modern 72B), noted for the user.

## M54: Recommendation Quality (servable formats, legacy demotion, missing 72B)

User question (2026-07-10): "then why isn't [Qwen2.5-72B-AWQ] on the recommendation list?" Three defects: the curated catalog lacked the AWQ text 72B (only the unfitting bf16); the deep pool includes formats vLLM cannot serve (MLX, GGUF, bitsandbytes 4-bit, EXL2, ONNX) which crowd the top; and size ranking is generation-blind (a 2024-era Qwen1.5-110B outranked a modern 72B).

- [x] Spec gate: (a) curated — add `Qwen/Qwen2.5-72B-Instruct-AWQ` (~47 GB, hermes; "the strongest proven tool-calling chat model for ~120 GB boxes"). (b) server — `_estimate_from_hf` drops repos whose names carry non-vLLM format markers (`gguf`, `mlx`, `bnb`, `exl2`, `onnx`, `openvino`) so unservable models never enter the pool. (c) client — the Recommended/All-in-one/Finance filters exclude known-legacy families (Qwen1.5, Llama-2, Falcon, Vicuna, MPT, GPT-2 era) from the ranked list ("All" still shows everything); generation-awareness beyond a blocklist stays backlog.
- [x] Implement + tests (format markers dropped server-side; legacy families excluded from recommended; catalog lists the text 72B-AWQ) + deploy + live-verify + commit. Verified: 316 api + 94 web tests pass; the live recommended top now reads Llama-3.2-90B-FP8 → Qwen3-Next-80B-A3B-FP8 → Qwen2.5-72B-AWQ — modern, vLLM-servable, tool-capable, fitting; the MLX/GGUF/bnb junk and the 2024-era Qwen1.5-110B are gone.

## M55: Automatic GPU-Fraction Budgeting in the Swap

Third fraction-related failure (2026-07-11): applying Qwen3-Next-80B-FP8 (76.4 GiB checkpoint) crash-looped because `VLLM_GPU_FRACTION` was still 0.35 (~43 GB) from the 32B era. Manual fraction management does not survive model changes.

- [x] Spec gate: `scripts/swap-model.sh` estimates each applied model's weights from its name (params × quant factor — same heuristics as the API: awq/gptq ≈0.65 GB/B, fp8/int8 ≈1.1, bf16 ≈2.1), adds runtime reserves (main +12 GB for 32k KV/overhead, vision +6 GB), converts to fractions of total memory (/proc/meminfo), writes `VLLM_GPU_FRACTION`/`VLLM_VISION_GPU_FRACTION` into `.env`, and **dies with a clear won't-fit message before recreating anything** when the combination exceeds 92% of memory. Unknown sizes (no params in the name) keep the existing fractions with a warning. Live remediation: re-apply the user's chosen Qwen3-Next-80B chat with the VL-7B describer (76+16 GB + reserves fits ~122 GB; the previously-paired VL-72B does not fit beside an 80B and the script now says so instead of crash-looping).
- [x] Implement + verify + commit. Verified live: the impossible combo (80B + VL-72B) is refused pre-swap with a clear message; the fitting combo (80B + VL-3B) auto-computed fractions 0.75/0.10 and applied. A second failure surfaced during the load — DeepGEMM's FP8 block-quant path asserts ("Unknown SF transformation") on this hardware — fixed by defaulting `VLLM_USE_DEEP_GEMM=0` in compose (env-overridable). End state: Qwen3-Next-80B-A3B-FP8 chat + VL-3B photos both serving; a tool-grounded affordability answer (real emergency-fund months from the household's data, answered_by attributed) completed in 23 s. FP8 estimate factor tuned 1.1→1.0 (real checkpoint measures ~0.96 GB/B).

## M56: Grounding Guardrail Tolerance for Honest Derivations (+ one corrective retry)

User screenshot (2026-07-10): with a healthy 80B serving, every real-household chat answer fell back to the deterministic stub. API logs show the model answered each time but the guardrail rejected honest phrasings as "ungrounded": `974000` (net worth $979,278.48 rounded to thousands), `6` ("3–6 months of expenses" guidance), `0.1` ("$1,000 ≈ 0.1% of net worth" — arithmetic on two grounded figures). The strict verbatim string match (plus 0–2-decimal roundings from M31) cannot see rounding to significant figures, small conversational numbers, or derived ratios — so the guardrail fails closed on essentially every naturally-phrased answer, making the model useless in chat.

- [x] Spec gate: relax `find_unattributed_numbers` (ai-orchestrator `guardrails.py`, shared by chat + purchase/report explanations) with three principled exemptions, keeping fail-closed for material invented figures: (a) **immaterial numbers** — absolute value < 100 is never a violation (months, counts, percentages, ratios; the ADR 0003 harm model is fabricated *money* figures); (b) **year-like integers** 1900–2100 exempt; (c) **relative tolerance** — a number ≥ 100 passes when within ±1% of any grounded value (covers rounding to thousands/significant figures; a false accept is bounded to a ≤1% error on a real figure, not a fabrication). A genuinely invented figure (e.g. "$45,000 loan" with no nearby grounded value) still fails. Additionally, chat gets **one corrective retry**: on guardrail failure the loop re-runs once, appending the violating answer and an instruction to restate using only tool-derived figures (or call a tool to compute them); only if the retry also violates does the answer fall back to the deterministic snapshot. Guardrail stays string/number-based — no semantic validation.
- [x] Implement + tests (small/year/tolerance exemptions; a material invented figure still fails; chat retry path: second loop run on violation, fallback only after both fail) + deploy + live verify (the previously-falling-back phone question must return an AI answer with `answered_by` set) + commit. Verified: 28 orchestrator + 317 api tests pass; live, a net-worth/percentage question full of derived phrasing (rounded "-$2.98M", "0.26 months", "25%", "5%") answered by the 80B in 7 s, `source=agentic_tool_calling`, no retry needed.

## Backlog: Dashboard Feature Ideas (proposed 2026-07-09)

Candidate features surfaced while enriching the overview; each needs its own spec gate before implementation:

- [x] Configurable emergency-fund target (per-household `target_months`, replacing the fixed 3/6 guidance). — delivered by M43.
- [x] Spending insights on Overview: this month's discretionary spending vs last month, top merchants/categories (transactions data already exists). — delivered by M42.
- [x] Net-worth history sparkline (persist a periodic net-worth snapshot; scheduler exists). — delivered by M40.
- [x] Upcoming bills calendar (bills have `next_due_date`; surface "due this week" on Overview). — delivered by M39.
- [x] Goal progress on Overview (goals API exists; show top-priority goal with a progress bar). — delivered by M41.
- [x] Budget envelopes per category with monthly limits + alerts — delivered by M45 (categories) + M46 (budgets).
- [x] Savings-rate metric (income − all spending, trailing 3 months). — delivered by M44.

## Backlog: Debt Payoff and Retirement Projections

The PRD (`docs/specs/01-prd.md`) promises "deterministic projections for cash flow, retirement, debt payoff, net worth, and savings goals" and a Scenario Planning journey ("Can we retire at 55?", "Should we refinance?"). Mostly owned by **M14** (`docs/specs/11-milestone-roadmap.md`); the open-ended scenario API remains backlog.

- [x] Add a deterministic `calculate_debt_payoff` calculation to the financial engine, unit tested with mocked `DebtInput` values (balance, annual interest rate, minimum payment, optional extra payment). This does not require database changes — it is a pure function like every other engine calculation.
- [x] Add `annual_interest_rate` and `minimum_payment` columns to `accounts` for liability account types, with a migration. (M14, migration `0029`.)
- [x] Wire `calculate_debt_payoff` into the purchase advisor's `debt` impact once account-level interest/payment data is persisted, replacing today's warning-only placeholder. (M14.)
- [x] Add a `calculate_retirement_projection` calculation to the financial engine. (M14.)
- [x] Open-ended scenario questions ("should we refinance?", "how many years of retirement does this purchase cost me?") are **not** a per-question API — that does not scale. The accepted direction is **agentic tool-calling** ([ADR 0009](../adr/0009-agentic-tool-calling.md)), delivered by **M16**: the local model orchestrates the deterministic engine calculations (exposed as tools) and narrates grounded results.

## Backlog: Annual Report

The PRD (`docs/specs/01-prd.md`) lists "weekly, monthly, and annual reports" as a functional requirement, but the M8 roadmap bullets (`docs/specs/11-milestone-roadmap.md`) name only weekly and monthly, so M8's spec gate scoped annual out rather than silently dropping it. No milestone currently owns it.

- [x] Add an `annual` `report_type` to the M8 `reports` table/generation service (same wins/risks/unusual-spending/goal-progress/recommended-actions shape, year-scoped instead of week/month-scoped). (M15.)
- [x] Add a scheduled annual report job (e.g. January 1st) once the above lands. (M15.)
- [x] Assign to a specific milestone. (M15.)

## Backlog: Deferred Follow-Ups from the Post-M8 Audit

These were explicitly deferred (not dropped) by M9/M10/M11 non-goals. Tracked here so nothing is undocumented.

- [x] Extend `audit_events` coverage to all pre-existing sensitive mutation points. (Delivered by M32.)
- [x] Add a dashboard **chat UI** page backed by the M6/M10 chat and conversation-history APIs. (Delivered by M19; upgraded through M21/M25/M26.)
- [x] Add a first-run **household setup wizard** UI around `POST /api/v1/households`. (Delivered by M19 as the /signup page.)
- [x] Add optional first-run bootstrap lockout. (Delivered by M32 — locked by default with FAMILY_CFO_ALLOW_MULTIPLE_HOUSEHOLDS opt-out.)
- [x] Feed prior conversation turns back into the model as context (true multi-turn memory). (Delivered by M30: bounded history window + grounded history numbers.)

## Backlog: Vector Store and Retrieval

The Docker spec (`docs/specs/10-docker-spec.md`) plans a `family-cfo-vector` (Qdrant) container, ADR 0007 lists "Vector database" as a replaceable component, and the AI-orchestration spec names "relevant retrieved context" as a reasoning input — but no milestone ever introduced a vector store, embeddings, or retrieval. The AI orchestrator calls the runtime directly with calculation context and no retrieval step, and M8 explicitly non-goaled Qdrant backup because no vector data exists. Tracked here until scoped into a milestone.

- [ ] Decide whether retrieval-augmented context is in scope for v1 or deferred post-release (it has no consumer until real multi-turn chat exists).
- [ ] If in scope: add a `VectorStoreAdapter` seam (ADR 0007), a Qdrant-backed implementation, an embedding step, and wire retrieved context into the AI orchestration path.
- [ ] Add encrypted Qdrant backup to M8's backup pipeline once vector data exists (M8 non-goaled it precisely because it does not yet).
- [ ] Assign to a specific milestone.

## Release Readiness

### Security and Privacy (M13)

- [x] Resolve threat model open question for database encryption. (ADR 0008: host/volume encryption, not app-layer.)
- [x] Resolve threat model open question for local certificate provisioning. (ADR 0008: self-signed default + bring-your-own / external proxy.)
- [x] Resolve threat model open question for backup key recovery. (ADR 0008: operator-managed, no recovery by design.)
- [x] Decide whether optional external AI providers are allowed only through a future explicit opt-in ADR. (ADR 0008: local-only default; cloud only via a future superseding ADR.)
- [x] Add HTTPS configuration for app-to-server communication. (nginx TLS on 443, HTTP→HTTPS redirect, security headers, self-signed cert bootstrap; verified `curl -k` end-to-end.)
- [x] Add token rotation. (`POST /api/v1/auth/sessions/refresh` — revokes the old token, issues a new one.)
- [x] Add session expiration enforcement. (Already enforced by `get_session_context`; `FAMILY_CFO_SESSION_TTL_HOURS` made configurable and an explicit expiry test added.)
- [x] Add role-based authorization tests. (`test_security.py` viewer/adult/owner matrix.)
- [x] Add paired device revocation tests. (`test_pairing_api.py`; referenced from `test_security.py`.)
- [x] Add structured logging redaction tests. (`test_logging.py` + `test_security.py` through the handler.)
- [x] Add no-telemetry verification. (`test_security.py` first-party-source scan for telemetry/analytics SDKs.)
- [x] Add secret scanning to CI. (`.github/workflows/security.yml` — gitleaks.)
- [x] Add dependency vulnerability checks. (`.github/workflows/security.yml` — pip-audit.)

### Docker and Deployment (M12)

- [x] Add Dockerfile for API.
- [x] Add Dockerfile for Angular dashboard.
- [x] Add Dockerfile for worker. (Shares `docker/api.Dockerfile` with a second entrypoint.)
- [x] Add Docker Compose service for web.
- [x] Add Docker Compose service for API.
- [x] Add Docker Compose service for PostgreSQL.
- [x] Add Docker Compose service for Qdrant. (Behind `--profile vector`; honest scaffolding, no consumer yet.)
- [x] Add Docker Compose service for vLLM. (Behind `--profile ai`; off by default.)
- [x] Add Docker Compose service for worker.
- [x] Add development Compose profile. (`docker-compose.dev.yml` override.)
- [x] Add home-server Compose profile. (Base `docker-compose.yml`.)
- [x] Configure private Docker network.
- [x] Expose only intended UI and API ports. (Only `web` publishes a port by default; API port only in the dev override.)
- [x] Keep vLLM private by default.
- [x] Document environment variables and secrets. (`.env.example`, `docker/README.md`.)
- [x] Document persistent volumes.
- [x] Test `docker compose up -d`. (Core stack verified healthy end-to-end on real PostgreSQL — bootstrap → account → balance → household context round-trip through the web proxy.)
- [x] Test backup and restore in Docker environment. (Verified against real PostgreSQL 17 in the container: created an account with a balance, took a backup, deleted the account, restored, and confirmed it came back byte-for-byte. This exercised M8's `PgDumpBackupAdapter` against real Postgres for the first time and caught a pg_dump/pg_restore major-version-match requirement, now handled by pinning the DB image to match the client.)

### Contracts and Generated Clients

- [ ] Add OpenAPI linting to CI.
- [x] Add backend OpenAPI drift check to CI.
- [x] Add Angular client generation check to CI. (`web.yml` fails if the committed client is stale vs. the OpenAPI contract.)
- [ ] Add Swift client generation check to CI.
- [ ] Document generated client regeneration workflow.
- [ ] Ensure clients do not hand-maintain DTOs that should come from OpenAPI.

### Quality Gates

- [x] Add backend linting to CI.
- [x] Add backend unit tests to CI.
- [x] Add backend integration tests to CI.
- [x] Add financial engine unit tests to CI. (`services.yml` matrix.)
- [x] Add AI orchestrator tests to CI. (`services.yml` matrix.)
- [x] Add worker tests to CI. (`services.yml` matrix covers ocr-worker, scheduler, and backup.)
- [x] Add Angular linting and tests to CI. (`web.yml` — build + Vitest.)
- [ ] Add iOS test instructions and CI plan.
- [ ] Add end-to-end smoke test for local deployment.
- [ ] Add synthetic fixture policy check.
- [ ] Add documentation link check.

### Documentation

- [x] Update root README with implemented setup path. (Docker quickstart added.)
- [x] Add local development quickstart. (`docs/guides/local-development.md`.)
- [x] Add home-server deployment guide. (`docs/guides/deployment.md`.)
- [x] Add onboarding guide. (First-run household setup in the deployment guide.)
- [x] Add API development guide. (`docs/guides/local-development.md` + `apps/api/README.md`.)
- [x] Add database migration guide. (Migration section in the local-development guide + `database/README.md`.)
- [x] Add financial engine guide. (Services section in the local-development guide + `services/financial-engine/README.md`.)
- [x] Add AI runtime configuration guide. (Deployment guide "optional services" + `apps/api/README.md` M4 scope.)
- [x] Add import guide. (Deployment guide + Imports troubleshooting + `apps/api/README.md` M7 scope.)
- [x] Add backup and restore guide. (`docs/guides/backup-and-restore.md`.)
- [x] Add security hardening guide. (`docs/guides/security.md`.)
- [x] Add troubleshooting guide. (`docs/guides/troubleshooting.md`.)
- [x] Add release checklist. (`docs/RELEASE-CHECKLIST.md`.)

### Final Acceptance (0.1.0)

- [x] Verify all milestone tasks are complete or explicitly deferred. (M1–M13 done; deferrals listed in `docs/RELEASE-CHECKLIST.md` and the backlog sections above.)
- [x] Verify all accepted specs match implemented behavior. (Acceptance State in `docs/specs/README.md` current through M13.)
- [x] Verify all OpenAPI endpoints match backend behavior. (`make check-openapi` passes.)
- [x] Verify generated clients build against current OpenAPI. (Angular client regenerated with no drift; `npm run build`/`npm test` pass. Swift client is deferred with the iOS app.)
- [x] Verify no sensitive sample data is committed. (Only `.env.example`/synthetic fixtures tracked; secret scan clean.)
- [x] Verify Docker deployment works from a clean checkout. (`docker compose up -d --build` brings all core services healthy; TLS health verified.)
- [x] Verify backup restore works from a clean environment. (Full round trip verified on real PostgreSQL in M12.)
- [x] Verify documentation is current. (Guides, READMEs, ADRs, and Acceptance State updated.)
- [x] Tag first release. (`v0.1.0`.)
