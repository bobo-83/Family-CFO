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

`make install` also installs the sibling `family-cfo-financial-engine` package from `services/financial-engine` in editable mode, since the API depends on it for M2 calculations but the two are not published packages.

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

The shared contract remains `shared/openapi/family-cfo.v1.yaml`. M1 only checks routes implemented by the FastAPI app.

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

M2 adds the household/account/transaction/bill/income/goal/scenario tables and `financial_calculations` as chained migrations (`0002`–`0014`); `make migrate` applies all of them.

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
