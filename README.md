# Family CFO

Privacy-first open source AI financial advisor for families.

Family CFO is a self-hosted home-server application that combines a deterministic financial engine with a local reasoning model. The financial engine calculates. The LLM explains.

## Vision

Family CFO should behave like a trusted family Chief Financial Officer available 24/7. It should answer practical questions such as:

- Can I afford this?
- Should we eat out tonight?
- Can we take this vacation?
- Can I retire at 55?
- Should I refinance my mortgage?
- How will this purchase affect our long-term goals?

Sensitive financial information remains under the user's control. No bank statements, bills, credentials, investment data, tax documents, spending history, or AI reasoning should require a third-party cloud service.

## Repository Status

This repository is intentionally starting with specifications before implementation.

The first development gate is the Spec Kit:

1. PRD
2. ADRs
3. Domain Model
4. OpenAPI
5. Database Schema
6. Security Model
7. AI Orchestration
8. Mobile Spec
9. Angular Dashboard Spec
10. Docker Spec
11. Milestone Roadmap

Implementation should begin only after these documents are reviewed and accepted.

## Monorepo Layout

```text
apps/
  ios/                 SwiftUI iPhone app
  web/                 Angular desktop dashboard
  api/                 FastAPI backend

services/
  ai-orchestrator/     LLM runtime abstraction and tool orchestration
  financial-engine/    Deterministic financial calculations
  ocr-worker/          OCR and document processing workers
  scheduler/           Scheduled jobs and reports

docker/                Docker Compose and container assets
database/              Database schema and migrations
shared/                Shared schemas, OpenAPI, generated client sources
docs/                  Product, architecture, security, and workflow specs
```

## Architectural Principles

- Privacy first: no telemetry, no advertising, no mandatory cloud services.
- Local AI first: local reasoning by default through a replaceable runtime.
- Deterministic finance: calculations are auditable and never delegated solely to an LLM.
- Explainable AI: every recommendation must include assumptions, tradeoffs, alternatives, and confidence.
- Replaceable components: AI runtime, vector database, OCR, authentication, and financial modules use clean interfaces.
- API as source of truth: SwiftUI and Angular clients generate from the same OpenAPI contract.

## Planned Runtime Stack

- iPhone app: SwiftUI, Face ID, Vision Framework, Foundation Models where available.
- Desktop dashboard: Angular.
- Backend API: FastAPI.
- Financial engine: deterministic Python service/library.
- AI runtime: vLLM first, OpenAI-compatible runtime abstraction.
- Vector store: Qdrant first, replaceable behind an interface.
- Storage: PostgreSQL.
- Deployment: Docker Compose.

## Development Workflow

Every feature starts with:

- Product requirement
- ADR when architecture changes
- API contract
- Tests
- Documentation update
- Commit message

See [AGENTS.md](./AGENTS.md) and [docs/development/codex-workflow.md](./docs/development/codex-workflow.md).

## License

MIT. See [LICENSE](./LICENSE).
