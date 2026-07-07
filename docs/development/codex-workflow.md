# Codex Workflow

Codex is expected to work one milestone at a time.

## Standard Loop

1. Read the relevant spec files.
2. Update specs if the feature is underspecified.
3. Implement the smallest milestone slice.
4. Add unit tests.
5. Add integration tests for component boundaries.
6. Update documentation.
7. Run verification commands.
8. Check off completed tasks in `docs/specs/12-implementation-tasks.md`.
9. Commit with a clear message when requested or when the workflow calls for it.

## Constraints

- Do not bypass the financial engine for calculations.
- Do not duplicate DTOs outside generated clients.
- Do not add cloud dependencies for sensitive data.
- Do not commit real financial data.
- Keep major components replaceable.

## Useful First Reads

- `README.md`
- `AGENTS.md`
- `docs/specs/README.md`
- `docs/specs/11-milestone-roadmap.md`
- `shared/openapi/family-cfo.v1.yaml`
