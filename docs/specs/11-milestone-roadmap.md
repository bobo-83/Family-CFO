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
- Implement the dashboard side of pairing (session creation, QR display, paired-device list and revocation) in the Angular app — Linux-buildable, unlike the rest of M6 — since the mobile app cannot pair without something generating the QR code it scans.

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

### Dashboard Integration (Linux-safe)

This spec's Pairing Flow Details says "Dashboard creates a pairing session," but that was never assigned to an implementation task — a gap surfaced during review, not part of the original M6 scope. Tracked here since it is Angular work (Linux-buildable) even though the rest of M6 is iOS-only.

- The Angular "Users & Devices" page (`apps/web`, added as a placeholder shell in M5) gets a real implementation: a "Pair a device" action calling `POST /api/v1/pairing/sessions` (visible to `owner`/`adult`, per that endpoint's role restriction) that renders the returned `qr_payload` as a scannable QR code plus its raw text and expiration time, and a paired-device list calling `GET /api/v1/pairing/devices` with a revoke action calling `DELETE /api/v1/pairing/devices/{device_id}` (visible to `owner` only, per that endpoint's role restriction).
- QR rendering is client-side only (a pure-JS QR code generator); the payload itself is already non-secret per the Pairing Flow Details above, so no new data leaves the browser beyond the existing API call.
- This does not include a QR *scanner* — the dashboard displays a code for the iPhone app to scan; scanning is inherently iOS's job, unavailable on Linux and out of scope here.

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
- Dashboard unit tests cover the pairing-session creation flow (including the `403` non-owner/adult path), the paired-device list, and the revoke action (including the `403` non-owner path), following the `ApiService`-DI mocking pattern established in M5.

### Documentation Impact

- Update `apps/api/README.md` with pairing, paired-device revocation, and chat endpoint behavior.
- Update `apps/ios/README.md` with setup, generated-client, testing, pairing, Face ID, chat, and capture development notes.
- Update `apps/web/README.md` with the pairing/device-management dashboard behavior.
- Update the implementation task checklist as M6 tasks complete.

## M7: Imports and OCR

- CSV import
- PDF pipeline
- OFX and QFX planning
- Review queue
- Worker scheduling

### Scope

- Add `imports`/`import_files` tables and a CSV import pipeline: register an import (`POST /api/v1/imports`), upload the file to staging storage (`POST /api/v1/imports/{id}/file`), parse and map CSV rows into `transactions` with `review_state = 'pending'`, flagging likely duplicates, then let the caller confirm (`POST /api/v1/imports/{id}/apply`, flips to `reviewed`) or discard (`POST /api/v1/imports/{id}/discard`, deletes the pending rows) the whole import.
- Add `documents`/`document_extractions` tables and a `POST /api/v1/documents` endpoint for single-document extraction (a receipt image or a PDF statement), separate from the CSV import pipeline: it produces a structured extraction record for review, but never writes directly to `transactions` — that linkage (e.g. into the purchase advisor) is future work.
- Add a `family_cfo_ocr_worker` package (`services/ocr-worker`) with a `DocumentExtractionAdapter` protocol, one real adapter (`PdfTextExtractionAdapter`, using `pypdf` — pure Python, no system binary) and one deterministic test adapter for image content (`DeterministicOcrAdapter`, matches known fixture bytes to fixed structured output; no real OCR engine is wired up).
- Add a `family_cfo_scheduler` package (`services/scheduler`) with an import-processing job function callable directly (for synchronous tests) and wrapped by an APScheduler interval trigger for real background polling, plus bounded retry/failure handling.
- Add OFX and QFX *planning only* — no parser, no endpoint. Document the target shape so a future milestone can implement it against a settled plan instead of starting from nothing.

### Non-Goals

- No real OCR engine (Tesseract, Apple Vision, cloud OCR). The adapter interface and a deterministic test adapter ship now; a real image-OCR adapter is future work behind the same interface (ADR 0007). This sandbox has no Tesseract binary and OCR accuracy isn't something a spec gate can pin down deterministically anyway.
- No OFX or QFX parsing — planning documentation only.
- No automatic creation of `transactions` from PDF or image extraction; only CSV import writes to `transactions`. Structured extraction from a receipt/PDF is surfaced for a human (or a future milestone) to act on.
- No Angular "Import Review" page upgrade — M5 left it as an explicit placeholder shell, and this milestone is backend-only, the same split M6 used (Linux-safe backend now, UI as identified follow-up work).
- No document storage encryption-at-rest beyond the filesystem's own guarantees; the database schema spec already flags database/backup encryption as an open threat-model question, and staged import files inherit that same open question rather than resolving it here.
- No malware/antivirus scanning of uploaded files.
- No multi-file batch upload; one file per import.

