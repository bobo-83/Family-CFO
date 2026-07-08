# API

FastAPI backend for Family CFO.

Responsibilities:

- Source-of-truth backend API
- Authentication and pairing
- Financial context persistence
- Chat and recommendation endpoints
- Report orchestration
- Import workflows
- OpenAPI generation

SwiftUI and Angular clients should generate from the same OpenAPI contract.

## M1 Scope

Implemented:

- FastAPI application entry point
- `GET /api/v1/health`
- Structured error response foundation
- Environment-based configuration
- Logging redaction hooks
- PostgreSQL connection helper
- Alembic migration tooling
- Pytest harness
- OpenAPI generation and implemented-route contract check

## M2 Scope

Implemented:

- Local authentication: `POST /api/v1/auth/sessions` (email/password login backed by `auth_sessions`, opaque bearer tokens hashed at rest).
- Household context: `GET /api/v1/household`, computed by running the financial engine's net worth and emergency fund calculations against the household's accounts, balances, and bills.
- `GET /api/v1/accounts`, `GET /api/v1/transactions`, `GET /api/v1/bills`, `GET /api/v1/income` — household-scoped read APIs.
- `GET /api/v1/goals` and `POST /api/v1/goals` — goal creation is limited to the `owner` and `adult` roles (`403` otherwise).
- A repository layer (`family_cfo_api/repository.py`) over the M2 tables, and a `finance_service.py` that composes repository data with `family_cfo_financial_engine` calculations and persists an audit row to `financial_calculations` for each computed result.
- Synthetic demo household fixtures (`family_cfo_api/fixtures.py`) for local development and tests.

Not implemented in M2:

- Account, transaction, bill, income, and scenario write APIs (create/update/delete) beyond goal creation.
- User registration/invitation or household-membership management APIs.
- Scenario calculation logic (only the `scenarios` table shape is persisted, for M3 to build on).
- Mobile pairing, chat, purchase advisor, imports, reports, and AI runtime behavior.

## M3 Scope

Implemented:

- `POST /api/v1/advisor/purchase`: given `item` and `price` (plus optional `merchant`, `description`, `source`, `confidence`, `user_question`), persists the request as a `scenarios` row, runs `calculate_purchase_impact` (see `services/financial-engine/README.md`) against the household's current financial context, and returns a `Recommendation` — `answer`, `assumptions`, `impacts`, `tradeoffs`, `alternatives`, `confidence`, `calculation_refs`, `warnings` — persisted as a `recommendations` row linked to that scenario.
- `calculation_refs` always cites the `financial_calculations` row backing the numbers in `answer`; nothing in the response is a fabricated numeric claim.
- `family_cfo_api/explanation.py`: an `ExplanationAdapter` interface plus `DeterministicExplanationAdapter`, a no-model implementation that renders calculation outputs as plain sentences. M4 will add a vLLM-backed adapter behind the same interface without changing the advisor route (ADR 0007).
- Available to every household role (owner, adult, viewer, child) — asking "can I afford this" doesn't require the write-capable roles goal creation does.
- Purchase item, merchant, and price are never written to logs (see `apps/api/src/family_cfo_api/api/advisor.py`); only household id, calculation id, and recommendation id are logged.

Not implemented in M3:

- Any real LLM call — that is M4's `ExplanationAdapter` implementation.
- Debt payoff impact calculation (no interest/payment data in the M2 schema; the response includes a warning instead).
- Multi-item or recurring-purchase scenarios, or any scenario/recommendation history, editing, or deletion API.
- Chat integration.

## M4 Scope

Implemented:

- `GET /api/v1/ai/runtime` and `PUT /api/v1/ai/runtime`, backed by a household-scoped `ai_runtime_configs` row. `GET` is available to every household role; `PUT` is limited to `owner` (`403` otherwise). A household with no config returns a disabled default — no household starts sending financial context to any runtime without an explicit opt-in.
- `family_cfo_api/llm_explanation.py`: `LlmExplanationAdapter`, an `ExplanationAdapter` implementation that builds a prompt from purchase-impact facts via `family_cfo_ai_orchestrator`, calls the configured `RuntimeAdapter`, and validates the response against the guardrails. On adapter error (timeout, non-2xx, malformed response) or a guardrail violation, it falls back to M3's `DeterministicExplanationAdapter` — the advisor route never surfaces an unvalidated LLM response and never hard-fails when the runtime is unreachable.
- `POST /api/v1/advisor/purchase` now selects between `LlmExplanationAdapter` and `DeterministicExplanationAdapter` per request, based on whether the household has an `ai_runtime_configs` row with `enabled = true` and a supported `provider` (`vllm` only, for now). Self-hosted deployments with no runtime configured see no behavior change from M3.
- `recommendations.model_version` and `recommendations.prompt_version` are populated when the LLM path is used, and left `null` for the deterministic stub.
- No raw prompt or raw model response is logged; the advisor route logs only household id, calculation id, recommendation id, and which `explanation_source` was used.

