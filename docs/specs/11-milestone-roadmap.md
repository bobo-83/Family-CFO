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

## M3: Purchase Advisor

- Scenario input API
- Financial impact calculation
- Recommendation response structure
- LLM explanation adapter stub
- Integration tests

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