### Import Job Lifecycle

1. `pending` — `POST /api/v1/imports` created the row; no file uploaded yet.
2. `pending` (still) — `POST /api/v1/imports/{id}/file` stored the file to staging and created `import_files`; the scheduler has not yet picked it up. (A separate `has_file` derived check, not a new status, distinguishes "no file yet" from "file uploaded, awaiting processing" — both are `pending`.)
3. `processing` — the scheduler job has started parsing.
4. `needs_review` — parsing succeeded; rows exist in `transactions` with `review_state = 'pending'`, or a `document_extractions` row exists (for `documents`, this is the terminal state — there is no `apply` for `documents`).
5. `completed` — `POST /api/v1/imports/{id}/apply` marked the import's pending transactions `reviewed`.
6. `discarded` — `POST /api/v1/imports/{id}/discard` deleted the import's pending transactions.
7. `failed` — parsing raised an error; `imports.error_message` holds a non-sensitive summary (never raw file content).

### CSV Import Schema and Mapping Behavior

- Expected columns (header row required, case-insensitive matching): `date`, `amount`, `description`/`merchant`, optional `category`. Amounts are parsed as decimal strings and converted to integer minor units via the same `Decimal`-based rounding approach the financial engine uses — never `float`.
- Rows that fail to parse (bad date, non-numeric amount) are skipped and counted in a `skipped_row_count` surfaced on the import record; they do not fail the whole import.
- Duplicate detection: a parsed row is flagged (not silently dropped) as a probable duplicate when an existing `transactions` row in the same household matches on `(account_id, occurred_at, amount_minor)`. Flagged rows still get inserted as `pending` so the reviewer decides — M7 does not auto-drop anything a human hasn't seen.

### PDF Pipeline Behavior

- `PdfTextExtractionAdapter` extracts raw text per page via `pypdf` — a real, deterministic operation on text-based PDFs (not scanned images; a scanned PDF yields little or no text, surfaced as a low-confidence result rather than an error).
- The adapter does not attempt statement-specific line-item parsing (vendor formats vary too much for a heuristic to be trustworthy); it returns raw text plus a naive "possible total" regex match as a low-confidence hint, not a committed number.
- PDFs uploaded through `POST /api/v1/documents` produce a `document_extractions` row; PDFs are not a supported `imports` `source_type` transaction path in M7 (only CSV writes transactions), even though `ImportSourceType` already includes `pdf` from the M0 baseline contract — that value now means "register intent to import a PDF statement," which the review queue still exposes as a raw-text extraction for a human to act on, not an automatic parse into transactions.

### OFX and QFX Planning

- Target shape (for a future milestone): both are structured, bank-defined formats (unlike freeform CSV), so parsing can be a real deterministic parser (no OCR/heuristics needed) once implemented — likely via the `ofxparse` library or a hand-rolled SGML/XML reader for OFX, and a compatible reader for QFX (a Quicken-flavored OFX variant).
- They would slot into the same `imports`/`import_files`/review-queue pipeline as CSV, differing only in the parser selected by `source_type`.
- No code lands in M7; this section exists so that future work starts from a settled plan instead of scratch.

### OCR Adapter Interface

- `DocumentExtractionAdapter` protocol: `extract(content: bytes, content_type: str) -> ExtractionResult`, where `ExtractionResult` carries `text`, `structured_fields: dict`, `confidence: float`, and `warnings: list[str]` — deliberately mirroring the financial engine's `CalculationResult` and the AI orchestrator's `RuntimeCompletion` shape (inputs/outputs/confidence/warnings) for consistency across the codebase's adapter patterns.
- `PdfTextExtractionAdapter` (real) handles `application/pdf`.
- `DeterministicOcrAdapter` (test-only) handles `image/*`: constructed with a fixture registry (`dict[bytes, ExtractionResult]`); known content returns its registered result, unknown content returns a fixed "OCR not available; manual entry required" result with `confidence = 0.0` and a warning — never a fabricated guess.
- Document API route selects an adapter by `content_type`, matching the same "replaceable component" pattern as `RuntimeAdapter` (M4) and the financial engine (ADR 0007).

