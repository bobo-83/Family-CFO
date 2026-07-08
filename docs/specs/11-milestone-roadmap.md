# Milestone Roadmap

## M0: Repository and Specification Baseline

- Monorepo scaffold
- Git initialized
- Spec Kit drafted
- Initial ADRs
- Initial OpenAPI contract
- CI spec check

## M1: Backend Skeleton

- FastAPI app
- Health endpoint
- OpenAPI generation workflow
- PostgreSQL connection
- Migration tooling
- Test harness

### Scope

- Add the initial FastAPI application under `apps/api`.
- Expose `GET /api/v1/health` as an unauthenticated liveness endpoint.
- Add application configuration, logging redaction hooks, PostgreSQL connection plumbing, Alembic migration tooling, and a pytest harness.
- Add an OpenAPI generation command and a contract check for routes implemented during M1.

### Non-Goals

- No household, account, transaction, goal, recommendation, import, report, authentication, pairing, or AI runtime product behavior.
- No financial calculations.
- No production Docker deployment.
- No generated Swift or Angular clients.

### API Behavior

- `GET /api/v1/health` returns the `HealthResponse` shape from `shared/openapi/family-cfo.v1.yaml`.
- The health endpoint returns `status` and `version`.
- The route is unauthenticated.
- Structured API errors use the common `{ "error": { "code", "message", "details" } }` shape.

### Data Model Changes

- M1 adds migration tooling and an empty baseline migration only.
- M1 does not create product tables.
- PostgreSQL connection configuration is added for development, tests, and future migrations.

### Security Impact

- The health endpoint exposes only service status and version.
- Local development configuration must not require committed credentials.
- Logging must include redaction hooks for credentials, tokens, secrets, and raw sensitive data.

### Test Expectations

- Unit tests cover health response construction.
- Integration tests cover `GET /api/v1/health`.
- Contract tests verify implemented FastAPI routes remain represented in the shared OpenAPI contract.
- Tests must not require a running PostgreSQL server.

### Documentation Impact

- Document API setup, run, test, lint, OpenAPI, and migration commands.
- Update the implementation task checklist as M1 tasks complete.

## M2: Financial Context and Deterministic Engine

- Household, accounts, balances, transactions, bills, income, and goals
- Cash flow and budget calculations
- Net worth calculation
- Unit tests for money precision

### Scope

- Add a deterministic financial engine package under `services/financial-engine` with a `Money` value type, a shared calculation result contract, and net worth, cash flow, budget summary, emergency fund months, and savings goal progress calculations.
- Add the M2 data model (households, users, household memberships, auth sessions, accounts, account balances, transactions, transaction categories, bills, income sources, goals, scenarios, financial calculations) as Alembic migrations.
- Add a local authentication foundation: password-based login that issues an opaque bearer token backed by `auth_sessions`, and a FastAPI dependency that resolves a token into a household/user/role context for protected routes.
- Add read APIs for household context, accounts, transactions, bills, and income sources, and read/create APIs for goals, all scoped to the caller's household and backed by the financial engine and a repository layer.
- Persist an audit record in `financial_calculations` whenever the API serves a financial engine result.
- Add synthetic fixtures for a demo household so the new routes and repository layer are testable without real financial data.

### Non-Goals

- No account, transaction, bill, income, or scenario write APIs (create/update/delete) beyond goal creation.
- No user registration, invitation, or household-membership management APIs; the demo household/user are seeded via fixtures, not a public signup flow.
- No mobile pairing, chat, purchase advisor, imports, reports, or AI runtime behavior.
- No scenario calculation logic; M2 only persists the `scenarios` table shape for M3 to build on.
- No token refresh, rotation, or revocation UI; sessions simply expire.
- No production Docker deployment changes.

### API Behavior

- `POST /api/v1/auth/sessions` accepts `email` and `password`, returns an `AuthSession` with `access_token`, `expires_at`, `household_id`, and `role`, and is unauthenticated.
- `GET /api/v1/household`, `GET /api/v1/accounts`, `GET /api/v1/goals`, `POST /api/v1/goals`, `GET /api/v1/transactions`, `GET /api/v1/bills`, and `GET /api/v1/income` all require `bearerAuth` and are scoped to the authenticated household.
- `GET /api/v1/household` returns `HouseholdContext`, computed by running the net worth and emergency fund calculations against the household's current accounts, balances, bills, and income.
- `POST /api/v1/goals` is limited to the `owner` and `adult` roles; `viewer` and `child` roles receive `403` with the common error shape.
- All list endpoints return only rows belonging to the caller's household.
- Structured API errors use the existing `{ "error": { "code", "message", "details" } }` shape, including `401` for missing/invalid/expired tokens and `403` for role violations.

