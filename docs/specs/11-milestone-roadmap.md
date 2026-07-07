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

## M5: Angular Dashboard

- Onboarding
- Reports shell
- Transaction review
- Import review
- AI runtime settings

## M6: iPhone App

- Pairing
- Chat
- Camera capture
- Structured image output
- Face ID local unlock

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
