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

Not implemented in M1:

- Authentication
- Pairing
- Household financial context
- Financial calculations
- AI orchestration
- Imports
- Reports

## Setup

```bash
cd apps/api
python3 -m venv .venv
. .venv/bin/activate
make install
```

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
