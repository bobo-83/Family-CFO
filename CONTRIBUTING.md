# Contributing

Family CFO is specification-driven.

Before implementation, a change must have:

- Product requirement
- ADR when architecture or long-term behavior changes
- API contract changes when external behavior changes
- Tests appropriate to risk
- Documentation updates

## Pull Request Expectations

- Keep sensitive sample data out of the repository.
- Do not introduce telemetry, cloud-only dependencies, or external AI calls without an ADR.
- Keep major components replaceable through clean interfaces.
- Keep deterministic financial calculations separate from LLM reasoning.
- Update OpenAPI before changing generated clients.

## Local Development

The initial repository contains specifications and placeholders. Runtime-specific setup belongs in the component directories once each milestone begins.
