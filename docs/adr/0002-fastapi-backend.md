# ADR 0002: Use FastAPI for the Backend API

Status: Accepted

## Context

The backend needs strong Python ecosystem support for AI integration, financial calculations, background processing, and OpenAPI generation.

## Decision

Use FastAPI as the primary backend API framework.

## Alternatives Considered

- NestJS: strong TypeScript framework, but less direct access to Python AI and financial tooling.

## Consequences

- Python becomes the backend implementation language.
- OpenAPI generation can be integrated directly with the API.
- The backend must keep clean boundaries so the financial engine and AI orchestrator remain independently testable.
