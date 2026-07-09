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

- [ ] Extend `audit_events` coverage beyond M9's own mutations to all pre-existing sensitive mutation points (auth login, pairing confirm, paired-device revoke, AI-runtime config change, import apply/discard, report generation, backup create/restore). M9 introduces the table and audits the writes it adds; this generalizes it.
- [ ] Add a dashboard **chat UI** page backed by the M6/M10 chat and conversation-history APIs (M10 persists threads at the API layer but ships no UI; M11 covers only the four M5 shells).
- [ ] Add a first-run **household setup wizard** UI around `POST /api/v1/households` (M9 adds the bootstrap API; M5 onboarding remains login-only; M11 non-goals defer the wizard UX).
- [ ] Add optional first-run bootstrap lockout (refuse `POST /api/v1/households` once any household exists) for deployments that want it — M9 leaves bootstrap open for the trusted-local-network case.
- [ ] Feed prior conversation turns back into the model as context (true multi-turn memory) — M10 persists history but does not change what the assistant computes; this interacts with the real-vLLM/retrieval work.

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
