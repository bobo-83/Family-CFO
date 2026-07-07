# ADR 0006: Use Docker Compose for the Home Server

Status: Accepted

## Context

Family CFO is self-hosted and should be installable by users running a home server.

## Decision

Use Docker Compose as the primary deployment path.

## Consequences

- Services are isolated into containers.
- Persistent volumes must be documented and backed up.
- vLLM, PostgreSQL, Qdrant, FastAPI, Angular, and workers can be started together.
- Future deployment targets can be added without changing the core architecture.
