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

## No personal identifiers (ADR 0030)

Never commit personal or environment-specific values: your Apple Team id, real
private IPs or hostnames, personal home paths, or secrets. Read them from env or
a gitignored `.deploy.env`; use placeholders (`192.168.1.x`, `your-login`) in
code and docs. `scripts/check-repo-hygiene.sh` enforces this in CI and as a
pre-commit hook — run `scripts/setup-git-hooks.sh` once to install the hooks. To
block your own literal values locally, list them in a gitignored
`.repo-hygiene-deny`.