Not implemented in M4:

- Any real vLLM deployment or Docker Compose service — Release Readiness/M8 work. Tests mock the HTTP layer; there is no vLLM server anywhere in this repo or its CI.
- Ollama or llama.cpp adapters (the `RuntimeAdapter` interface supports them; only `VLLMAdapter` ships).
- Chat endpoint or conversation history.
- API-key/secret storage for cloud-hosted OpenAI-compatible endpoints.

## M6 Backend Support

Implemented:

- `POST /api/v1/pairing/sessions`: authenticated `owner`/`adult` users can create a 10-minute pairing session. The returned QR payload contains non-secret server and household display metadata plus the pairing session id; it never contains a bearer token.
- `POST /api/v1/pairing/confirm`: a mobile client can exchange a valid, unexpired, single-use pairing session id plus device name/public key for a device-backed bearer token. Raw tokens are returned once and only token hashes are stored.
- `GET /api/v1/pairing/devices`: authenticated household users can list paired devices without public keys or token hashes.
- `DELETE /api/v1/pairing/devices/{device_id}`: `owner` users can revoke a paired device. Revocation also revokes active auth sessions issued for that device.
- `POST /api/v1/chat/messages`: authenticated users can request a bounded deterministic household snapshot. M6 chat computes net worth and emergency fund coverage, persists a recommendation row with calculation references, and does not persist raw chat messages.
- The shared OpenAPI contract and generated Angular client now include paired-device listing/revocation, auth requirements for pairing session creation, and chat `401` errors.

Not implemented in this Linux-backed M6 slice:

- SwiftUI app code, Swift client generation, QR scanning, Keychain storage, Face ID, camera capture UI, Vision extraction, or iOS tests. Per `AGENTS.md`, those require macOS with Swift/Xcode.
- General-purpose conversational memory or raw chat history persistence.
- Raw photo/document upload endpoints — these exist now, added in M7 below.

### Auth Flow

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/sessions \
  -H 'content-type: application/json' \
  -d '{"email": "demo@family-cfo.local", "password": "demo-password-123"}'
```

Use the returned `access_token` as a bearer token against protected routes:

```bash
curl -s http://127.0.0.1:8000/api/v1/household \
  -H "authorization: Bearer <access_token>"