### Data Model Changes

- Add tables: `households`, `users`, `household_memberships`, `auth_sessions`, `accounts`, `account_balances`, `transactions`, `transaction_categories`, `bills`, `income_sources`, `goals`, `scenarios`, `financial_calculations`.
- All money columns use a signed integer minor-unit column plus a 3-character ISO 4217 currency column; no floating-point money columns are permitted.
- `household_memberships.role` is one of `owner`, `adult`, `viewer`, `child`; enforced with a `CHECK` constraint.
- `auth_sessions` stores a hash of the access token, never the raw token, plus `expires_at` and a nullable `revoked_at`.
- `account_balances` is append-only; the current balance for an account is the row with the latest `as_of` timestamp.
- Foreign keys scope `accounts`, `transactions`, `bills`, `income_sources`, `goals`, `scenarios`, and `financial_calculations` to `household_id`.
- Migrations are added one table at a time, chained from `0001_initial_baseline`, and are reversible.

### Security Impact

- Passwords are stored as salted hashes; raw passwords and raw bearer tokens are never persisted or logged.
- Bearer tokens are opaque, random, and expire; expired or revoked sessions are rejected.
- Household-scoped queries prevent cross-household data access even with a valid token for a different household.
- Role-based checks gate write access; read access is available to all household member roles.
- Logging redaction hooks from M1 continue to cover credentials and tokens used by the new auth endpoint.

### Test Expectations

- Financial engine: unit tests for money precision (integer minor units, no float drift), currency mismatch handling, and each calculation (net worth, cash flow, budget summary, emergency fund months, goal progress).
- Repository layer: tests over an in-memory SQLite engine using the same table metadata as the Postgres migrations, so tests do not require a running PostgreSQL server.
- API: integration tests for each new route covering the authenticated success path, the `401` unauthenticated path, and the `403` role-restricted path for goal creation.
- Contract tests continue to verify implemented FastAPI routes remain represented in the shared OpenAPI contract.

### Documentation Impact

- Document the financial engine's calculation contracts, assumptions, and limitations in `services/financial-engine/README.md`.
- Update `apps/api/README.md` with M2 routes, the auth flow, migration commands for the new tables, and fixture seeding.
- Update `database/README.md` to note product tables now exist and describe the money storage convention.
- Update the implementation task checklist as M2 tasks complete.

## M3: Purchase Advisor

- Scenario input API
- Financial impact calculation
- Recommendation response structure
- LLM explanation adapter stub
- Integration tests

### Scope

- Add `POST /api/v1/advisor/purchase`: given an item, price, and optional context, persist the request as a `scenarios` row and return a `Recommendation` grounded in deterministic financial engine output.
- Add a `calculate_purchase_impact` calculation to the financial engine that models a one-time cash purchase's effect on net worth, emergency fund coverage, and discretionary cash-flow burn, and — only when the household has goal data — the purchase's size relative to the household's top-priority goal.
- Add a `recommendations` table so every recommendation response is durably linked to the `financial_calculations` row(s) and `scenarios` row it cites.
- Add a small `ExplanationAdapter` interface in the API with a deterministic, no-model implementation that renders calculation outputs as plain-language sentences. This is the seam M4's LLM-backed adapter will implement (ADR 0007).
- Persist an audit record in `financial_calculations` for the purchase impact calculation, same as M2's household context calculations.

### Non-Goals

- No real LLM call; the M4 milestone adds the vLLM-backed adapter behind the same `ExplanationAdapter` interface.
- No debt payoff calculation — M2's schema has no interest rate or payment schedule data, so when the household carries liabilities the recommendation includes a `debt` impact entry with a warning instead of a fabricated number. This is tracked as backlog, not silently dropped: see "Backlog: Debt Payoff and Retirement Projections" in `docs/specs/12-implementation-tasks.md`. The engine calculation itself (`calculate_debt_payoff`) is already implemented and unit tested with mocked inputs; only the account-level schema and API wiring remain.
- No multi-item or recurring-purchase scenarios; a purchase is modeled as a single one-time cash outflow.
- No scenario or recommendation history UI, editing, or deletion APIs.
- No chat integration; that begins in a later milestone.

