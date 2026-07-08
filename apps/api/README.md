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
- Raw photo/document upload endpoints.

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

## Setup

```bash
cd apps/api
python3 -m venv .venv
. .venv/bin/activate
make install
```

`make install` also installs the sibling `family-cfo-financial-engine` and `family-cfo-ai-orchestrator` packages from `services/financial-engine` and `services/ai-orchestrator` in editable mode, since the API depends on both but neither is a published package.

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

M2 adds the household/account/transaction/bill/income/goal/scenario tables and `financial_calculations` as chained migrations (`0002`–`0014`); M3 adds `recommendations` (`0015`); M4 adds `recommendations.model_version`/`prompt_version` and `ai_runtime_configs` (`0016`–`0017`); M6 backend support adds `pairing_sessions`, `paired_devices`, and `auth_sessions.device_id` (`0018`). `make migrate` applies all of them.

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