```

The demo credentials above only exist once you seed the synthetic fixtures (see Fixtures below); they are not created by any migration.

## M7 Scope

Implemented:

- `POST /api/v1/imports` registers an import (`source_type`, `filename`, optional `account_id`); `POST /api/v1/imports/{id}/file` (multipart) stages the uploaded file to `FAMILY_CFO_IMPORT_STAGING_DIR` and returns `202` — processing is asynchronous, done by the worker (`family-cfo-worker`, see below), not the request.
- CSV imports are parsed and written to `transactions` with `review_state = "pending"`; malformed rows are skipped and counted (`skipped_row_count`), not fatal. A row matching an existing transaction on `(account_id, occurred_at, amount_minor)` is still inserted, flagged `possible_duplicate = true` for a human to resolve — never silently dropped.
- `POST /api/v1/imports/{id}/apply` (`owner`/`adult`) marks that import's pending transactions reviewed; `POST /api/v1/imports/{id}/discard` (`owner`/`adult`) deletes them. Both are scoped to the import via `transactions.import_id`, not "every pending transaction in the household."
- PDF imports extract raw text via `family_cfo_ocr_worker.PdfTextExtractionAdapter` (real, `pypdf`-based) into a linked `document_extractions` row for human review; they do **not** automatically create transactions — only CSV does.
- `POST /api/v1/documents` (multipart) is a synchronous single-document extraction endpoint (no worker hop) for receipts/PDFs uploaded outside the import flow. PDFs use the same real adapter; images use `family_cfo_ocr_worker.DeterministicOcrAdapter`, which has **no real OCR engine wired up** — every image upload returns a fixed `confidence = 0.0` "not available" result today. `GET /api/v1/documents` lists uploads with their extraction.
- `family-cfo-worker`: a separate long-running process (not started by the API server) that polls for pending imports every 30 seconds and processes them, with bounded retry (3 attempts, then `status = "failed"`) via `family_cfo_scheduler`.

Not implemented in M7:

- OFX/QFX parsing — planning documentation only (`docs/specs/11-milestone-roadmap.md`).
- A real OCR engine (Tesseract, Apple Vision, cloud OCR) — see `services/ocr-worker/README.md`.
- Any Angular "Import Review"/"Transaction Review" page upgrade; those remain M5's placeholder shells.
- Full CSV-processing transactional atomicity: a retried import can re-insert rows the failed attempt already committed, surfaced as `possible_duplicate` for review rather than silently duplicated. See the M7 Worker Scheduling Expectations in the roadmap doc for why this is a deliberate, bounded tradeoff.

### Imports Flow

```bash
IMPORT_ID=$(curl -s -X POST http://127.0.0.1:8000/api/v1/imports \
  -H "authorization: Bearer <access_token>" -H 'content-type: application/json' \
  -d '{"source_type": "csv", "filename": "statement.csv", "account_id": "<account_id>"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

curl -s -X POST "http://127.0.0.1:8000/api/v1/imports/$IMPORT_ID/file" \
  -H "authorization: Bearer <access_token>" -F "file=@statement.csv;type=text/csv"
```

Nothing happens to the file until the worker runs (`make worker` or `family-cfo-worker`). Once it has, `GET /api/v1/imports` shows `status: needs_review`, and the parsed rows appear as `pending` transactions.

## M8 Scope

Implemented:

- `POST /api/v1/reports/generate` (`owner`/`adult`) generates or regenerates a `weekly` or `monthly` report for the caller's household, reusing `calculate_cash_flow`/`calculate_budget_summary`/`calculate_goal_progress` (see `services/financial-engine/README.md`) against the report's period. Regenerating the same `(household_id, report_type, period_start)` updates the existing row instead of creating a duplicate. `GET /api/v1/reports` and `GET /api/v1/reports/{id}` are available to every household role.
- Report content is wins/risks/unusual-spending (rule-based heuristics over category spend, never an LLM guess at the numbers) plus goal progress and a narrative `explanation_text` produced by the same `LlmExplanationAdapter`/guardrail-fallback/`DeterministicExplanationAdapter` pattern M3/M4 already use for the purchase advisor (`family_cfo_api/report_generation.py`, `family_cfo_api/ai_runtime_selection.py` — the latter factors the adapter-selection logic the purchase advisor used to keep privately, now shared between both features).
- `POST /api/v1/backups`, `GET /api/v1/backups`, and `POST /api/v1/backups/{id}/restore` are `owner`-only (a whole-household administrative action, the same bar as `PUT /api/v1/ai/runtime`). A backup bundles an encrypted database dump plus a tar of the import/document staging tree into one archive (`family_cfo_api/backup_processing.py`, `services/backup`'s `family_cfo_backup` package — `BackupAdapter` protocol, `PgDumpBackupAdapter` real/`SqliteFileBackupAdapter` test-only, ADR 0007). Every backup is Fernet-encrypted; there is no unencrypted-backup code path, and a missing `FAMILY_CFO_BACKUP_ENCRYPTION_KEY` fails the backup job rather than writing plaintext.
- Restore fully replaces the current database and document tree from a backup archive — a destructive operation by definition, gated to `owner`, with no additional API-level confirmation step (that belongs in a future dashboard UI). Restoring also reverts `backup_jobs`' own bookkeeping to its state at dump time; see the M8 section of `docs/specs/11-milestone-roadmap.md` for why.
- `family-cfo-worker` now also polls hourly for weekly/monthly reports not yet generated for the current period (skipping households already covered, so a missed poll self-heals without re-calling the AI runtime) and runs a backup once a day, applying `FAMILY_CFO_BACKUP_RETENTION_COUNT` retention (oldest completed backups pruned first) after each successful run.

Not implemented in M8:

- An annual report — the roadmap named only weekly/monthly; tracked as backlog in `docs/specs/12-implementation-tasks.md`.
- Any Angular "Reports"/"Backups" page upgrade — those remain M5's placeholder shells (same backend-first split M6 and M7 used).
- A real PostgreSQL server in this sandboxed environment; `PgDumpBackupAdapter` is command/error-handling tested only (no `pg_dump`/`pg_restore` binary here). `SqliteFileBackupAdapter` exercises the identical encrypt/dump/retention/restore code paths against a real file.
- Backup-key recovery or rotation, and any automatic periodic restore-canary job in production — see the M8 Non-Goals in the roadmap doc.

### Reports and Backups Flow

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/reports/generate \
  -H "authorization: Bearer <access_token>" -H 'content-type: application/json' \
  -d '{"report_type": "weekly"}'

curl -s -X POST http://127.0.0.1:8000/api/v1/backups \
  -H "authorization: Bearer <access_token>"
```

`FAMILY_CFO_BACKUP_ENCRYPTION_KEY` must be a Fernet key (`python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`). Losing it makes existing backups permanently unrecoverable — there is no recovery mechanism.

## M9 Scope

Closes the M2-deferred write-API gap and the unbuilt `audit_events` table (surfaced by the post-M8 spec-kit audit). Backend + OpenAPI only; the dashboard UI for these is M11.

Implemented:

- `POST /api/v1/households` — unauthenticated self-hosted bootstrap that creates a household, its first `owner` user, and membership, and returns a working `AuthSession`. This resolves the "onboarding is login-only, there is no signup" limitation M5 documented. It is deliberately open for a trusted local network (ADR 0006); a first-run lockout option is tracked as backlog.
- Membership management (`owner` only): `GET`/`POST /api/v1/household/members`, `PATCH`/`DELETE /api/v1/household/members/{user_id}`. A household can never drop below one owner (last-owner demote/delete → `409`), and removing a member revokes their active auth sessions (mirroring M6 device revocation).
- Account writes (`owner`/`adult`): `POST`/`PATCH`/`DELETE /api/v1/accounts` and `POST /api/v1/accounts/{id}/balances` (balances are append-only history). Deleting an account referenced by a transaction, bill, or import returns `409`.
- Transaction writes (`owner`/`adult`): `POST`/`PATCH`/`DELETE /api/v1/transactions` for manual entry (defaults to `review_state = "reviewed"`, `import_source = null`). Amount currency must match the account.
- Bill and income writes (`owner`/`adult`): `POST`/`PATCH`/`DELETE /api/v1/bills` and `/api/v1/income`.
- `audit_events` table + `GET /api/v1/audit` (`owner` only): every write above records a non-sensitive audit row (actor, action, entity type/id, summary) via `family_cfo_api/audit.py`. Summaries never contain amounts, balances, passwords, or tokens — asserted by a test.

Not implemented in M9:

- No general scenario-planning write API — scenarios are still created only by the purchase advisor; a user-facing scenario CRUD remains backlog.
- No public/open registration semantics, password reset, or credential rotation — Release-Readiness security work.
- No retroactive audit backfill for pre-M9 mutations (auth, pairing, imports apply/discard, reports, backups) — tracked as a backlog follow-up.
- No Angular UI — that is M11.

### Household Bootstrap and Data Entry Flow

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/households \
  -H 'content-type: application/json' \
  -d '{"display_name":"My Home","base_currency":"USD","owner_email":"me@example.com","owner_password":"change-me-123","owner_display_name":"Me"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

ACCOUNT=$(curl -s -X POST http://127.0.0.1:8000/api/v1/accounts \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"name":"Checking","type":"checking","currency":"USD"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

curl -s -X POST "http://127.0.0.1:8000/api/v1/accounts/$ACCOUNT/balances" \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"balance":{"amount_minor":500000,"currency":"USD"}}'
```

## M10 Scope

Closes the unbuilt `conversations`/`conversation_messages` gap that M4/M6 deferred to "a later milestone" that did not exist. Additive to M6 chat — the `ChatResponse` shape is unchanged.

Implemented:

- `POST /api/v1/chat/messages` now persists the thread: with no `conversation_id` (or an unknown one) it creates a conversation titled from the first message; with an existing owned `conversation_id` it appends. Both the user message and the assistant answer are written in one transaction, and the assistant message carries the `recommendation_id` so a stored turn still links to its grounding `recommendations` row.
- `GET /api/v1/conversations` (any role) lists the household's conversations, newest first, with a `message_count`; `GET /api/v1/conversations/{id}` (any role) returns the ordered message thread.
- `DELETE /api/v1/conversations/{id}` (`owner`/`adult`) hard-deletes a conversation and its messages — the privacy escape hatch — leaving the linked `recommendations` audit rows intact.

Not implemented in M10:

- No change to what the assistant computes: still the bounded, deterministic-grounded M6 snapshot. No multi-turn context-carrying, retrieval, or memory (tracked as backlog).
- No raw-prompt persistence — only the user message and the guardrail-validated assistant answer are stored, same M4 rule.
- No dashboard chat UI (tracked as backlog; M11 covers the four M5 shells only).

## M13 Scope (Security Hardening)

Implemented (backend half; the TLS half lives in `docker/`):

- `POST /api/v1/auth/sessions/refresh` (bearer) rotates the session: it revokes the presented token and returns a fresh `AuthSession`. The old token is immediately `401`.
- `DELETE /api/v1/auth/sessions` (bearer) logs out — revokes the presented token (`204`).
- Session expiration is enforced by `get_session_context` (expired/revoked tokens are `401`); the TTL is configurable via `FAMILY_CFO_SESSION_TTL_HOURS` (default 12).
- Consolidated security tests in `tests/test_security.py`: the viewer/adult/owner authorization matrix, logging redaction through the handler, and a no-telemetry scan of first-party source. See also ADR 0008 (`docs/adr/0008-security-hardening-decisions.md`) for the resolved threat-model decisions.

The web tier's HTTPS/TLS termination, security headers, and self-signed-cert bootstrap are in `docker/` (see `docker/README.md`).

## M15 Scope (Annual Report)

Adds `annual` as a third report type alongside weekly/monthly (`POST /api/v1/reports/generate`, the scheduled worker job, and `ReportType`). An annual report covers the **prior calendar year**, scales the engine's monthly income/bills figures up by 12, and reuses the M8 report content and narrative exactly. Migration `0030` widens the `reports` type check.

## M14 Scope (Debt Payoff and Retirement Projections)

Finishes the M3-deferred debt/retirement backlog.

- Accounts carry optional debt terms — `annual_interest_rate` and `minimum_payment` (liability types). Set them on create/update (`owner`/`adult`); the `Account` read model exposes them.
- The purchase advisor's `debt` impact is now real: it runs `calculate_debt_payoff` over liability accounts that have terms and reports months-to-payoff and remaining interest, plus a note for debts still lacking terms — replacing the old "cannot be modeled" placeholder.
- `POST /api/v1/advisor/retirement` projects retirement savings (deterministic monthly compound growth) for a scenario — current age, retirement age, current savings, monthly contribution, expected annual return, optional annual expenses — and returns a grounded `Recommendation`, persisted like the purchase advisor. Any household role; deterministic explanation (no LLM narration — tracked follow-up).

Not implemented in M14: an open-ended scenario-planning API ("should we refinance?") and inflation/tax/drawdown modeling in the retirement projection — see the M14 non-goals in the roadmap.

## M16 Scope (Agentic Tool-Calling)

`POST /api/v1/chat/messages` becomes an open-ended conversational advisor when a tool-calling runtime is enabled — the answer to a per-question API that doesn't scale (ADR 0009). The local model orchestrates the deterministic engine instead of guessing.

- `family_cfo_api/ai_tools.py`: the tool library exposed to the model — read tools (`get_net_worth`, `get_emergency_fund`, `get_debt_outlook`) and compute tools (`project_purchase_impact`, `future_value`, `project_retirement`, `debt_payoff`). Each is a thin wrapper: **validate arguments** (type/range/currency), run the existing deterministic calculation, persist a `financial_calculations` row, and return a structured result with a `calculation_ref`. This module is the trust boundary — read tools are scoped to the caller's household from the session (the model never supplies an entity id), and bad arguments/missing facts return `{"error": ...}` payloads (never raised) so the model corrects itself or asks the user.
- The route runs `run_tool_calling_loop` (ai-orchestrator) against the household's runtime, then a grounding guardrail (`grounded_values` + `validate_recommendation`): any figure in the final answer that doesn't trace to a tool-call trace value fails closed to the deterministic snapshot. The loop not converging, the runtime being unavailable, or no runtime configured (the default) all fall back the same way — so deployments without vLLM see the unchanged M6/M10 deterministic snapshot.
- Recommendations from this path are persisted with `explanation_source = "agentic_tool_calling"` and cite the tools' `financial_calculations` rows; the turn is stored via M10 conversations. No raw prompt or model response is logged — only household id, recommendation id, conversation id, and the `explanation_source`.

Not implemented in M16: tools that mutate state or move money (read/compute only), external/cloud models, and document/vector retrieval — see the M16 non-goals in the roadmap.

## Setup

```bash
cd apps/api
python3 -m venv .venv
. .venv/bin/activate
make install
```

`make install` also installs the sibling `family-cfo-financial-engine`, `family-cfo-ai-orchestrator`, `family-cfo-ocr-worker`, `family-cfo-scheduler`, and `family-cfo-backup` packages from `services/` in editable mode, since the API depends on all five but none is a published package.

Optional local configuration reference:

```bash
cp .env.example .env
```

The app reads environment variables from the process environment. Export values in your shell or through Docker Compose when overriding defaults.

## Run

```bash
cd apps/api
. .venv/bin/activate
make run
```

Health check:

```bash
curl http://127.0.0.1:8000/api/v1/health
```

Expected response:

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

Run the background worker (a separate process from the API server) — polls for pending imports every 30 seconds, weekly/monthly reports hourly, and runs a backup once a day:

```bash
cd apps/api
. .venv/bin/activate
make worker
```

## Test and Lint

```bash
cd apps/api
. .venv/bin/activate
make test
make lint
```

Format:

```bash
make format
```

## OpenAPI

Generate the FastAPI OpenAPI document:

```bash
make openapi
```

Check implemented routes against the shared contract:

```bash
make check-openapi
```

The shared contract remains `shared/openapi/family-cfo.v1.yaml`. Implemented FastAPI routes must exist in that shared contract before client generation.

## Migrations

Alembic uses `database/migrations` as the migration script directory.

Run migrations:

```bash
cd apps/api
. .venv/bin/activate
make migrate
```

Override the database URL without committing secrets:

```bash
FAMILY_CFO_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/family_cfo make migrate
```

M2 adds the household/account/transaction/bill/income/goal/scenario tables and `financial_calculations` as chained migrations (`0002`–`0014`); M3 adds `recommendations` (`0015`); M4 adds `recommendations.model_version`/`prompt_version` and `ai_runtime_configs` (`0016`–`0017`); M6 backend support adds `pairing_sessions`, `paired_devices`, and `auth_sessions.device_id` (`0018`); M7 adds `imports`, `import_files`, `documents`, `document_extractions`, and `transactions.import_id`/`possible_duplicate` (`0019`–`0023`); M8 adds `reports` and `backup_jobs` (`0024`–`0025`); M9 adds `audit_events` (`0026`); M10 adds `conversations` and `conversation_messages` (`0027`–`0028`); M14 adds `accounts.annual_interest_rate`/`minimum_payment_minor` and two new calculation types (`0029`); M15 adds `annual` to the reports type check (`0030`). `make migrate` applies all of them.

Set `FAMILY_CFO_IMPORT_STAGING_DIR` (default `./data/import-staging`) to control where uploaded import/document files are staged on disk. Set `FAMILY_CFO_BACKUP_DIR` (default `./data/backups`), `FAMILY_CFO_BACKUP_RETENTION_COUNT` (default `7`), and `FAMILY_CFO_BACKUP_ENCRYPTION_KEY` (no default — required for any backup/restore) to control encrypted backup storage.

## Fixtures

`family_cfo_api.fixtures` seeds a synthetic demo household — never real financial data — for local development and tests:

```bash
cd apps/api
. .venv/bin/activate
python -c "
from family_cfo_api.db import create_database_engine
from family_cfo_api.config import get_settings
from family_cfo_api import fixtures

engine = create_database_engine(get_settings().database_url)
fixtures.seed_demo_household(engine)
"
```

This creates a demo household owned by `demo@family-cfo.local` (password `demo-password-123`) plus a `viewer@family-cfo.local` account, three accounts, sample transactions, bills, an income source, and a goal. Tests seed the same fixtures against an in-memory SQLite database via the `demo_engine`/`demo_client` fixtures in `tests/conftest.py`, so the test suite never requires a running PostgreSQL server.
