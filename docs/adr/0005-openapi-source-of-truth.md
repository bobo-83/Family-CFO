# ADR 0005: Use OpenAPI as the Client Contract Source of Truth

Status: Accepted

## Context

The iPhone app and Angular dashboard must use consistent DTOs and API behavior.

## Decision

The backend OpenAPI contract is the source of truth. Swift and Angular clients are generated from the same specification.

## Consequences

- API changes start in OpenAPI.
- Client DTOs should not be hand-maintained independently.
- CI should validate that required API spec files exist and later should validate generated client consistency.
