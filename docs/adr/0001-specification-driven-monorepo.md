# ADR 0001: Use a Specification-Driven Monorepo

Status: Accepted

## Context

Family CFO includes a SwiftUI app, Angular dashboard, FastAPI backend, local AI runtime integration, deterministic financial engine, workers, Docker deployment, shared schemas, and documentation.

The project needs one versioned source of truth for specifications, API contracts, and implementation.

## Decision

Use a single Git monorepo and require specifications before implementation.

## Consequences

- Shared schemas and OpenAPI live with implementation.
- One CI pipeline can validate cross-component contracts.
- Codex and other coding agents can reason across the full system.
- Repository boundaries must stay clear through directory ownership and component READMEs.