### API Behavior

- `POST /api/v1/advisor/purchase` requires `bearerAuth` and is available to every household role (owner, adult, viewer, child) — asking "can I afford this" is a read-like action, unlike goal creation.
- Request validation rejects a non-positive `price` with a `400` structured error.
- The response is the existing `Recommendation` shape: `answer`, `assumptions`, `impacts`, `tradeoffs`, `alternatives`, `confidence`, `calculation_refs`, and `warnings`.
- `calculation_refs` cites the persisted `financial_calculations` row id for the purchase impact calculation; every numeric claim in `answer` traces back to that calculation's outputs (ADR 0003).
- `impacts` always includes `net_worth`, `emergency_fund`, and `cash_flow` entries with computed `amount` values, plus a `savings_goal` entry when the household has at least one goal and a `debt` entry (warning-only, no `amount`) when the household has liability accounts.

### Data Model Changes

- Add `recommendations`: `id`, `household_id`, `scenario_id` (nullable FK to `scenarios`), `answer`, `assumptions_json`, `impacts_json`, `tradeoffs_json`, `alternatives_json`, `confidence`, `calculation_refs_json`, `warnings_json`, `explanation_source` (`CHECK` constrained to `deterministic_stub` for M3; M4 will add `llm` in a follow-up migration), `created_at`.
- The purchase request itself is persisted as a `scenarios` row (`name`, `description`, `input_json` holding the raw request), reusing the table M2 added for this purpose.

### Security Impact

- Purchase item names, merchant names, and prices are classified `Sensitive` (per `docs/specs/06-security-model.md`) and must not appear in application logs; only non-sensitive identifiers (household id, calculation id) are logged.
- No new attack surface beyond the existing bearer-token auth already covering all M2 routes.

### Test Expectations

- Financial engine: unit tests for `calculate_purchase_impact` covering net worth/emergency fund/cash-flow deltas, the top-goal opportunity-cost path, the liability warning path, and the case where a purchase price exceeds liquid balance.
- API: integration tests for the success path (200 with calculation refs), the `401` unauthenticated path, and the `400` invalid-price path.
- A redaction test asserting that log output produced while handling a purchase advisor request never contains the submitted item name, merchant, or price.

### Documentation Impact

- Document `calculate_purchase_impact`'s assumptions and limitations in `services/financial-engine/README.md`.
- Document the advisor route and the `ExplanationAdapter` seam in `apps/api/README.md`.
- Update the implementation task checklist as M3 tasks complete.

## M4: Local AI Runtime

- vLLM adapter
- Runtime configuration
- Prompt and model version tracking
- Guardrail tests

### Scope

- Add an `ai-orchestrator` package (`family_cfo_ai_orchestrator`) with a `RuntimeAdapter` protocol, a `VLLMAdapter` implementation calling an OpenAI-compatible `/v1/chat/completions` endpoint over HTTP with a configurable timeout and retry policy, versioned prompt templates, and guardrail utilities — all independent of `apps/api` and `family_cfo_financial_engine` so the runtime stays replaceable (ADR 0004, ADR 0007).
- Add `GET /api/v1/ai/runtime` and `PUT /api/v1/ai/runtime`, backed by a new household-scoped `ai_runtime_configs` table.
- Add `LlmExplanationAdapter` in `apps/api`, conforming to M3's `ExplanationAdapter` interface, that builds a prompt from purchase-impact facts, calls the configured `RuntimeAdapter`, validates the response against the guardrails, and falls back to M3's `DeterministicExplanationAdapter` (with a warning) on timeout, adapter error, or guardrail violation.
- Track `model_version` and `prompt_version` on each `recommendations` row.
- Wire the purchase advisor route to use `LlmExplanationAdapter` only when the household has an `ai_runtime_configs` row with `enabled = true`; otherwise it keeps using the deterministic stub exactly as in M3, so self-hosted deployments with no runtime configured see no behavior change.

### Non-Goals

