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

## M9: Household Setup, Data Management, and Audit

- Household/owner bootstrap and membership management
- Account, transaction, bill, and income write APIs
- `audit_events` for every sensitive mutation

> Context: this milestone closes a gap surfaced during a post-M8 spec-kit audit, not part of the original roadmap. M2 deferred "account, transaction, bill, income write APIs" and "user registration/household-membership management" to its non-goals, and the schema spec (`docs/specs/05-database-schema.md`) lists an `audit_events` table, but no milestone owned any of it — the product could only be populated via CSV import or seeded fixtures, and nothing wrote the promised audit log. M9 makes the app writable and auditable through the API.

### Scope

- Bootstrap a self-hosted instance: `POST /api/v1/households` creates a household, its first `owner` user, and the owning membership in one call, then returns an `AuthSession` (the same shape `POST /api/v1/auth/sessions` returns) so first-run setup can proceed without seeded fixtures. This is the "onboarding is currently login-only, there is no signup" limitation M5 documented, now resolved at the API layer.
- Household membership management (`owner` only): `POST /api/v1/household/members` creates an additional user (`adult`/`viewer`/`child`) with a membership, `PATCH /api/v1/household/members/{user_id}` edits a member's role, `DELETE /api/v1/household/members/{user_id}` removes a membership (and revokes that user's active auth sessions, mirroring paired-device revocation from M6). `GET /api/v1/household/members` lists members for any household role.
- Account writes (`owner`/`adult`): `POST`/`PATCH`/`DELETE /api/v1/accounts`, plus `POST /api/v1/accounts/{id}/balances` to record a new `account_balances` row (balances are append-only history, consistent with how M2 already reads "latest balance per account"; there is no balance update/delete).
- Transaction writes (`owner`/`adult`): `POST`/`PATCH`/`DELETE /api/v1/transactions` for manual entry, editing, and removal. Manually-created transactions default to `review_state = 'reviewed'` (a human is entering them directly, unlike a CSV import's `pending` rows) and carry `import_source = null`.
- Bill writes (`owner`/`adult`): `POST`/`PATCH`/`DELETE /api/v1/bills`.
- Income writes (`owner`/`adult`): `POST`/`PATCH`/`DELETE /api/v1/income`.
- Add the `audit_events` table and write an audit row for every sensitive mutation introduced here (create/update/delete of accounts, balances, transactions, bills, income, members, role changes, plus household bootstrap). Expose `GET /api/v1/audit` (`owner` only). Audit rows record actor, action, entity type/id, and a non-sensitive summary — never the raw financial values or credentials that changed.

### Non-Goals

- No general scenario-planning write API ("can we retire at 55", "should we refinance"). Scenarios are still created only internally by the purchase advisor; a user-facing scenario CRUD/planning API remains backlog (`docs/specs/12-implementation-tasks.md`), unchanged by M9. This keeps M9 focused on the concrete household-data resources M2 deferred, not the open-ended planning surface.
- No public/open registration or multi-tenant SaaS semantics. `POST /api/v1/households` is a self-hosted bootstrap for a trusted local network (ADR 0006); it is not rate-limited, email-verified, or CAPTCHA-gated, and the deployment is expected to sit behind the same private network the rest of the stack does. A future milestone can add first-run lockout (refuse bootstrap once any household exists) if a deployment needs it — noted, not built here.
- No retroactive audit backfill for mutations that already happened before M9 (auth logins, pairing, AI-runtime changes, imports apply/discard, report generation, backups). M9 introduces the `audit_events` table and audits the mutations it adds; extending audit coverage to the other existing mutation points is a tracked follow-up (see Documentation Impact), not silently assumed done.
- No soft-delete/undo or per-field change history. Deletes are hard deletes; the audit row records that a delete happened and the entity id, not a full before/after snapshot (that is the domain of backups/restore from M8, not the audit log).
- No password reset, email flows, or self-service credential rotation for members — the `owner` sets a member's initial password at creation, and rotation is Release-Readiness security work.
- No Angular UI for any of this — that is M11. M9 is backend + OpenAPI only, the same backend-first split M6/M7/M8 used.

### Referential Integrity and Delete Behavior

- Deleting an account is rejected with `409` if any transaction, bill (via `account_id`), or import references it — the caller must reassign or delete those first. This avoids orphaning financial history behind a silent cascade.
- Deleting a transaction that belongs to an import is allowed (it is just a row); the import's `apply`/`discard` lifecycle from M7 is unaffected.
- Deleting a member who is the household's last `owner` is rejected with `409` — a household must always have at least one owner. A member cannot delete or role-demote themselves out of the last-owner position.
- `PATCH` endpoints accept partial updates (only the provided fields change) and validate currency/type/enum values the same way the M2 read models and CHECK constraints already do.

### API Behavior

- All write endpoints require `bearerAuth`. Household-data writes (accounts, transactions, bills, income) require `owner`/`adult` (the same bar M2 set for goal creation); membership management and `GET /api/v1/audit` require `owner` (the same bar M4/M6/M8 set for admin actions). `POST /api/v1/households` is unauthenticated (it is the bootstrap that creates the first credential).
- Every mutation returns the created/updated resource in the same schema its M2 read endpoint already uses (e.g. `POST /api/v1/accounts` returns `Account`); deletes return `204`.
- All new list/detail reads reuse the existing M2 response schemas; new create requests get `*CreateRequest` schemas and updates get `*UpdateRequest` schemas (all-optional fields) in the shared OpenAPI contract.
- Structured `{ "error": { "code", "message", "details" } }` errors as everywhere else; `409` for the referential-integrity conflicts above, `404` for unknown ids, `403` for role violations.

### Data Model Changes

- Add `audit_events`: `id`, `household_id`, `actor_user_id` (nullable — the household-bootstrap actor does not exist until the same transaction), `action` (e.g. `account.created`, `member.role_changed`), `entity_type`, `entity_id` (nullable), `summary` (non-sensitive text), `created_at`.
- No new columns on existing tables. `transactions`, `bills`, `income_sources`, `accounts`, `account_balances`, `households`, `users`, `household_memberships` already have every column these writes need (M1–M2 schema); M9 only adds write paths over them plus the one new audit table.

### Security Impact

- Write authorization is enforced by the same `require_role` dependency used since M2; the last-owner and self-demotion guards prevent a household from locking itself out or escalating privilege.
- Audit rows are `Internal` per the security model and must never contain `Restricted`/`Sensitive` values — no amounts, balances, passwords, or tokens; only ids, action names, and a short non-sensitive summary. This is asserted by a test.
- Member creation sets a password hash via the existing `security.hash_password`; raw passwords are never logged or stored, consistent with M2 auth.
- Household bootstrap creates a credential from an unauthenticated call; this is acceptable only because the deployment is a trusted local network (ADR 0006), and the non-goal above records the "lock bootstrap after first household" option for deployments that need it.

### Test Expectations

- Repository unit tests for each new create/update/delete, the append-only balance insert, membership role changes, and the last-owner/self-demotion guards.
- API integration tests per resource: create→read-back, update, delete, `403` for the wrong role, `404` for unknown ids, and the `409` referential-integrity/last-owner conflicts.
- Household bootstrap test: `POST /api/v1/households` with no auth creates household+owner+membership and returns a working session that can immediately call a protected route.
- Audit tests: a mutation writes exactly one `audit_events` row with the right `action`/`entity_type`; a test asserts no audit `summary` contains a known sensitive value (amount/password) seeded into the mutation.
- OpenAPI contract check continues to pass with the new paths/schemas.

### Documentation Impact

- Update `apps/api/README.md` with an "M9 Scope" section: the write endpoints, the bootstrap flow (replacing the "login-only, no signup" caveat), membership management, and audit.
- Update `database/README.md` with the `audit_events` table and note that extending audit coverage to pre-M9 mutation points (auth, pairing, imports apply/discard, reports, backups) is tracked follow-up.
- Update `shared/openapi/family-cfo.v1.yaml` with the new write paths, `*CreateRequest`/`*UpdateRequest` schemas, `AuditEvent`/`AuditEventListResponse`, and household/member schemas.
- Update `docs/specs/README.md` Acceptance State with M9's summary.
- Add a tracked Release-Readiness/backlog item for "extend audit coverage to all sensitive mutations" so the partial coverage is not silently forgotten.

## M10: Conversation History

- `conversations` + `conversation_messages` persistence
- Chat turns stored and re-readable
- Conversation list and detail APIs

> Context: another gap surfaced by the post-M8 audit. The schema spec lists `conversations` and `conversation_messages`, the domain model names "Conversation" as an aggregate boundary, and both M4 and M6 explicitly deferred conversation history to "a later milestone" — but no later milestone existed. M6 chat computes a bounded response per call and persists only a `recommendations` row, discarding the message thread. M10 adds the promised persistence so a household can revisit past chats.

### Scope

- Add `conversations` (a titled thread owned by a household) and `conversation_messages` (the ordered user/assistant turns within it).
- Change `POST /api/v1/chat/messages` (from M6) to persist the thread: when called with no `conversation_id`, create a new conversation (titled from the first message, truncated) and store the user message plus the assistant response; when called with an existing `conversation_id`, append to it. The response shape (`ChatResponse`) is unchanged, so the M6 contract and the Angular client keep working — M10 is additive.
- Add `GET /api/v1/conversations` (list the household's conversations, newest first, with title/timestamps/message count) and `GET /api/v1/conversations/{id}` (the full ordered message thread), both available to any household role.
- Store the assistant turn's `recommendation_id` on the assistant message so a stored turn still links to its `recommendations` audit row (the numeric grounding M6 already produces), keeping the "every numeric claim traces to a calculation" guarantee intact across history.
- Add `DELETE /api/v1/conversations/{id}` (`owner`/`adult`) so a household can remove a thread (and its messages) — the privacy escape hatch for "don't keep this."

### Non-Goals

- No change to *what* the assistant computes. M10 persists the same bounded, deterministic-grounded response M6 already returns; it does not add multi-turn context-carrying, retrieval, memory, or free-form conversational reasoning. Feeding prior turns back into the model as context is explicitly future work (it interacts with the vLLM/retrieval work that has no real runtime yet).
- No vector store / embeddings / semantic search over history — that is the still-unscoped Qdrant work, tracked separately, not introduced here.
- No editing of stored messages (append-only thread; delete-whole-conversation is the only mutation besides appending).
- No Angular chat UI — the dashboard has no chat page today, and adding one is out of M10's backend-only scope (it can be folded into M11 or a later UI pass; noted, not silently dropped).
- No raw-prompt or raw-model-output persistence beyond the already-guardrail-validated assistant text — the same M4 rule (never persist raw prompts/completions) still holds; `conversation_messages` stores the user's message and the validated assistant answer, not the internal prompt.

### Conversation and Message Behavior

- A `conversation` has a `title` (derived from the first user message, truncated to a fixed length), `created_at`, and `updated_at` (bumped on each new turn so the list can sort by recency).
- A `conversation_message` has a `role` (`user`/`assistant`), `content` (text), an optional `recommendation_id` (set on assistant turns), a monotonic `sequence` within the conversation, and `created_at`.
- Both user message and assistant response for a single `POST /chat/messages` call are written in one transaction, so a thread never ends on a dangling user message with no answer.
- Deleting a conversation hard-deletes its messages; the linked `recommendations` rows are left intact (they are the audit trail and may be referenced elsewhere) — only the conversational wrapper is removed.

### API Behavior

- `POST /api/v1/chat/messages` (unchanged auth: any household role) now additionally persists the thread and includes the (possibly newly-created) `conversation_id` in `ChatResponse` — which the field already carries, so no schema change to the response.
- `GET /api/v1/conversations` and `GET /api/v1/conversations/{id}` require `bearerAuth`, any household role, household-scoped.
- `DELETE /api/v1/conversations/{id}` requires `owner`/`adult`; returns `204`; `404` for unknown/other-household ids.

### Data Model Changes

- Add `conversations`: `id`, `household_id`, `created_by_user_id`, `title`, `created_at`, `updated_at`.
- Add `conversation_messages`: `id`, `conversation_id`, `role` (`user`/`assistant`), `content`, `recommendation_id` (nullable FK to `recommendations`), `sequence`, `created_at`.
- No changes to existing tables.

### Security Impact

- Conversation content is `Sensitive` (AI conversation history, per the security model) and is household-scoped on every read/delete; a household can never read another household's threads.
- Logging references only `conversation_id`/`message_id`/`recommendation_id`, never message content — same convention as M4 prompts and M7 file contents.
- `DELETE` gives the household a concrete way to purge stored chat, supporting the privacy-first posture.

### Test Expectations

- Repository tests: create-conversation-on-first-message, append-on-existing, `sequence` ordering, delete cascades messages, household scoping.
- API tests: a first `POST /chat/messages` creates a conversation and returns its id; a second call with that id appends; `GET /conversations` lists it; `GET /conversations/{id}` returns both turns in order with the assistant turn carrying a `recommendation_id`; `DELETE` removes it and a subsequent `GET` is `404`; cross-household access is `404`; role gating on delete.
- A test asserts the assistant message's `recommendation_id` resolves to a real `recommendations` row (grounding preserved).
- OpenAPI contract check passes with the new `Conversations` paths/schemas.

### Documentation Impact

- Update `apps/api/README.md` with an "M10 Scope" section describing persisted chat and the conversation endpoints, and updating the M6 chat description to note threads are now stored.
- Update `database/README.md` with the `conversations`/`conversation_messages` tables.
- Update `shared/openapi/family-cfo.v1.yaml` with the `Conversations` tag, paths, and `Conversation`/`ConversationMessage`/`ConversationListResponse`/`ConversationDetail` schemas.
- Update `docs/specs/README.md` Acceptance State with M10's summary.

## M11: Dashboard Data Entry and Review UIs

- Real Angular pages replacing the placeholder shells
- Data-entry forms for the M9 write APIs
- Review UIs for imports, reports, and backups

> Context: the final gap from the post-M8 audit. M5 shipped four pages — Transactions, Imports, Reports, Backups — as explicit placeholder shells, and M7/M8 each deferred "the Angular page upgrade" as "identified follow-up work," but no milestone ever owned turning those shells into real UIs. M11 does, now that their backends (M7 imports, M8 reports/backups) and their write APIs (M9) exist.

### Scope

- **Transactions** (`apps/web` Transactions page): replace the shell with a real transaction list (paged, from `GET /api/v1/transactions`) plus create/edit/delete forms backed by the M9 transaction write APIs, and account/bill/income management surfaced from the same page group so a household can actually enter its data from the dashboard. Manual entry is gated in the UI to `owner`/`adult` (matching the API), with read-only display for `viewer`.
- **Accounts data entry**: extend the existing real M5 Accounts page with create/edit/delete and "record balance" actions (M9 account writes + `POST /accounts/{id}/balances`), rather than a separate page.
- **Imports review** (Imports page): replace the shell with the real import lifecycle UI — register an import, upload a CSV/PDF file, watch status, and review the resulting pending transactions with apply/discard (`owner`/`adult`), plus a documents/extraction view. This is the "Import Review page upgrade" M7 deferred.
- **Reports** (Reports page): replace the shell with generate (weekly/monthly, `owner`/`adult`) and a report list/detail rendering wins/risks/unusual-spending/goal-progress/recommended-actions and the narrative explanation. This is the "Reports page upgrade" M8 deferred.
- **Backups** (Backups page): replace the shell with the real backup list, create-backup, and restore actions (`owner` only), with a confirmation dialog on restore (the destructive-action confirmation M8 said "belongs in the dashboard UI").
- **Household members** (Users & Devices page): extend the existing M6 pairing/device page with member list/create/role-edit/remove (M9 membership APIs, `owner`), so the "Users" half of that page becomes real alongside the already-real "Devices" half.
- Regenerate the Angular OpenAPI client so all the M9 write and M10 (if surfaced) shapes are available, and add every new `ApiService` wrapper method following the established M5 DI-testable pattern.

### Non-Goals

- No chat UI. M10 persists conversations at the API layer, but a dashboard chat page is not in M11 (the dashboard has never had one); it is noted as future UI work, not silently dropped. M11 covers the four shells M5 explicitly created plus the data-entry the write APIs unlock.
- No onboarding/first-run *setup wizard* UI around `POST /api/v1/households`. M5's onboarding is a login screen; wiring a full self-hosted "create your household" wizard is a distinct UX effort deferred to a later UI pass (the API exists after M9; the wizard is future work) — recorded here, not assumed.
- No new backend endpoints — M11 is frontend-only against M7/M8/M9 APIs. Any shape mismatch found during UI work is fixed in the shared OpenAPI contract first (source of truth), then the client regenerated, but no new server behavior is added.
- No redesign of the M5 shell/navigation or the already-real Overview/Goals/AI Runtime pages beyond adding the new actions.
- No charts/visualizations beyond what cleanly renders the existing structured data (tables, status badges, the report summary lists). Rich dashboards/graphs are a later polish pass.

### UI Behavior and Role Gating

- Every write action is gated in the UI to the same roles the API enforces (`owner`/`adult` for household-data writes, `owner` for members/backups), and the API remains the real authority — the UI gating is convenience, not the security boundary (a `viewer` who forges a request still gets `403`).
- Forms validate money as major-unit input and convert to integer minor units before sending (never floats reaching the API), consistent with the `Money` contract everywhere else.
- Destructive actions (delete transaction/account/bill/income/member, discard import, restore backup) show a confirmation step; restore's confirmation states plainly that it replaces all current data.
- Loading/empty/error states use the same patterns M5 established (the `resource()` API and structured-error message rendering).

### Test Expectations

- Vitest component/unit tests for each new page's happy path and its role-gated controls (a `viewer` sees read-only; `owner`/`adult` see the write actions), using the `ApiService`-DI mocking pattern from M5 (no `vi.mock()` on relative imports).
- Tests for the money major→minor conversion in the entry forms and for the restore confirmation gate.
- At least one Playwright end-to-end smoke test (opt-in, against a running backend, following M5's `e2e/onboarding.spec.ts` precedent) covering: log in, create an account, add a manual transaction, generate a report, and see it listed.
- `npm run build` and `npm test` clean; the generated client matches the current OpenAPI.

### Documentation Impact

- Update `apps/web/README.md` with the now-real pages and how to run the expanded e2e smoke test.
- Update `docs/specs/README.md` Acceptance State: the M5 note that "reports/transactions/imports/backups/users are placeholder shells" is superseded by M11.
- No `shared/openapi` change expected (frontend-only); if a mismatch forces one, it lands in the contract before the client is regenerated.

## M12: Docker Deployment and Home-Server Packaging

- `docker compose up -d` runs the stack
- Core services containerized; heavy AI/vector services opt-in
- Migrations run on startup; secrets via env file

> Context: implements the `docs/specs/10-docker-spec.md` plan and the "Docker and Deployment" Release-Readiness checklist in `docs/specs/12-implementation-tasks.md`. It is the packaging that makes every prior milestone actually runnable on a home server.

### Scope

- Containerize the three code images the app needs: `api` (FastAPI/uvicorn), `worker` (the background jobs process, same image as `api` with a different command), and `web` (Angular built to static files, served by nginx which also proxies `/api` to the API container). All three build from a repo-root context so the sibling `services/*` packages, `apps/api`, and `database/migrations` are available.
- Add `docker-compose.yml` (repo root) defining the **core** stack — `db` (PostgreSQL), `api`, `worker`, `web` — that comes up with a plain `docker compose up -d`, plus a `docker-compose.dev.yml` override for local development (source bind-mounts, `--reload`, direct API port exposure).
- The `api` container's entrypoint waits for PostgreSQL to accept connections, runs `alembic upgrade head`, then starts uvicorn. The `worker` waits for the database and for the API to have migrated, then starts. Only the `api` runs migrations, so there is no migration race between `api` and `worker`.
- Secrets and configuration come from a `.env` file (repo root, gitignored) with a committed `.env.example` template — the Postgres password, the `FAMILY_CFO_BACKUP_ENCRYPTION_KEY`, and the derived `FAMILY_CFO_DATABASE_URL`. No secret is baked into an image or committed.
- Named volumes for the persistent data the spec lists: `postgres_data`, `backups`, and `import_staging` (the last two shared by `api` and `worker` so uploads staged by the API are visible to the worker and backups are written to a durable location).
- A private bridge network; only the `web` container publishes a host port by default. The `api` port is published only in the dev override (for `curl`/tests), not in the home-server base file.

### Non-Goals

- **vLLM and Qdrant do not run by default.** _(vLLM part superseded by [M17](#m17-turnkey-deployment-ai-on-by-default), which makes the local runtime on by default; Qdrant stays profile-gated.)_ Both were defined behind Compose **profiles** (`--profile ai` for `vllm`, `--profile vector` for `qdrant`), off unless explicitly requested:
  - `vllm` was a wire-able opt-in — it needs a GPU and a multi-gigabyte model download, and the app runs fully without it (the advisor, reports, and chat fall back to the deterministic explanation stub). As of M17 it runs by default and households inherit it automatically; the opt-out is `FAMILY_CFO_AI_ENABLED=false` + `--scale vllm=0`. It is never published to the host.
  - `qdrant` is **honest scaffolding only** — it matches the Docker spec's planned `family-cfo-vector` container, but nothing in the codebase connects to it yet (the vector-store/retrieval work is tracked backlog). It is profile-gated and documented as having no consumer, rather than pretending it is wired up (the same honesty the M5 placeholder shells used).
- No reverse proxy, TLS/HTTPS termination, monitoring, or the "Backup" sidecar container — those are the Docker spec's "Future Containers" and remain Release-Readiness security work (HTTPS is a separate tracked item). The `web` container serves plain HTTP; a home-server operator is expected to front it with their own TLS proxy until that milestone lands.
- No image publishing to a registry, no multi-arch build, no Kubernetes/Swarm manifests — a single-host `docker compose` deployment is the target (ADR 0006).
- No production-grade Postgres tuning, connection pooler (PgBouncer), or read replicas — a single Postgres container with a named volume is the home-server scale.

### Image Design

- `docker/api.Dockerfile`: `python:3.12-slim`; copies `services/*`, `apps/api`, and `database/` preserving the repo layout under `/app` (so `apps/api/alembic.ini`'s `%(here)s/../../database/migrations` path stays valid), installs the API and the five service packages non-editable, and ships `docker/entrypoint-api.sh` / `docker/entrypoint-worker.sh`. One image, two entrypoints.
- `docker/web.Dockerfile`: multi-stage — `node` stage runs `npm ci && npm run build`, then `nginx:alpine` serves `dist/web` and proxies `/api/` to `http://api:8000` via `docker/web-nginx.conf`. The generated client's default `/api/v1` base path works unchanged behind that proxy.
- Entrypoints use a small `pg_isready`/TCP wait loop (no extra dependency) before migrating/starting.

### Data Model Changes

- None. M12 is packaging only; no schema, API, or contract changes.

### Security Impact

- No secrets in images or the compose file — all injected from the gitignored `.env`; `.env.example` documents them with placeholder values only.
- vLLM is never published to the host (private network only), matching the spec's "vLLM should not be exposed publicly by default."
- The API port is unpublished in the home-server base file; only `web` is reachable from outside the Docker network. The database, worker, and (optional) vLLM/Qdrant are internal-only.
- HTTPS is explicitly out of scope (tracked separately); the compose file serves HTTP and the docs tell operators to front it with TLS.

### Test Expectations

- `docker compose config` validates (both the base file and the base+dev override compose).
- The `api` and `web` images build successfully from a clean context.
- The core stack (`db` + `api` + `worker` + `web`) comes up with `docker compose up -d`; the API becomes healthy (`GET /api/v1/health` through the `web` proxy returns `{"status":"ok"}`), migrations have run (a protected route works after seeding), and a real request round-trips against the containerized Postgres.
- vLLM/Qdrant are **not** built or started in verification (GPU/无consumer); their compose definitions are validated by `docker compose config` only. This is the same "validate the seam, don't run the vendor heavyweight" approach M4 (no real vLLM) and M8 (no real Postgres in unit tests) already used.

### Documentation Impact

- Rewrite `docker/README.md` with the real services, profiles, the `up`/dev commands, and the volume list.
- Update the root `README.md` and `apps/api`/`apps/web` READMEs with the `docker compose` quickstart.
- Add `.env.example` documenting every required variable.
- Check off the "Docker and Deployment" items in `docs/specs/12-implementation-tasks.md` that M12 completes; leave reverse-proxy/monitoring/backup-sidecar unchecked (Future Containers).
- Update `docs/specs/README.md` Acceptance State with M12.

## M13: Security Hardening

- HTTPS/TLS at the web tier
- Session logout and token rotation
- Threat-model open questions resolved (ADR 0008)
- Security test coverage and CI scanning

> Context: the first tranche of the "Security and Privacy" Release-Readiness checklist (`docs/specs/12-implementation-tasks.md`). M12 ships plain HTTP and the threat model still lists four open questions; M13 closes the app-to-server transport gap, adds session lifecycle controls, and records the security decisions as an ADR.

### Scope

- **HTTPS/TLS.** The `web` (nginx) container terminates TLS on 443, redirects HTTP (80) to HTTPS, and adds security response headers (HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, a conservative `Content-Security-Policy`). An entrypoint generates a self-signed certificate on first start if none is provided, and an operator can bring their own by mounting a cert/key over `/etc/nginx/certs` (the documented path for a real deployment, alongside fronting with an external TLS proxy). Compose publishes 443.
- **Session logout + token rotation.** Add `DELETE /api/v1/auth/sessions` (log out — revoke the caller's current session) and `POST /api/v1/auth/sessions/refresh` (rotate — revoke the current token and issue a fresh one with a new TTL). Session expiration is already enforced by `get_session_context` (expired or revoked tokens resolve to `401`); M13 makes the TTL configurable (`FAMILY_CFO_SESSION_TTL_HOURS`) and adds explicit tests for expiry, revocation-on-logout, and rotation-invalidates-the-old-token.
- **Security decisions as an ADR.** Add ADR 0008 resolving the threat model's four open questions, and update `docs/security/threat-model.md` to point at it instead of listing them as open.
- **Consolidated security tests.** A `test_security.py` covering the role-based authorization matrix (viewer/child blocked from writes; adult vs owner boundaries), paired-device revocation invalidating device sessions, logging redaction (a password/token in a log line is `[REDACTED]`), and a no-telemetry assertion (the codebase opens no outbound analytics/telemetry connection — grep-level guard plus the documented no-telemetry stance).
- **CI hardening.** Add secret scanning (gitleaks) and a Python dependency vulnerability audit (`pip-audit`) as CI workflows, and wire the currently-uncovered suites (financial-engine, ai-orchestrator, ocr-worker, scheduler, backup, and the Angular unit tests) into CI so every package is gated.

### Non-Goals

- **No public-CA/ACME certificate automation** (Let's Encrypt/certbot). A home server behind a dynamic IP or on a LAN can't always complete an ACME challenge; M13 ships a self-signed default and a bring-your-own-cert mount, and documents fronting with an external TLS proxy (Caddy/Traefik/nginx) for a public deployment. Automated cert renewal is future work.
- **No database encryption-at-rest implemented in the app layer.** ADR 0008 resolves the *open question* by assigning it: at-rest encryption is the host/volume's responsibility (LUKS/encrypted volume) for a self-hosted box, not per-column app encryption, which would break the deterministic-money and audit requirements. This is a documented decision, not a new code path.
- **No backup-key recovery mechanism.** ADR 0008 records the decision that the backup key is operator-managed with no recovery by design (losing it means losing the backups) — consistent with what M8 already documents; M13 formalizes it rather than building key escrow.
- **No OAuth/OIDC/SSO, MFA, or password-reset flows.** Local password auth plus revocable sessions/devices remains the model (ADR 0002-era decision); federated identity is out of scope.
- **No rate limiting, WAF, fail2ban, or intrusion detection.** Those belong to the reverse-proxy/monitoring "Future Containers" tranche, not M13.
- **No mutual-TLS or client-certificate auth**, and no HTTP Strict Transport Security preload-list submission (HSTS header is set, but preloading is an operator choice on a real domain).

### API Behavior

- `DELETE /api/v1/auth/sessions` requires `bearerAuth`; it revokes the session backing the presented token and returns `204`. A subsequent request with that token is `401`.
- `POST /api/v1/auth/sessions/refresh` requires `bearerAuth`; it revokes the current session and returns a new `AuthSession` (new token, new `expires_at`). The old token no longer authenticates.
- Both re-read the bearer token from the `Authorization` header (a new `get_bearer_token` dependency) so they can act on the exact session presented, not just the resolved `SessionContext`.
- No change to existing routes; these are additive.

### Data Model Changes

- None. `auth_sessions` already has `revoked_at` and `expires_at`; logout sets `revoked_at`, refresh revokes the old row and inserts a new one. No migration.

### Security Impact

- TLS closes the "Local Network Attacker interception" threat the threat model names — app-to-server traffic is encrypted end to end at the web tier.
- Token rotation and logout close the "Compromised Device" gap: a user (or owner, via existing device revocation) can invalidate a leaked token immediately rather than waiting out the TTL.
- The security-header set mitigates clickjacking (`X-Frame-Options`), MIME sniffing (`nosniff`), and mixed-content/injection (CSP) against the dashboard.
- The self-signed default means the first-run experience shows a browser warning; documented clearly, with bring-your-own-cert and external-proxy paths for production trust.
- ADR 0008 converts four unresolved threat-model questions into recorded, reviewable decisions.

### Test Expectations

- Auth lifecycle: login → refresh returns a new token and the old token is now `401`; login → logout makes the token `401`; an expired session (seeded with a past `expires_at`) is `401`.
- Security matrix (`test_security.py`): a `viewer` is `403` on representative writes across accounts/transactions/bills/income/members/backups; an `adult` is allowed household-data writes but `403` on owner-only member/backup actions; device revocation invalidates that device's session.
- Redaction: a log record containing `password=hunter2` / `token=abc` is emitted as `[REDACTED]`; a no-telemetry test asserts the app defines no telemetry/analytics client and makes no outbound call on a normal request path.
- Docker: the built web image serves HTTPS (self-signed) on 443 with the expected security headers, and HTTP redirects to HTTPS. Verified with `curl -k`.
- CI YAML validates; the new workflows install and run their scanners/suites.

### Documentation Impact

- Add ADR 0008 and update `docs/adr` index (`docs/specs/02-adrs.md`).
- Update `docs/security/threat-model.md` (open questions → resolved, pointing at ADR 0008) and `docs/specs/06-security-model.md` if control language changes.
- Update `docker/README.md` and `.env.example` with the TLS ports, cert paths/bring-your-own, and `FAMILY_CFO_SESSION_TTL_HOURS`.
- Update `apps/api/README.md` with the logout/refresh endpoints, and `docs/specs/README.md` Acceptance State with M13.
- Check off the corresponding "Security and Privacy" and CI items in `docs/specs/12-implementation-tasks.md`.

## M14: Debt Payoff and Retirement Projections

- Persist per-account debt terms
- Real debt-payoff impact in the purchase advisor
- Deterministic retirement projection + scenario endpoint

> Context: finally owns the "Backlog: Debt Payoff and Retirement Projections" tracked since M3 (`docs/specs/12-implementation-tasks.md`). The PRD promises "deterministic projections for cash flow, retirement, debt payoff, net worth, and savings goals" and a Scenario Planning journey; `calculate_debt_payoff` shipped in M3's backlog but had no persisted inputs and the advisor's `debt` impact is a warning-only placeholder. M14 persists the inputs, makes the debt impact real, and adds retirement projection.

### Scope

- Add nullable `accounts.annual_interest_rate` and `accounts.minimum_payment_minor` columns (only meaningful for liability account types: `credit_card`, `mortgage`, `auto_loan`, `student_loan`, `other_liability`). Account create/update (`owner`/`adult`, from M9) accept them; the `Account` read model exposes them.
- Wire `calculate_debt_payoff` into the purchase advisor: for each liability account that has both terms set, run the payoff simulation and replace the "cannot be modeled without interest rate and payment data" placeholder with a real `debt` impact — months to payoff and total interest for the household's debts, plus the honest note that paying cash reduces capacity for extra payments. Accounts still missing terms are surfaced as a "add terms to model this debt" note, not silently ignored.
- Add `calculate_retirement_projection` to the financial engine: a deterministic monthly compound-growth simulation (current savings + monthly contribution, compounding at `annual_return_rate / 12`) to a retirement age, returning the projected balance and, if retirement expenses are supplied, a simple coverage ratio. Pure function, no DB — same shape as every other engine calculation.
- Add `POST /api/v1/advisor/retirement`: accepts a retirement scenario (current age, retirement age, current savings, monthly contribution, expected annual return, optional annual expenses in retirement), runs the projection, persists a `scenarios` + `recommendations` row, and returns a `Recommendation` (answer/assumptions/impacts/tradeoffs/alternatives/confidence/calculation_refs/warnings), grounded in the calculation.

### Non-Goals

- No general free-form scenario-planning API ("should we refinance?", arbitrary what-ifs). M14 delivers the two concrete PRD projections (debt payoff, retirement); an open-ended scenario engine remains backlog.
- No LLM narration of the retirement scenario. The purchase advisor and reports route explanations through the guardrailed LLM/deterministic-stub adapter; the retirement endpoint uses a deterministic explanation only (its answer is fully derived from the calculation). Extending the `ExplanationAdapter` with `explain_retirement` is a tracked follow-up, not M14.
- No amortization schedule, tax treatment, inflation adjustment, Social Security, or drawdown/sequence-of-returns modeling in the retirement projection. It is a single deterministic growth curve with documented assumptions — honest guidance, not financial-planning-grade modeling (matching the PRD's "educational guidance" framing and ADR 0003).
- No automatic extra-payment optimization or avalanche/snowball strategy selection in the debt impact; M14 reports each debt's payoff outlook, it does not prescribe a paydown order (reasonable follow-up).
- No Angular UI for the retirement scenario or account debt-terms entry beyond what the existing M11 account form trivially extends; a dedicated planning page is future UI work.

### Debt Payoff Wiring Behavior

- A liability account contributes to the modeled debt impact only when **both** `annual_interest_rate` and `minimum_payment_minor` are set; the balance owed is the absolute value of its (negative) latest balance.
- Each qualifying debt is run through `calculate_debt_payoff`; the advisor aggregates: total interest remaining across debts, and the longest single payoff horizon. A debt whose payment doesn't cover interest surfaces that calculation's warning rather than a number.
- Liability accounts without terms produce a non-blocking note ("N debts have no interest/payment terms and were not modeled"), preserving the old honesty without pretending.

### Retirement Projection Behavior

- Inputs: `current_age`, `retirement_age` (> current_age), `current_savings` (Money), `monthly_contribution` (Money), `annual_return_rate` (float ≥ 0), optional `annual_expenses` (Money).
- Simulation: month-by-month for `(retirement_age - current_age) * 12` months; each month the balance grows by `balance * annual_return_rate / 12` (Decimal, rounded to minor units) and the contribution is added. Integer minor units throughout, never float money.
- Outputs: `projected_balance` (Money), `months_to_retirement`, and — if `annual_expenses` is given and positive — `years_of_expenses_covered` (projected_balance / annual_expenses, a dimensionless ratio) with a warning if it is below a documented threshold (e.g. < 20 years).
- Assumptions are recorded on the `CalculationResult` (constant return, constant contribution, no inflation/tax/drawdown).

### API Behavior

- `POST /api/v1/advisor/retirement` requires `bearerAuth`, any household role (like the purchase advisor — asking a projection question doesn't mutate financial state). Validates `retirement_age > current_age` and non-negative amounts/rate (`400` otherwise). Returns `Recommendation`.
- Account create/update accept the two optional debt-term fields; `PATCH` can set or clear them. Currency of `minimum_payment` follows the account currency.

### Data Model Changes

- `accounts.annual_interest_rate` (nullable float) and `accounts.minimum_payment_minor` (nullable signed integer, minor units, paired with the account's existing `currency`). Additive migration; no rewrite of the M2 accounts table.
- No new tables. Retirement scenarios reuse the existing `scenarios`/`recommendations` tables (like the purchase advisor).

### Security Impact

- Debt terms are `Sensitive` (financial detail) and covered by the same household-scoped auth and audit as other account writes; account write paths already audit (M9), and setting terms is part of `account.updated`/`account.created`.
- The retirement endpoint reads only caller-supplied inputs plus (optionally) household context; it persists a recommendation the same way the purchase advisor does and logs only ids.

### Test Expectations

- Engine: unit tests for `calculate_retirement_projection` — growth to a target balance with known inputs, the zero-return case, the expenses-coverage ratio and its low-coverage warning, and input validation.
- Repository/API: account create/update round-trips the debt-term fields; `Account` exposes them.
- Advisor: a household with a liability account carrying terms produces a modeled `debt` impact (months/interest, not the placeholder); a household with a liability account missing terms produces the "not modeled" note.
- Retirement endpoint: a valid scenario returns a grounded `Recommendation` and persists it; `retirement_age <= current_age` is `400`; role/no-auth paths behave like the purchase advisor's.
- OpenAPI contract check passes with the new endpoint and account fields.

### Documentation Impact

- Update `services/financial-engine/README.md` (retirement projection) and `apps/api/README.md` (M14 scope: debt-term fields, real debt impact, retirement endpoint).
- Update `database/README.md` (account debt-term columns) and `shared/openapi/family-cfo.v1.yaml` (retirement path + schemas, `Account`/account-request debt-term fields).
- Mark the "Backlog: Debt Payoff and Retirement Projections" items done except the general scenario-planning API (which stays backlog), and update `docs/specs/README.md` Acceptance State.

## M15: Annual Report

- Add `annual` as a third report type
- Prior-calendar-year period, 12× monthly normalization
- Scheduled annual generation

> Context: closes the "Backlog: Annual Report" item. The PRD lists "weekly, monthly, and annual reports"; M8 shipped weekly/monthly and scoped annual out. M15 is a thin extension of M8's proven report machinery — the same wins/risks/unusual-spending/goal-progress/recommended-actions shape and the same guardrail-validated narrative — over a yearly period.

### Scope

- Add `annual` to the report types (`reports.report_type` CHECK, the `REPORT_TYPES` tuple, and the `ReportType` API literal), via an additive migration that rebuilds the `ck_reports_type` constraint.
- `compute_report_period("annual", ref)` covers the **prior calendar year** (Jan 1 of last year through Dec 31 of last year), mirroring how `monthly` covers the prior calendar month — never the in-progress year.
- Scale the financial engine's monthly income/bills figures **up by 12** for the annual budget summary (a year is 12 months), the counterpart to `weekly`'s 7/30 down-scale, recorded as a report assumption.
- Add a scheduled annual generation job to `family-cfo-worker` using the same idempotent, poll-and-skip pattern as the weekly/monthly jobs (`run_scheduled_reports_once("annual", ...)`), so a missed poll self-heals.
- `POST /api/v1/reports/generate` accepts `annual`; `GET /api/v1/reports*` return annual reports unchanged (no shape change).

### Non-Goals

- No new report content, sections, or schema fields — annual reuses the M8 `ReportSummary` exactly. Year-specific analytics (e.g. tax-year rollups, YoY trend charts) are not in scope.
- No Angular change beyond the reports page trivially listing whatever the API returns (it already renders any `report_type`); a dedicated annual view is not built.
- No back-generation of historical annual reports for prior years; the scheduler and endpoint generate the most recent completed year on request.

### Data Model Changes

- Rebuild `ck_reports_type` to `('weekly','monthly','annual')` (migration; reversible). No new columns or tables.

### Test Expectations

- `compute_report_period("annual", ...)` returns the prior calendar year; the 12× up-scaling is exercised; a generated annual report persists with `report_type = "annual"` and the expected summary shape.
- API: `POST /api/v1/reports/generate` with `annual` returns `201` and lists; role gating unchanged (`owner`/`adult` to generate).
- Scheduler: `run_scheduled_reports_once("annual")` generates once and is idempotent on the second run.
- Migration up/down/up cycle; OpenAPI contract check passes.

### Documentation Impact

- Update `apps/api/README.md` (annual report), `shared/openapi/family-cfo.v1.yaml` (`ReportType` enum), `docs/specs/README.md` Acceptance State, and mark the "Backlog: Annual Report" items done.

## M16: Agentic Tool-Calling (Conversational Advisor)

- Expose the deterministic engine calculations as callable tools
- A tool-calling loop so the local model can answer open-ended questions
- Guardrails move to validating tool arguments; answers stay grounded

> Context: implements [ADR 0009](../adr/0009-agentic-tool-calling.md). Open-ended questions ("if I buy this $1,000 phone, how many years of retirement does it cost me?") cannot be a per-question API. The model orchestrates deterministic tools and narrates grounded results; it never computes numbers or supplies facts itself. Extends "the LLM explains, the engine calculates" (ADR 0003) to a multi-step flow.

### Scope

- Add a **tool-descriptor layer** exposing the financial-engine calculations as tools (JSON-schema described): net worth, cash flow, budget summary, emergency fund, goal progress, purchase impact, debt payoff, retirement projection, plus a new `future_value` / opportunity-cost primitive in the engine. Each tool is a thin wrapper: validate arguments, run the existing deterministic calculation, return a structured result. No calculation logic changes.
- Extend `VLLMAdapter` (and the `RuntimeAdapter` seam) with **tool/function-calling**: pass the tool schemas, parse the model's `tool_calls`, and support a bounded multi-turn loop (model → tool calls → execute → results → model → final answer).
- Add a **tool-calling orchestration loop** in `services/ai-orchestrator` that drives that exchange with a hard iteration cap, executes requested tools through the descriptor layer, feeds results back, and returns the final narration plus the trace of tools/args used.
- Upgrade `POST /api/v1/chat/messages` to route through the tool-calling loop **when the household has an enabled tool-calling-capable runtime**, persisting the turn via M10 conversations. With no model (the default), it keeps the existing bounded deterministic snapshot — so behaviour is unchanged for deployments without vLLM.
- Every figure in the final answer must trace to a tool output; the response cites the `financial_calculations` rows the tools persisted, same grounding contract as the purchase advisor.

### Non-Goals

- **No tool may mutate state or move money.** M16 tools are read/compute only (query + deterministic calculation). Write actions (create a transaction, change a goal) via tools are explicitly out of scope — a safety boundary, revisited only under a future ADR.
- **No external/cloud model** (ADR 0008) — tool-calling runs against the same opt-in local vLLM.
- **No document/vector retrieval.** Tools query structured data (Postgres/engine) only; semantic search over documents/conversation is the separate vector-store backlog. The model is not given raw document text to interpret.
- **No real vLLM in tests/CI.** The loop is tested against a stubbed runtime that emits scripted `tool_calls` (the same "mock the runtime, not the vendor" approach M4 used). Real-model tool-selection reliability is a deployment/verification concern, not something a unit test pins down.
- **Not an unbounded tool set.** A fixed, curated set of calculation tools ships; new tools are added deliberately, not generated.
- No change to the structured `/advisor/*` and `/reports/generate` endpoints — they remain fast deterministic paths and the fallback.

### Tool Library (initial)

- Read tools (household-scoped, no args beyond context): `get_net_worth`, `get_cash_flow`, `get_budget_summary`, `get_emergency_fund`, `list_goals_progress`, `get_debt_outlook`.
- Compute tools (caller/model-supplied args, validated): `project_purchase_impact`, `project_retirement`, `future_value` (opportunity cost of an amount grown at a rate for N years), `debt_payoff` (for supplied terms).
- Each returns a structured result and persists a `financial_calculations` row so the answer stays auditable.

### Tool-Calling Loop and Trust Boundary

- The loop has a hard maximum iteration count; exceeding it falls back to the deterministic snapshot with a "could not complete" note rather than looping or fabricating.
- **The trust boundary is tool arguments, not output text.** Before executing a tool the system validates argument types, numeric ranges, currency (must match the household/account), and that any referenced entity id belongs to the caller's household. Invalid arguments are rejected and returned to the model to correct, never executed blindly.
- Tool **outputs are authoritative**; the final narration is still checked so every figure it states traces to a tool output (the M4 guardrail principle, re-homed).
- **Facts the model cannot compute are never guessed.** If a required input is missing (e.g. retirement cost of living), the tool signals "missing input" and the model asks the user rather than inventing a value; the response surfaces the question instead of a fabricated number.

### API Behavior

- `POST /api/v1/chat/messages` (unchanged auth: any household role) gains tool-calling when an enabled capable runtime exists; response shape (`ChatResponse` with a grounded `Recommendation`) is unchanged, so the contract and Angular client are unaffected. Conversation persistence (M10) is reused.
- No new endpoint and no request-shape change are expected. If the trace of tool calls is surfaced, it is additive/optional on the response and lands in the OpenAPI contract first.

### Data Model Changes

- None expected. Reuses `conversations`/`conversation_messages` (M10), `recommendations`, `scenarios`, and `financial_calculations`. If a persisted tool-call trace proves worth storing, it is an additive migration scoped at implementation time, not assumed here.

### Security Impact

- Tools are read/compute only and strictly household-scoped; argument validation prevents a model from reaching another household's data via a forged id.
- No household data leaves the box (local model, ADR 0008); raw prompts/tool exchanges are not persisted beyond the guardrail-validated answer, consistent with M4.
- The no-mutation boundary means a misbehaving model cannot change financial records through this path.

### Test Expectations

- Engine: unit tests for the new `future_value`/opportunity-cost primitive (known-input growth, zero-rate, validation).
- Tool descriptors: each tool validates arguments (type/range/currency/household ownership) and returns the expected structured result; a forged cross-household id is rejected.
- Tool-calling loop (stubbed runtime): a scripted multi-step exchange (e.g. the phone-vs-retirement question → `future_value` then a retirement ratio) executes the right tools with validated args and produces an answer whose numbers all trace to tool outputs; the iteration cap and the missing-fact "ask back" path are covered; the no-model fallback returns the deterministic snapshot.
- API: `POST /chat/messages` with a tool-calling runtime configured persists the conversation and returns a grounded recommendation; role/no-auth paths unchanged.
- OpenAPI contract check passes.

### Documentation Impact

- Update `services/ai-orchestrator/README.md` (tool-calling loop, tool descriptors), `apps/api/README.md` (chat gains tool-calling when a model is enabled), and `services/financial-engine/README.md` (future-value primitive).
- Reference ADR 0009; update `docs/specs/README.md` Acceptance State.
- Update `shared/openapi/family-cfo.v1.yaml` only if the response gains an optional tool-call trace.

## M17: Turnkey Deployment (AI on by default)

- Ship a single-command deploy of the full stack (dashboard + API + worker + DB + vLLM) to a local or remote host.
- Flip the **local** AI runtime from opt-in to **on by default** so a fresh instance answers open-ended questions without manual configuration.

> Context: M16 delivered the agentic advisor but it stayed dormant unless an operator hand-enabled a runtime and the `vllm` service (behind a Compose profile). This milestone makes "stand up an instance and use the AI dashboard" a one-step operation, on the assumption that the target is a GPU-capable host. It refines M12 (Docker) and the AI-runtime posture of ADR 0004; it does **not** change the privacy stance of ADR 0008 — see Non-Goals.

### Scope

- **Local AI on by default.** Add deployment-level settings (`FAMILY_CFO_AI_ENABLED`, `FAMILY_CFO_AI_PROVIDER`, `FAMILY_CFO_AI_BASE_URL`, `FAMILY_CFO_AI_MODEL`) that supply the default AI runtime a household inherits until it saves its own `ai_runtime_configs` row. The code default stays **off** (so tests and non-Docker runs never reach for an absent runtime); the Docker stack sets these to **on**. A household's own saved config always overrides the default.
- **vLLM runs by default.** Remove the `ai` Compose profile so `docker compose up -d` starts the runtime; wire the api/worker env to it. Keep an operator escape hatch (`FAMILY_CFO_AI_ENABLED=false` + `--scale vllm=0`) for GPU-less hosts.
- **`scripts/deploy.sh`** — an interactive, one-command deploy. For `local` it runs Compose in place; for `remote` it prompts for SSH host/user/port/key, verifies Docker (and, for AI, the NVIDIA Container Toolkit) on the target, rsyncs the repo, generates a `.env` with random secrets on first deploy (never clobbering an existing remote `.env`), runs `docker compose up -d --build`, and prints the dashboard URL.

### Non-Goals

- **No change to the external/cloud-AI stance.** ADR 0008 keeps *external* runtimes opt-in; "on by default" applies only to the **local, on-box** vLLM, where no household data leaves the host. Enabling a local model by default is consistent with the privacy-first principle, not a reversal of it.
- **No orchestration platform.** `scripts/deploy.sh` is Compose over SSH for a single home-server/box, not Kubernetes/Swarm/multi-node. No zero-downtime, load-balancing, or secret-manager integration.
- **No password-based SSH automation.** The script relies on the operator's own SSH key/agent (or ssh's interactive prompt); it does not embed credentials or use `sshpass`.
- **No provisioning.** It assumes Docker Engine + Compose v2 (and the NVIDIA Container Toolkit for AI) are already installed on the target.

### Data Model Changes

- None. The default runtime is resolved from settings at request time; no schema or migration.

### Security Impact

- Generated `.env` secrets (`POSTGRES_PASSWORD`, `FAMILY_CFO_BACKUP_ENCRYPTION_KEY`) use a CSPRNG; the remote `.env` is excluded from rsync so a re-deploy never overwrites or exfiltrates existing secrets.
- On-by-default AI still sends household context only to the on-box vLLM; the trust boundary and grounding guardrail from M16 are unchanged.

### Test Expectations

- Runtime selection: with an enabled settings default and no household row, `resolve_ai_config`/`select_tool_runtime` yield a usable runtime; a saved household row (e.g. disabled) overrides it; the code default (disabled) yields the deterministic fallback; enabled-but-no-model is treated as unusable.
- The default-disabled code posture keeps the existing chat/advisor/ai-runtime tests unchanged.
- `scripts/deploy.sh` passes `bash -n` (and shellcheck where available); a live deploy is a manual/operator verification, not a CI test.

### Documentation Impact

- Update `docs/specs/10-docker-spec.md` (AI on by default; vLLM not profiled), `.env.example`, `docker/README.md`, and the deployment + AI-advisor guides. Reference this milestone from `docs/specs/README.md` Acceptance State.

## M18: Security Hardening Pass & Deployment Tooling

- Close the findings from a manual security review (SSRF, auth brute-force, upload DoS, pairing-secret entropy, prod docs exposure).
- Make a turnkey deploy observable and testable: a health "doctor" and a real build+boot e2e; publish system requirements.

> Context: implements [ADR 0010](../adr/0010-security-hardening-and-deploy-tooling.md). Threat model unchanged (single-tenant, self-hosted, ADR 0006/0008) — this raises the floor for the exposed-anyway case without adding operational burden.

### Scope

- **SSRF fix:** allowlist the AI runtime `base_url` (`FAMILY_CFO_AI_ALLOWED_BASE_URLS`, default = the deployment `FAMILY_CFO_AI_BASE_URL`); `PUT /ai/runtime` rejects other hosts/schemes.
- **Auth throttling:** per-IP + per-account fixed-window limiter with temporary lockout on `POST /auth/sessions` (in-memory, single-instance; configurable, on by default).
- **Upload cap:** enforce a max upload size in the imports/documents handlers and set `client_max_body_size` in nginx.
- **Pairing secret:** generate the pairing session id with a CSPRNG token, not `uuid4`.
- **Prod docs:** disable Swagger UI and `openapi.json` when `FAMILY_CFO_ENV=production`.
- **`scripts/doctor.sh`:** read-only health report (Docker, containers, API `/health`, web, DB, vLLM `/v1/models`, disk, GPU) with clear pass/fail.
- **`scripts/e2e-deploy-test.sh`:** real image build + core-stack boot (no vLLM) + login + chat smoke test + teardown.
- **System requirements:** per-model RAM/VRAM and storage (minimum vs recommended) tables in the root README + deployment guide; deploy-script preflight.

### Non-Goals

- No distributed/shared-store rate limiting (single `api` instance; revisit if multi-instance is ever a goal).
- No IP-range SSRF denylisting (allowlist chosen instead — see ADR 0010).
- The e2e test does **not** boot vLLM (multi-GB model + GPU); the AI path stays covered by M16 stubbed-runtime tests and by `doctor.sh` at runtime.

### Data Model Changes

- None. All new controls are settings/middleware; no schema or migration.

### Security Impact

- Removes the owner-triggered SSRF/exfiltration vector; blunts online password guessing; caps upload memory use; strengthens the pairing bearer secret; stops schema disclosure in production. Documented residual: in-memory limiter resets on restart / single-instance only.

### Test Expectations

- SSRF: `PUT /ai/runtime` accepts an allowlisted base_url and 4xx-rejects a non-allowlisted one.
- Rate limit: repeated bad logins get locked out; a good login within limits still succeeds; lockout is per-account/IP.
- Upload cap: an over-limit upload is rejected (413/400); an at-limit upload succeeds.
- Pairing: the generated session id is high-entropy; confirm/expiry/single-use paths unchanged.
- Docs gating: `/api/v1/docs` returns 404 under `FAMILY_CFO_ENV=production`, served otherwise.
- Scripts: `bash -n` (and shellcheck where available) for `doctor.sh`/`e2e-deploy-test.sh`; the e2e run is executed for real against the core stack as part of verification.

### Documentation Impact

- New ADR 0010; update root `README.md` (system requirements + deploy how-to), deployment + AI-advisor guides, `.env.example`, `docker/README.md`, `web-nginx.conf`; acceptance state.

## M19: Dashboard AI Chat & Self Sign-up

- Surface the existing agentic advisor in the dashboard as a conversational **AI Chat** page ("can I afford this?").
- Add a self-service **sign-up / onboarding** page so a new owner can create their household from the login screen.

> Context: the backend has had the chat/advisor (`POST /chat/messages`, M16) and the first-run household bootstrap (`POST /households`, M9) for a while, and the generated web client already exposes them — but no Angular page called them. This closes that UI gap. Web-only; no API or contract changes.

### Scope

- **AI Chat page** (`/chat`, authed): send a message via `createChatMessage`, render the returned `Recommendation` (answer + assumptions, impacts, tradeoffs, alternatives, confidence, calculation_refs, warnings); keep the thread going with the returned `conversation_id`; a conversation-history list (`listConversations` / `getConversation`, M10) with "New conversation". A nav link is added to the shell. Open-ended affordability questions ("can I afford this $1,000 phone?") go through this page; when no model is loaded the API's deterministic snapshot answers (unchanged behaviour).
- **AI status banner** (chat page): a new additive `GET /ai/runtime/status` endpoint (operationId `getAiRuntimeStatus`) probes the household's runtime and returns `{enabled, provider, model, ready, served_model, detail}`; the banner shows whether the model is loaded/active and which model is serving. Contract + schema updated and the client regenerated.
- **Confidence indicator** (chat page): each assistant answer surfaces the `Recommendation.confidence` (already returned by the API) as a High/Medium/Low + percentage chip.
- **Sign-up page** (`/signup`, public): a form (household display name, base currency, owner name/email/password) that calls `createHousehold`, stores the returned session like login, and enters the app; a link to/from the login page. (Also fixes the shared contract, which was missing the `POST /households` request body.)

### Non-Goals

- No new/changed API endpoints, OpenAPI contract, or database — pure frontend against existing generated client methods.
- No streaming/token-by-token chat UI (request/response per message is enough; the local model isn't streamed today).
- No public multi-tenant registration policy — `POST /households` remains a self-hosted first-run bootstrap; exposing it in the UI doesn't change that a self-hosted instance is single-family.
- iOS/SwiftUI unchanged (built from macOS; out of scope here).

### Test Expectations

- Sign-up: invalid form doesn't submit; a successful `createHousehold` stores auth and navigates; a failure surfaces the error.
- Chat: sending calls `createChatMessage` with the message (and the current `conversation_id` on follow-ups), appends the user turn and the assistant answer, and renders recommendation details; an API error is surfaced; "New conversation" clears the thread.
- Vitest component tests mock `ApiService`/`AuthService` per the existing pattern; `npm run lint` and `npm test` pass.

### Documentation Impact

- Update `apps/web/README.md` (new pages) and the login-page copy (which currently says "there is no public sign-up"). Reference from `docs/specs/README.md` Acceptance State.

## M20: Dashboard Redesign & Mobile Support

- Restyle the Angular dashboard to a modern, professional visual standard using a shared design-token system.
- Make every page usable on phone-sized screens (target: iPhone 15 Pro, 393×852 CSS px), including notch/safe-area handling.

> Context: the M5/M11/M19 dashboard is functional but visually utilitarian — hard-coded hex values per page, a fixed 220px sidebar with no responsive behaviour, tables that overflow small screens, and a default "Web" page title. Web-only; no API or contract changes.

### Scope

- **Design tokens**: CSS custom properties in `styles.scss` (color palette, surface/border/shadow, radius, spacing, typography scale) consumed by the shell and pages; a refined system font stack. Global element baselines (buttons, inputs/selects, tables, headings) so all existing pages inherit the new look without per-page rewrites.
- **Responsive shell**: desktop keeps a refined dark sidebar (accent active state); below a breakpoint (~820px) the sidebar becomes a **slide-in drawer** behind a fixed top app bar with a hamburger button and scrim; the drawer closes on navigation. Safe-area insets (`viewport-fit=cover`, `env(safe-area-inset-*)`) for notched iPhones.
- **Responsive pages**: data tables become horizontally scrollable on narrow screens via a global rule; chat's history sidebar collapses above the thread on mobile; login/sign-up cards fit small widths; content padding scales down.
- **Head metadata**: proper title ("Family CFO"), `theme-color`, `viewport-fit=cover`.

### Non-Goals

- No component-library adoption (Material/PrimeNG) — a token-based restyle of the existing hand-rolled components keeps the bundle small and the diff reviewable.
- No information-architecture changes: same pages, routes, and functionality; this is visual + responsive only.
- No dark mode (tokens make it cheap later; not in this pass).
- Not the native iOS app (`apps/ios`) — this is the responsive web dashboard on a phone browser.

### Test Expectations

- Shell: a component test covers the mobile menu toggle (open/close, closes on nav selection).
- All existing Vitest component tests stay green (restyle must not break behaviour); production build type-checks.
- Manual verification on the live deployment at 393px width (devtools emulation): no horizontal page overflow; nav, chat, forms, and tables usable.

### Documentation Impact

- `apps/web/README.md` (design tokens + responsive behaviour note); acceptance state.

## M21: Chat Photo Attachments (Vision Routing)

- Let a user attach a photo (or take one with the camera) in chat and ask about it ("can I afford this?").
- Route the image to the main model if it is vision-capable, else to a small on-box vision describer model; ground the description's numbers.

> Context: implements [ADR 0011](../adr/0011-vision-image-routing.md) — describe-then-ground. The image is always converted to a text description first (main model if `FAMILY_CFO_AI_SUPPORTS_VISION`, else the `vllm-vision` describer); the description joins the user message in the existing text tool-calling loop (M16) and its numbers join the grounded set. On-device iPhone describing is a native-app concern (Safari cannot reach Apple's on-device models) and is recorded in the mobile spec backlog.

### Scope

- **Contract/API**: `ChatRequest` gains optional `image_base64` + `image_media_type` (jpeg/png/webp); server enforces the upload cap and type allowlist. Response unchanged; a `warnings` entry reports when no vision model was available. `AiRuntimeStatus` gains optional `vision_ready`/`vision_model`.
- **Orchestrator**: `RuntimeMessage` supports an attached image (data URL); `VLLMAdapter` builds OpenAI multimodal content parts; a `describe_image` helper runs a single no-tools completion against whichever runtime is doing the describing.
- **API flow**: decode/validate the image → pick describer per ADR 0011 → get description → append `[Attached photo: …]` to the user message → existing tool loop; add `extract_numbers(description)` to the grounded set. The image is processed in memory only — never persisted; the stored conversation turn contains the description text.
- **Compose**: new `vllm-vision` service (default `Qwen/Qwen2.5-VL-7B-Instruct`), on by default like the main runtime; both services get explicit `--gpu-memory-utilization` fractions (`VLLM_GPU_FRACTION=0.60`, `VLLM_VISION_GPU_FRACTION=0.20`). Opt-out: `FAMILY_CFO_AI_VISION_ENABLED=false` + `--scale vllm-vision=0`.
- **Web chat**: an attach-photo button (`<input type="file" accept="image/*" capture="environment">` — on iPhone this offers Camera or Photo Library), client-side downscale + JPEG re-encode (canvas, max ~1280px) so HEIC and huge photos become small JPEGs, a removable thumbnail preview, and an "included a photo" marker on sent turns.

### Non-Goals

- No multimodal tool-calling (ADR 0011 rationale); no image persistence or gallery; no OCR-pipeline integration (imports/documents remain the place for statements needing structured extraction); no native-iOS on-device describing (mobile-app backlog); no multi-image messages.

### Test Expectations

- Orchestrator: multimodal payload building; `describe_image` happy/error paths (stubbed transport).
- API: image chat with a stubbed describer (description reaches the prompt; its numbers are grounded); vision-capable-main path; no-vision warning path; oversized/wrong-type image rejected (413/422); image absent unchanged.
- Web: attach → preview → send includes the encoded image; remove clears it; component tests with mocked ApiService.
- Contract test green; existing suites green.

### Documentation Impact

- ADR 0011; README system-requirements row for the vision model; `.env.example`, docker README, AI-advisor guide (vision section), mobile-spec backlog note; acceptance state.

## M22: Model Selection, Hardware Planning & Status Clarity

- Pick the main model (and a vision model when the main isn't vision-capable) from a curated list in the AI Runtime page, with live hardware-fit feedback.
- Split the chat status banner so main-model and vision-model states are separately visible; fix the camera-button alignment.

> Context: implements [ADR 0012](../adr/0012-model-selection-and-hardware-planning.md). The API never controls Docker — the UI saves the desired selection and generates the exact `scripts/swap-model.sh` command; selections replace the current models (never additive).

### Scope

- **Catalog**: `GET /ai/models` (curated, backend-owned: id/label/params/est. memory GB/est. disk GB/parser/`supports_vision`/role/gated).
- **Hardware**: `GET /ai/hardware` (system memory from `/proc/meminfo`, disk free, GPU memory from `FAMILY_CFO_GPU_MEMORY_GB` when provided else null + unified-memory note). Deploy script writes the GPU value when detectable.
- **Status**: `AiRuntimeStatus.vision_enabled` (additive) so the chat banner can distinguish vision "loading" from "off"; banner shows separate main + vision states; camera button alignment fixed.
- **AI Runtime page rebuild**: main-model picker; vision picker shown only when the selected main lacks vision (hidden with a "main model handles photos" note otherwise); live required-memory/disk vs available with fit verdict (fits / tight / won't fit, ~15% headroom); save via existing `PUT /ai/runtime`; "selected but not serving" mismatch notice + copyable `swap-model.sh` command.
- **`scripts/swap-model.sh <main> [vision|none]`**: updates `.env` (`VLLM_MODEL`, `VLLM_TOOL_PARSER`, `VLLM_VISION_MODEL`, `FAMILY_CFO_AI_SUPPORTS_VISION`, `FAMILY_CFO_AI_VISION_ENABLED`) and recreates vllm/vllm-vision/api/worker.

### Non-Goals

- No Docker socket in the API / no one-click restart (ADR 0012); no free-form model ids in the picker (curated list; `.env` remains the escape hatch); no quantized-variant matrix (bf16 estimates only); no cache pruning.

### Test Expectations

- API: catalog shape; hardware profile (env-provided GPU value vs null); status `vision_enabled`; contract green.
- Web: picker logic (vision section hidden for vision-capable main; requirement totals recompute on selection; fit verdict against mocked hardware; save calls PUT; mismatch notice), banner states. Existing suites green; build clean.
- `bash -n scripts/swap-model.sh`.

### Documentation Impact

- ADR 0012; AI-advisor guide (swap-model section replaces manual `.env` editing); README pointer; acceptance state.