### Review Queue Behavior

- Imported transactions never affect financial-engine calculations differently based on `review_state` — M2's calculations already read all transactions in a household regardless of state, so "before it affects financial state" specifically means before a human confirms it belongs (`apply`), not before the system trusts the numbers. This is a deliberate scope boundary: M7 does not add review-state filtering to the financial engine; that's a reasonable follow-up if pending-but-wrong imported rows turn out to skew net worth/cash flow in practice.
- `discard` is the safety valve for a bad import (wrong account, garbled CSV) — it removes every `pending` transaction tied to that `import_id` in one action rather than requiring row-by-row deletion.

### Worker Scheduling Expectations

- `run_pending_imports_once(engine)` is the core unit: finds `imports` rows with status `pending` and an uploaded file, processes each via the appropriate parser, and updates status. It has no scheduling logic itself, so tests call it directly and synchronously — no real scheduler needs to run for tests to be deterministic.
- `family_cfo_scheduler`'s `Worker` wraps that function in an APScheduler `IntervalTrigger` for real deployments (Docker Compose, later milestone) and adds bounded retry: a failed job increments `imports.retry_count`, and after 3 attempts the import moves to `failed` rather than retrying forever.
- No message broker (Redis/RabbitMQ) — APScheduler runs in-process against the same database, consistent with keeping the self-hosted deployment simple (ADR 0006).
- CSV processing is not fully transactional: rows are inserted one at a time, not as a single all-or-nothing batch, so a retry after a partial failure can re-process rows already inserted by the failed attempt. This is deliberately bounded rather than silent — the existing duplicate-detection check runs on every insert attempt including retries, so a re-inserted row surfaces as `possible_duplicate = true` for a human to resolve via `discard`, the same safety net as any other duplicate. True per-row idempotency (a processing ledger) is future work if this proves to matter in practice.

### API Behavior

- `POST /api/v1/imports` requires `bearerAuth`, any household role, and returns `201` with the created `ImportRecord` (`status: pending`).
- `POST /api/v1/imports/{id}/file` requires `bearerAuth`, accepts `multipart/form-data`, and returns `202` — processing is asynchronous (the scheduler, not the request, parses the file).
- `GET /api/v1/imports` requires `bearerAuth`, any household role, returns the household's imports.
- `POST /api/v1/imports/{id}/apply` and `POST /api/v1/imports/{id}/discard` require `bearerAuth` and are limited to `owner`/`adult` (same rationale as goal creation: these mutate household financial data).
- `POST /api/v1/documents` requires `bearerAuth`, any household role, accepts `multipart/form-data`, and returns the created `document` plus its `document_extractions` result synchronously (extraction is fast and local — no worker hop needed for a single document).
- `GET /api/v1/documents` requires `bearerAuth`, any household role.

### Data Model Changes

