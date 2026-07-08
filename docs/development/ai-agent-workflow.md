# AI Agent Workflow

Codex, Claude, and other AI coding agents are expected to work one milestone at a time.

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

## Commit Message Template

This repo includes `.gitmessage`. GitHub Actions validates pushed and pull request
commit messages against it, so new clones do not require setup for enforcement.

For local validation before committing, run this optional one-time setup per checkout:

```bash
scripts/setup-git-hooks.sh
```

The optional local `commit-msg` hook and the CI workflow both use
`scripts/validate-commit-message.sh`, which checks for a typed subject line plus
filled `Why`, `What changed`, `Verification`, and `Sensitive data check` sections.

## Command Docs

Component-specific run, test, lint, migration, and generation commands live in the component README files. For the backend API, use `apps/api/README.md`.

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