- No actual vLLM (or other runtime) deployment, container, or Compose service — that is M8/Release Readiness Docker work. M4 only needs a runtime reachable over HTTP for real use; tests mock the HTTP layer.
- No chat endpoint or conversation history; the runtime adapter is exercised only through the purchase advisor for now.
- No API-key/secret storage for cloud-hosted OpenAI-compatible endpoints — cloud AI calls for sensitive data require the explicit opt-in ADR the security model reserves for a future decision (`docs/specs/06-security-model.md`), which is out of scope here.
- No persistence of raw prompts or raw model completions — only the final, guardrail-validated explanation text (already covered by `recommendations.answer`) plus `model_version`/`prompt_version` metadata are stored, consistent with the security model's prompt-redaction expectations.
- No Ollama or llama.cpp adapters yet; the interface is designed for them but only the vLLM adapter ships in M4.

### API Behavior

- `GET /api/v1/ai/runtime` requires `bearerAuth` and is available to every household role; it returns the household's current config or a default disabled config if none has been set.
- `PUT /api/v1/ai/runtime` requires `bearerAuth` and is limited to the `owner` role (`403` otherwise) — changing which runtime a household's financial data is sent to is a higher-sensitivity action than goal creation.
- Both routes use the existing `AiRuntimeConfig` schema (`provider`, `base_url`, `model`, `enabled`) already defined in the shared OpenAPI contract.

### Data Model Changes

- Add `ai_runtime_configs`: `id`, `household_id` (FK, unique — one active config per household), `provider` (`CHECK` constrained to `vllm`, `ollama`, `llama_cpp`, `openai_compatible`), `base_url`, `model`, `enabled`, `created_at`, `updated_at`.
- Add nullable `model_version` and `prompt_version` columns to `recommendations` via an additive migration (`ADD COLUMN`, no constraint rewrite needed).

### Security Impact

- `base_url` is expected to point at a private, self-hosted runtime; the security model already requires vLLM be private by default (`docs/specs/10-docker-spec.md` scope, enforced later in Release Readiness).
- Guardrails reject any generated explanation containing a numeric claim that doesn't trace back to the calculation's own outputs, and the system falls back to the deterministic stub rather than surfacing an unvalidated LLM response.
- No raw prompt or raw model response is logged or persisted; only household id, model, prompt version, and pass/fail guardrail outcome are logged.
- `PUT /api/v1/ai/runtime` is owner-only, consistent with role-based authorization already established in M2.

### Test Expectations

- `ai-orchestrator`: contract tests for `RuntimeAdapter` covering a successful completion, an HTTP timeout, and a non-2xx response, all against a mocked transport (no real vLLM server); guardrail unit tests for the unattributed-numeric-claim detector.
- `apps/api`: integration tests for `GET`/`PUT /api/v1/ai/runtime` covering the authenticated success path, `401`, and `403` for non-owner `PUT`; tests for `LlmExplanationAdapter` covering the guardrail-pass path, the guardrail-fail fallback path, and the adapter-error fallback path, using a mocked `RuntimeAdapter` — no real vLLM server required anywhere in the test suite.

### Documentation Impact

- Document the `RuntimeAdapter` interface and guardrail behavior in `services/ai-orchestrator/README.md`.
- Document the runtime config API and the guardrail fallback behavior in `apps/api/README.md`.
- Update the implementation task checklist as M4 tasks complete.

## M5: Angular Dashboard

- Onboarding
- Reports shell
- Transaction review
- Import review
- AI runtime settings

### Scope