- Add `imports`: `id`, `household_id`, `account_id` (nullable FK), `source_type` (`csv`/`pdf`/`ofx`/`qfx`), `filename`, `status` (`pending`/`processing`/`needs_review`/`completed`/`discarded`/`failed` — adds `discarded` to the M0-baseline enum), `error_message` (nullable), `skipped_row_count`, `retry_count`, `created_at`, `updated_at`.
- Add `import_files`: `id`, `import_id`, `storage_path`, `content_type`, `size_bytes`, `created_at`.
- Add `documents`: `id`, `household_id`, `content_type`, `storage_path`, `created_at`.
- Add `document_extractions`: `id`, `document_id`, `extraction_type` (`pdf_text`/`ocr`), `text`, `structured_fields_json`, `confidence`, `warnings_json`, `created_at`.
- Staged files live on local disk under a configurable directory (`FAMILY_CFO_IMPORT_STAGING_DIR`), matching the Docker spec's planned "Import staging" volume; `storage_path` is a relative path within that directory, never an absolute host path, so it stays portable across environments.
- Add a nullable `transactions.import_id` foreign key (additive migration, not a rewrite of M2's original `transactions` migration) so `apply`/`discard` can scope their bulk update/delete to exactly the rows a given import created, rather than every `pending` transaction in the household.

### Security Impact

- Uploaded file bytes and extracted document text are `Restricted`/`Sensitive` per the security model (financial documents, receipts) — never logged. Log lines reference only `import_id`/`document_id`, byte counts, and status, matching the pattern already established for prompts (M4) and purchase details (M3).
- Staging storage and extracted text inherit the existing open threat-model question about database/backup encryption rather than resolving it — noted explicitly as a non-goal above, not silently skipped.
- `apply`/`discard` are role-gated the same way goal creation is, since both mutate household financial records.

### Test Expectations

- `ocr-worker`: unit tests for `PdfTextExtractionAdapter` against `fpdf2`-generated synthetic PDF fixtures (dev-only dependency, never a runtime one) covering normal text extraction and a fixture with no extractable text (low-confidence path). Unit tests for `DeterministicOcrAdapter` covering the known-fixture path and the unknown-content fallback.
- `scheduler`: unit tests for `run_pending_imports_once` covering successful CSV processing, a malformed-row-skips-not-fails case, duplicate flagging, and the retry-then-fail-after-3-attempts path — all against an in-memory SQLite engine, no real scheduler thread involved.
- `apps/api`: repository tests for import/document persistence; API integration tests for the full CSV lifecycle (`create` → `upload file` → synchronously invoke the job function, since there's no running scheduler in tests → `apply`/`discard`), `401`/`403` paths, and a document upload/extraction round trip.
- A redaction test confirms uploaded file contents and extracted document text never appear in log output, following the same pattern as M3's purchase-advisor redaction test.

### Documentation Impact

- Update `apps/api/README.md` with the imports/documents API surface and staging-directory configuration.
- Add `services/ocr-worker/README.md` and `services/scheduler/README.md` documenting the adapter interface, the real vs. deterministic-test adapters, and the worker's retry behavior.
- Update `database/README.md` with the new tables and the staging-directory convention.
- Update the implementation task checklist as M7 tasks complete.

## M8: Reports and Backups

- Weekly report
- Monthly report
- Encrypted backups
- Restore test

### Scope

- Add a `reports` table and a report generation service that reuses existing M2 financial-engine calculations (cash flow, budget summary) and M2/M3 persistence (accounts, goals, transactions) to produce two report types: `weekly` and `monthly`. Each report captures wins, risks, unusual spending, goal progress, and recommended actions, plus a narrative explanation generated through the same `LlmExplanationAdapter`/guardrail-fallback pattern M3/M4 already use for the purchase advisor.
- Add `POST /api/v1/reports/generate` (on-demand generation) and `GET /api/v1/reports` / `GET /api/v1/reports/{id}` (list/detail).
- Add a scheduled report job, following M7's `family_cfo_scheduler` pattern exactly: a directly-callable `run_scheduled_reports_once(engine, report_type, ...)` wrapped by the existing `family_cfo_scheduler.Scheduler`'s `IntervalTrigger`-only `Job` abstraction (no new trigger type). It polls daily and generates one report per household per period, idempotent on `(household_id, report_type, period_start)` — so a missed poll (container downtime) self-heals on the next poll instead of requiring true cron semantics.
- Add a `backup_jobs` table and a `BackupAdapter` protocol (ADR 0007 replaceable-component pattern): one real adapter (`PgDumpBackupAdapter`, shells out to `pg_dump`/`pg_restore`) and one deterministic test adapter (`SqliteFileBackupAdapter`, file-copy based) for exercising the same encrypt/dump/retention/restore code paths in tests without a live PostgreSQL server.
- Every backup archive (database dump plus a tar of the `FAMILY_CFO_IMPORT_STAGING_DIR` document/import staging tree) is symmetrically encrypted with a Fernet key (`cryptography` package) sourced from `FAMILY_CFO_BACKUP_ENCRYPTION_KEY`; there is no unencrypted-backup code path.
- Add `POST /api/v1/backups` (on-demand backup, synchronous — same "small self-hosted scale" reasoning M7 used for synchronous document extraction), `GET /api/v1/backups` (list/status), and `POST /api/v1/backups/{id}/restore` (decrypt, restore database and documents from the named backup — a destructive operation by definition).
- Add a scheduled backup job (daily, via the same worker/scheduler pattern) and a retention policy (`FAMILY_CFO_BACKUP_RETENTION_COUNT`, default 7) that deletes the oldest completed backups once the count is exceeded.
- Document backup volumes, key handling/generation, and the restore procedure in `database/README.md` and `apps/api/README.md`.

### Non-Goals

- No annual report. The roadmap bullets for M8 name only weekly and monthly; annual is tracked as backlog in `docs/specs/12-implementation-tasks.md` alongside the existing debt-payoff/retirement backlog, the same "narrower than the PRD's aspirational list" scope call M3 made for retirement projections.
- No Angular "Reports" or "Backups" page upgrade. M5 left both as explicit placeholder shells; this milestone is backend-only, the same split M6 (backend first, dashboard-gap-fix follow-up) and M7 (backend-only, Import Review UI deferred) already used. The dashboard-side work is identified follow-up, not silently dropped.
- No real PostgreSQL server in this sandboxed development environment. `PgDumpBackupAdapter` is implemented against the real `pg_dump`/`pg_restore` CLI contract (command construction, exit-code/error handling) but is not exercised against a live Postgres instance in CI; `SqliteFileBackupAdapter` exercises the identical encryption/retention/restore seam against a real (if smaller-scope) database file. This is the same "test the seam, not the vendor binary" approach M4 used for vLLM (HTTP layer mocked, no real vLLM deployment) and M7 used for OCR (deterministic adapter, no real Tesseract).
- No encrypted Qdrant backup. No vector database is used anywhere in this codebase yet (M4's AI orchestrator calls the runtime adapter directly with calculation context, no retrieval/embedding step), so there is nothing to back up; this is a real non-goal, not a deferred one.
- No backup-key recovery or rotation mechanism. The threat model's open question ("Backup key recovery flow") is not resolved here — M8 requires and documents a key for every backup/restore operation (a genuine new control), but losing `FAMILY_CFO_BACKUP_ENCRYPTION_KEY` makes existing backups permanently unrecoverable, and rotating it only affects backups taken after the rotation. This is documented as an operator responsibility, not silently glossed over.
- No automatic periodic restore-verification job running against a scratch database in production. Restore correctness is proven by the test suite's real dump-then-restore round trip (`SqliteFileBackupAdapter`), consistent with keeping the self-hosted worker simple (ADR 0006); a production "canary restore" job is a reasonable future addition, not required for M8's "Restore test" bullet.
- No malware/antivirus scanning of restored files, and no multi-target restore (restore always targets the same database the backup was taken from).
- No email/webhook alerting on backup failure. `backup_jobs.status`/`error_message` are queryable via `GET /api/v1/backups`, satisfying the "or dashboard status" half of the implementation checklist's "alert or dashboard status" item; a notification channel is future work.
- No user-configurable report schedule or report content customization. The weekly/monthly cadence is fixed.

### Report Generation Behavior

- Report content is assembled from the same deterministic financial-engine calculations M2/M3 already expose (`calculate_cash_flow`, `calculate_budget_summary`) plus goal progress read from `goals`/`account_balances`. "Wins," "risks," and "unusual spending" are produced by rule-based heuristics over that data (e.g. a budget category under its target is a win; a category exceeding its target or a period-over-period spend increase past a fixed threshold is a risk; a transaction category with no prior spending history above a fixed minor-unit threshold is flagged unusual) — never an LLM guess at the numbers themselves, matching the existing guardrail principle that numeric claims must trace back to a calculation output.
- "Recommended actions" are deterministic templated strings keyed to which risks/wins fired (mirroring the purchase advisor's deterministic-explanation-stub fallback from M3), not free-form LLM generation.
- The narrative explanation text wrapping the structured wins/risks/recommendations reuses the M4 `LlmExplanationAdapter` and guardrail validation unchanged: any generated sentence containing a number not traceable to the report's own calculation outputs is discarded in favor of the deterministic template text, exactly as M4 already does for purchase-advisor explanations.
- A report is keyed by `(household_id, report_type, period_start)`; regenerating the same period is idempotent (updates the existing row) rather than creating duplicates, so the scheduled job can safely run more than once for the same period without operator intervention.
- `calculate_cash_flow` always normalizes recurring income/bills to a monthly figure (its own existing 12-months-per-year assumption from M2, unchanged here). For a weekly report, that monthly figure is scaled down by a fixed 7/30 fraction before being handed to `calculate_budget_summary` alongside the period's actual category spend, so a weekly report's "remaining"/net cash flow is period-scoped rather than comparing seven days of actual spending against a full month of budgeted income and bills. This 7/30 approximation is recorded as a report assumption, the same way M2's calculations already document their own normalization assumptions.

### Backup Job Lifecycle

1. `pending` — `POST /api/v1/backups` or the scheduled job created the `backup_jobs` row.
2. `running` — the adapter has started `dump_database`/staging-tree tar/encrypt.
3. `completed` — the encrypted archive was written to `FAMILY_CFO_BACKUP_DIR`; `size_bytes` and `completed_at` are set.
4. `failed` — any step raised; `error_message` holds a non-sensitive summary (adapter name and error class, never dump content), matching the `imports.error_message` convention from M7.
- Retention runs after every successful backup: completed backups beyond `FAMILY_CFO_BACKUP_RETENTION_COUNT` (oldest first) are deleted from disk and marked accordingly; a `failed` backup is never counted toward or deleted by retention, so a run of failures doesn't silently erase the last good backup.
- A restore reverts the *entire* database, including `backup_jobs` itself: the row for the backup being restored from necessarily reads `running` (its state at the moment the dump was taken), not `completed` (written after). This is an inherent property of a whole-database backup, not a bug -- restoring from backup N always also rolls back any `backup_jobs`/retention bookkeeping that happened after backup N was taken.

### Backup Adapter Interface

- `BackupAdapter` protocol: `dump_database(destination: Path) -> None` and `restore_database(source: Path) -> None`, deliberately narrow (no document-tree handling inside the adapter — that step is identical regardless of database backend, so it lives once in the shared backup service, not duplicated per adapter).
- `PgDumpBackupAdapter` (real): shells out to `pg_dump --format=custom` / `pg_restore --clean` against `settings.database_url` when it is a `postgresql` URL.
- `SqliteFileBackupAdapter` (test-only): copies the SQLite database file directly; used only when `settings.database_url` is a file-based `sqlite` URL (never `:memory:`, which cannot be file-copied — backup/restore tests use a `tmp_path` SQLite file, not the in-memory fixture the rest of the suite uses).
- The backup service selects an adapter from `settings.database_url`'s scheme, the same "replaceable component selected by a settings/content-type value" pattern already used for `RuntimeAdapter` (M4) and `DocumentExtractionAdapter` (M7).

### Encryption and Key Handling

- `FAMILY_CFO_BACKUP_ENCRYPTION_KEY` (env var) holds a Fernet key (url-safe base64, 32 bytes — `Fernet.generate_key()` output). It is required at backup and restore time; a missing key raises a configuration error and aborts the job as `failed` rather than writing an unencrypted archive.
- The encrypted archive bundles the database dump and the document-tree tar as two named entries so a single key encrypts/decrypts both in one operation.
- Key generation, storage (env file or Docker secret, matching the pattern `docs/specs/10-docker-spec.md` already specifies for other secrets), and the "losing the key means permanent data loss" consequence are documented in `database/README.md`.

### API Behavior

- `POST /api/v1/reports/generate` requires `bearerAuth` and `owner`/`adult` (mutates household state, same bar as goal creation and import apply/discard).
- `GET /api/v1/reports` and `GET /api/v1/reports/{id}` require `bearerAuth`, any household role.
- `POST /api/v1/backups`, `GET /api/v1/backups`, and `POST /api/v1/backups/{id}/restore` require `bearerAuth` and `owner` only — matching the stricter bar already used for `PUT /api/v1/ai/runtime` and paired-device revocation, since backup/restore is a whole-household administrative action, not a single-record mutation.

### Data Model Changes

- Add `reports`: `id`, `household_id`, `report_type` (`weekly`/`monthly`), `period_start`, `period_end`, `generated_at`, `summary_json` (wins/risks/unusual_spending/goal_progress/recommended_actions), `explanation_text`, `model_version` (nullable), `prompt_version` (nullable), `calculation_version`.
- Add `backup_jobs`: `id`, `status` (`pending`/`running`/`completed`/`failed`), `storage_path` (relative path under `FAMILY_CFO_BACKUP_DIR`, portable across environments like `import_files.storage_path`; cleared, not the row deleted, when retention prunes the file), `size_bytes` (nullable until completed), `error_message` (nullable), `started_at`, `completed_at` (nullable), `pruned_at` (nullable — set when retention deletes the on-disk file so `GET /api/v1/backups` still shows prior backup history), `created_at`.
- Add `FAMILY_CFO_BACKUP_DIR` (default `./data/backups`) and `FAMILY_CFO_BACKUP_RETENTION_COUNT` (default `7`) settings, mirroring the `FAMILY_CFO_IMPORT_STAGING_DIR` configuration pattern from M7.

### Security Impact

- Report content (`summary_json`, `explanation_text`) is `Sensitive` per the security model (transactions/goals/AI conversation-adjacent content) and is logged only by `report_id`/`report_type`/status, never field content, matching the existing prompt/purchase-detail logging convention.
- Backup archives are `Restricted` (financial documents, bank data by way of the database dump) and are always encrypted at rest; `FAMILY_CFO_BACKUP_ENCRYPTION_KEY` itself is never logged and never persisted to the database.
- `backup_jobs.error_message` is restricted to adapter/error-class summaries — never raw dump bytes, file paths outside the configured backup directory, or connection strings.
- Restore is destructive by nature (it replaces the current database and document tree); it is gated to `owner` and documented as such, with no additional in-API confirmation step — the two-step "are you sure" belongs in the (future) dashboard UI, not the API contract.

### Test Expectations

- Financial-engine-adjacent report logic: unit tests for the wins/risks/unusual-spending heuristics against synthetic transaction/goal fixtures, covering at least one case per category (win, risk, unusual, none-triggered).
- Report generation service: unit tests confirming guardrail fallback (a numeric hallucination in the mocked explanation adapter's output is discarded in favor of the deterministic template, same pattern as the M3/M4 purchase-advisor guardrail tests) and idempotent regeneration for the same `(household_id, report_type, period_start)`.
- `GET`/`POST /api/v1/reports*`: integration tests for generate/list/detail, role gating (`owner`/`adult` for generate, any role for read), and the OpenAPI contract check.
- `backup-adapter` unit tests: `SqliteFileBackupAdapter` dump/restore round trip against a `tmp_path` SQLite file (not `:memory:`); `PgDumpBackupAdapter` command-construction/error-handling tests using a stubbed subprocess call (no real `pg_dump` binary required in CI, matching how M4 mocks the vLLM HTTP layer).
- Encryption tests: a backup archive is unreadable without the key; decrypting with the correct key reproduces the original dump and document tree byte-for-byte.
- Retention tests: creating more than `FAMILY_CFO_BACKUP_RETENTION_COUNT` completed backups deletes only the oldest excess, never a `failed` job, never more than necessary.
- Restore verification test (the roadmap's "Restore test" bullet): full round trip — seed synthetic household data, back up, mutate/delete the data, restore, assert the restored data matches the original seed exactly. This is the concrete implementation of that bullet; there is no separate production restore-canary job (see Non-Goals).
- `POST /api/v1/backups*`: integration tests for on-demand backup, list, restore, and `owner`-only role gating.
- Scheduler tests: `run_scheduled_reports_once`/the daily backup job tested directly and synchronously (no real APScheduler trigger needs to fire in tests), following the exact pattern `run_pending_imports_once` established in M7.

### Documentation Impact

- Update `database/README.md`: new tables (`reports`, `backup_jobs`), backup volume location, key generation/storage instructions, and the restore procedure.
- Update `apps/api/README.md`: new endpoints, `FAMILY_CFO_BACKUP_DIR`/`FAMILY_CFO_BACKUP_RETENTION_COUNT`/`FAMILY_CFO_BACKUP_ENCRYPTION_KEY` settings, and the `worker` target's expanded responsibilities (imports + reports + backups).
- Update `shared/openapi/family-cfo.v1.yaml` with the new `Reports`/`Backups` paths and schemas.
- Update `docs/specs/README.md`'s Acceptance State with M8's implemented/non-goal summary, following the exact phrasing convention used for M2–M7.
