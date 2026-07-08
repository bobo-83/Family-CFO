# Local Development Guide

How to run and work on Family CFO outside Docker. The monorepo is a set of
independent Python packages plus an Angular app; each has its own README with
the authoritative detail — this guide ties them together.

## Layout

- `apps/api` — FastAPI backend (owns migrations and the OpenAPI-implemented routes).
- `apps/web` — Angular dashboard.
- `services/*` — independent Python packages: `financial-engine`,
  `ai-orchestrator`, `ocr-worker`, `scheduler`, `backup`. No cross-dependencies;
  `apps/api` depends on all of them.
- `database/migrations` — Alembic migrations, shared via `apps/api`'s config.
- `shared/openapi/family-cfo.v1.yaml` — the source-of-truth API contract.

## Backend (apps/api)

```bash
cd apps/api
python3 -m venv .venv
. .venv/bin/activate
make install     # installs the API + all five service packages (editable)
make test        # pytest
make lint        # ruff
make run         # uvicorn on :8000 (needs a database — see below)
```

Tests use an in-memory SQLite database and seeded synthetic fixtures, so they
need **no** running PostgreSQL. See `apps/api/README.md` for the full endpoint
list per milestone, the auth flow, and the fixtures.

### Database and migrations

The app targets PostgreSQL; tests and quick local runs can use SQLite. Apply
migrations:

```bash
make migrate                                    # against FAMILY_CFO_DATABASE_URL
FAMILY_CFO_DATABASE_URL=sqlite:////tmp/dev.sqlite3 make migrate
```

Migration discipline (see `database/README.md`): additive-only once pushed;
verify every new migration with a full `upgrade head` → `downgrade base` →
`upgrade head` cycle; revision ids must be ≤ 32 characters (PostgreSQL's
`alembic_version` column width).

### OpenAPI contract

The shared contract is the source of truth. After changing a route:

```bash
make check-openapi   # fails if an implemented route drifts from the contract
```

Update `shared/openapi/family-cfo.v1.yaml` first, then implement.

## Frontend (apps/web)

```bash
cd apps/web
npm install
npm start                 # dev server on :4200, proxies /api to :8000
npm test                  # Vitest (jsdom, no browser)
npm run build
npm run generate:client   # regenerate the API client from the shared contract
```

The generated client lives in `src/app/api-client` (committed, never
hand-edited); components depend on `core/api.service.ts` so tests can substitute
it via `TestBed`. See `apps/web/README.md`.

## Services

Each service package is self-contained:

```bash
cd services/<package>
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest && python -m ruff check src tests
```

- `financial-engine` — deterministic `Money` and calculations (net worth, cash
  flow, budget, emergency fund, goals, purchase impact, debt payoff).
- `ai-orchestrator` — the `RuntimeAdapter` seam, vLLM adapter, and guardrails.
- `ocr-worker` — PDF text extraction (real) and a deterministic OCR test adapter.
- `scheduler` — generic retry/interval job runner (APScheduler).
- `backup` — `BackupAdapter` seam, pg_dump/SQLite adapters, Fernet encryption.

## The whole stack in Docker (dev override)

```bash
cp .env.example .env   # set POSTGRES_PASSWORD
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

The dev override exposes the API on 8000, enables DEBUG logging, and reloads the
API on source edits. See [Deployment](./deployment.md) and `docker/README.md`.

## Before you commit

- `make test && make lint && make check-openapi` in `apps/api`.
- The touched service package's `pytest`/`ruff`.
- `npm test && npm run build` in `apps/web` if the UI changed.
- Commit messages follow `.gitmessage` (type(scope): subject ≤72 chars, plus
  Why / What changed / Verification / Sensitive data check).