- Add an Angular project under `apps/web`: standalone components (no `NgModule`s), Angular signals for local component state, plain SCSS for styling, no server-side rendering — a self-hosted single-page app served behind the FastAPI backend.
- Add a generated TypeScript client from `shared/openapi/family-cfo.v1.yaml` (`@hey-api/openapi-ts` with the `@hey-api/client-fetch` runtime — TypeScript-native, no JVM required; this sandbox's Java 8 is too old for modern `openapi-generator-cli`), regenerated via an npm script, never hand-edited.
- Add an app shell (nav + routed content), a login page against `POST /api/v1/auth/sessions`, token storage, a generated-client request interceptor attaching the bearer token, and an auth guard. The generated client (`@hey-api/client-fetch`) calls `fetch()` directly rather than Angular's `HttpClient`, so token attachment uses the client's own `interceptors.request.use()` hook, not an Angular `HttpInterceptor`.
- Add real, backend-integrated pages for every M2–M4 read/write API that exists today: onboarding/login, overview (`GET /household`), accounts (`GET /accounts`), goals (`GET`/`POST /goals`), and AI runtime settings (`GET`/`PUT /api/v1/ai/runtime`).
- Add explicitly-labeled shell pages, reachable from the nav, for capabilities whose backend doesn't exist yet: reports, transaction review, import review, backup management, user management, and paired-device revocation. Each shell states which future milestone will make it real (M6 pairing/revocation, M7 imports, M8 reports/backups); this is honest scaffolding, not simulated functionality.
- Add loading and error states as a shared pattern (a small signal-based "resource" wrapper) used by every backend-integrated page, and client-side form validation for the login and goal-creation forms.

### Non-Goals

- No user registration/signup UI — M2 has no signup API; onboarding authenticates against the seeded demo household, same limitation already documented in `apps/api/README.md`.
- No real functionality behind the shell pages (reports, transaction/import review, backup management, user management, device revocation) — they render a labeled placeholder, nothing more, until their backend milestones land.
- No purchase advisor UI — not part of M5's page list; the dashboard doesn't yet expose the M3 advisor or M4 AI explanation path.
- No dark mode, i18n, or design system beyond plain SCSS; visual polish is deferred.
- No production Docker image for the dashboard — that is Release Readiness Docker work (`docs/specs/10-docker-spec.md`).

### Onboarding Flow

Onboarding is a login screen, not a signup wizard: a welcome message explaining this is a self-hosted instance, an email/password form posting to `POST /api/v1/auth/sessions`, and on success a redirect to the overview page. A failed login shows the structured API error's `message`. This matches M2's actual auth surface — there is no account-creation flow to onboard into yet.

### Dashboard Information Architecture

Nav sections, per `docs/specs/09-angular-dashboard-spec.md`: Overview, Accounts, Goals, Reports (shell), Transactions (shell), Imports (shell), AI Runtime (real), Backups (shell), Users (shell). Paired-device revocation lives inside a Settings-adjacent shell page rather than its own nav entry, since it has no backend until M6.

### Generated Client Workflow

`npm run generate:client` runs `@hey-api/openapi-ts` against `shared/openapi/family-cfo.v1.yaml`, writing into `apps/web/src/app/api-client/`. The generated directory is committed (not gitignored) so `npm ci` alone is sufficient to build, consistent with treating the OpenAPI contract as source of truth without requiring a code-generation step in CI for M5. Regenerate and commit whenever the shared contract changes.

### Browser-Side Security Expectations

- The bearer token is held in memory (a signal-based auth service) and persisted to `localStorage` only so a page refresh doesn't force re-login; this is a self-hosted single-tenant dashboard, not a target for XSS-heavy multi-tenant threat models, but no token is ever logged to the browser console.
- The client's request interceptor attaches the token only to requests made through the generated client (always same-origin `/api/v1/...`), never to arbitrary URLs.
- A `401` response from any API call clears the stored token and redirects to login.
- No inline `eval`, no third-party analytics or telemetry scripts, consistent with the project's no-telemetry principle.

### Test Expectations

- Unit tests via Vitest (the Angular 22 CLI default — jsdom-based, no browser required) for the token store, the auth service, the auth guard, and each backend-integrated page component (login, overview, accounts, goals, AI runtime settings), covering the loading, success, and error-state rendering. Angular's Vitest integration does not support `vi.mock()` on relative imports ("Please use Angular TestBed for mocking dependencies"), so components depend on an injectable `ApiService` wrapping the generated client's SDK functions rather than importing them directly — tests substitute `ApiService` via `TestBed`'s DI, not module mocking.
- Request-shape correctness against the OpenAPI contract, and the client interceptor's bearer-token attachment and 401-clears-session behavior, are validated by the end-to-end Playwright test below against a real backend rather than by mocking `fetch()` in a unit test — an attempt at the latter hit an unresolved environment quirk in Angular 22's Vitest builder where `globalThis.fetch` spies in non-`TestBed` test files never observed the generated client's calls. The real-backend test is strictly stronger evidence for this specific behavior anyway.
- One end-to-end Playwright smoke test covering login against a running backend and confirming the overview page renders household data — the "onboarding and health connectivity" smoke test the implementation checklist calls for. It is not part of the default `npm test` run; it requires the API server (and thus is documented as an opt-in script), since spinning up Postgres/Alembic in the same pass this milestone touches is out of scope.
- This sandbox has no system Chrome; the Playwright e2e test runs against a Playwright-managed headless Chromium (`npx playwright install chromium` once), documented in `apps/web/README.md`. Unit tests need no browser at all.

### Documentation Impact

- Add `apps/web/README.md` with setup, run, test, lint, and client-generation commands.
- Update the implementation task checklist as M5 tasks complete.

## M6: iPhone App

- Pairing
- Chat
- Camera capture
- Structured image output
- Face ID local unlock

### Scope

- Add a SwiftUI iPhone app scaffold under `apps/ios` with an app navigation shell and a generated Swift client derived from `shared/openapi/family-cfo.v1.yaml`.
- Implement the mobile pairing path from the existing mobile and security specs: QR scan, server identity and household confirmation, pairing confirmation API call, secure device credential storage, and revocation handling surfaced to the app.
- Implement the backend support M6 needs for OpenAPI-defined pairing and chat behavior that is not yet shipped, keeping OpenAPI as the source of truth before generating the Swift client.
- Add Face ID local unlock around stored mobile credentials where available; server authorization remains token-based and revocable.
- Add a chat UI backed by the local Family CFO API and AI orchestration path, with financial claims grounded in deterministic calculation references rather than mobile-only calculations.
- Add camera, receipt, and store-item capture flows that use Apple Vision where available to turn images into structured JSON, including source and confidence metadata, before sending data to the server when an accepted endpoint exists.
- Keep raw photos on device when structured extraction is sufficient, and add focused tests plus iOS documentation for pairing, unlock, chat, capture, generated-client request mapping, and credential-storage seams.

### Non-Goals

- No App Store release, signing, provisioning profile management, push notification service, or production mobile distribution.
- No user registration, household invitation, membership management, or role editing from mobile.
- No mobile-side financial calculations; the iPhone can capture structured inputs but all financial reasoning remains server-side and deterministic.
- No general-purpose conversational AI or persisted conversation history. M6 chat returns a bounded recommendation-style response grounded in existing financial calculations; broader conversational memory belongs to a later milestone.
- No raw photo upload requirement. Uploading images is deferred unless a future import/document endpoint explicitly accepts binary documents; M6 sends structured JSON where possible.
- No Android, iPad-specific layout, watchOS, widgets, notifications, or offline-first financial data cache.

### API Behavior

- `POST /api/v1/pairing/sessions` requires bearer auth and is limited to `owner` and `adult` roles. It creates a short-lived pairing session for the caller's household and returns a QR payload containing the session id and non-secret server/household display metadata.
- `POST /api/v1/pairing/confirm` accepts a pairing session id, device name, and device public key. It rejects unknown, expired, confirmed, or revoked sessions with `400`; on success it creates a paired device record and an opaque bearer session scoped to that device.
- `GET /api/v1/pairing/devices` requires bearer auth and returns the household's paired devices without token hashes or public-key secrets.
- `DELETE /api/v1/pairing/devices/{device_id}` requires the `owner` role, marks the paired device revoked, and revokes active auth sessions issued for that device.
- `POST /api/v1/chat/messages` requires bearer auth. It accepts a message and optional conversation id, computes the household's current net worth and emergency fund context, persists a recommendation row with calculation references, and returns `ChatResponse`.
- The M6 chat response does not persist raw user messages and does not expose unvalidated LLM output. Numeric claims cite `financial_calculations` rows.

### Data Model Changes

- Add `pairing_sessions`: `id`, `household_id`, `created_by_user_id`, `qr_payload`, `created_at`, `expires_at`, `confirmed_at`, and `revoked_at`.
- Add `paired_devices`: `id`, `household_id`, `user_id`, `name`, `public_key`, `created_at`, `last_seen_at`, and `revoked_at`.
- Add nullable `device_id` to `auth_sessions` so revoking a paired device can revoke sessions issued through pairing without affecting normal password sessions.
- No chat message table is added in M6 because raw conversation history is explicitly out of scope.

### Pairing Flow Details

- Dashboard creates a pairing session after the user is authenticated as `owner` or `adult`.
- The QR payload contains the API base path, pairing session id, household id, household display name, and expiration timestamp. It does not contain an access token or reusable secret.
- The iPhone scans the QR code, shows server and household confirmation, generates or loads a device public key, and calls `POST /api/v1/pairing/confirm`.
- The server stores only the device public key and a hash of the issued bearer token. The raw token is returned once and then stored by the app in Keychain.
- Pairing sessions expire after a short TTL and are single-use.

### Generated Swift Client Workflow

- The shared OpenAPI contract remains the source of truth. Any M6 API shape changes land in `shared/openapi/family-cfo.v1.yaml` before app client code changes.
- The iOS scaffold includes a repeatable generation command under `apps/ios/Scripts` that reads the shared OpenAPI contract and writes the checked-in Swift API surface under `apps/ios/Sources/FamilyCFOApp/API/Generated`.
- Generated Swift files carry a header stating they should not be hand-edited. Hand-written app services wrap the generated client for token injection, error mapping, and test doubles.

### Secure Credential Storage Expectations

- Pairing access tokens are stored only in Keychain using a Keychain abstraction that can be replaced in tests.
- Tokens are never stored in `UserDefaults`, logs, fixtures, source files, screenshots, or generated previews.
- The app sends credentials only in the `Authorization: Bearer <token>` header over the configured local server URL.
- Logging in app code must not include tokens, QR payload contents, raw receipt text, image data, or household financial values.

### Face ID Local Unlock Behavior

- Face ID or device passcode protects access to the locally stored token where available through `LocalAuthentication`.
- If biometric authentication is unavailable, the app can fall back to device passcode or require re-pairing; it must not silently bypass local unlock after a protected token exists.
- Local unlock gates app access only. Server authorization remains controlled by bearer-token validity and paired-device revocation.

### Camera Capture and Structured Image Output Rules

- Capture flows cover receipt and store-item use cases. The app may use Apple Vision to extract merchant, item, price, currency, source, confidence, and optional user question.
- Structured purchase captures map to the existing purchase advisor request shape using `source` values such as `mobile_vision`, `receipt`, or `product_photo`.
- The app sends structured JSON to the server when confidence is high enough for user review; otherwise it keeps the image on device and asks the user to edit or confirm fields.
- Raw photos stay on device unless a later accepted import/document endpoint explicitly requires document upload.

### Security Impact

- Pairing creates bearer credentials equivalent to a local auth session, so the flow is restricted to authenticated household users and supports owner-driven revocation.
- Device public keys, pairing secrets, and bearer tokens are `Restricted`; device names and QR display metadata are `Internal`; captured purchase fields are `Sensitive`.
- Pairing and chat logs include only non-sensitive identifiers such as household id, pairing session id, device id, recommendation id, and calculation id.
- The mobile app remains a client of server-side deterministic calculations; LLM output, if used in later chat work, must pass the same attribution guardrails established in M4.

### Test Expectations

- Backend repository tests cover pairing-session lifecycle, device creation, and device revocation revoking device-backed auth sessions.
- Backend API tests cover pairing session creation, pairing confirmation, device list/revocation, chat success, `401` unauthenticated paths, `403` role restrictions, and invalid/expired pairing sessions.
- OpenAPI contract tests continue to verify every implemented FastAPI route exists in the shared contract.
- iOS unit tests, run on macOS with the Swift toolchain, cover generated-client request mapping, token injection, Keychain abstraction behavior, local unlock state, QR payload parsing, chat request flow, and capture-to-purchase-request mapping.
- This Linux development environment does not provide Swift or Xcode, so local verification here is limited to backend tests, OpenAPI checks, generated-source consistency checks, and documentation validation.

### Documentation Impact

- Update `apps/api/README.md` with pairing, paired-device revocation, and chat endpoint behavior.
- Update `apps/ios/README.md` with setup, generated-client, testing, pairing, Face ID, chat, and capture development notes.
- Update the implementation task checklist as M6 tasks complete.

## M7: Imports and OCR

- CSV import
- PDF pipeline
- OFX and QFX planning
- Review queue
- Worker scheduling

## M8: Reports and Backups

- Weekly report
- Monthly report
- Encrypted backups
- Restore test
