# Release Checklist

Run before tagging a release. The detailed task tracking lives in
[docs/specs/12-implementation-tasks.md](./specs/12-implementation-tasks.md); this
is the pre-tag gate.

## Code and tests

- [ ] `apps/api`: `make test && make lint && make check-openapi` all pass.
- [ ] Each `services/*` package: `python -m pytest && python -m ruff check src tests` pass.
- [ ] `apps/web`: `npm run build && npm test` pass; generated client is not stale
      (`npm run generate:client` produces no diff).
- [ ] Full migration cycle verified: `upgrade head` → `downgrade base` →
      `upgrade head` on a scratch database.

## Contract and specs

- [ ] Implemented API routes match `shared/openapi/family-cfo.v1.yaml`
      (`make check-openapi`).
- [ ] Accepted specs in `docs/specs` reflect implemented behavior; the
      Acceptance State in `docs/specs/README.md` is current.
- [ ] Every milestone task is checked off or explicitly deferred with a note.

## Deployment

- [ ] `docker compose config` valid (base and dev override).
- [ ] Clean-checkout `docker compose up -d` brings the core stack healthy.
- [ ] Health responds over TLS through the web proxy.
- [ ] Backup + restore round trip works against the containerized database.

## Security and privacy

- [ ] No secrets committed: only `.env.example` (placeholders) is tracked;
      gitleaks passes.
- [ ] `pip-audit` shows no unaddressed high-severity vulnerabilities.
- [ ] No real financial data or non-synthetic fixtures committed.
- [ ] Redaction and no-telemetry tests pass.

## Documentation

- [ ] Root `README.md` and component READMEs current.
- [ ] The [guides](./guides/README.md) (deployment, local dev, backup/restore,
      security, troubleshooting) are accurate for this release.
- [ ] New ADRs recorded for any architecture/security/storage decisions.

## Tag

- [ ] Version strings agree (`apps/api` `__version__`/`pyproject`, service
      `pyproject`s, `HealthResponse.version`).
- [ ] Create an annotated tag (`git tag -a vX.Y.Z`) with a summary of the
      release, and push it.

## Known deferrals for this release (0.1.0)

Documented and intentional, not blockers:

- iOS app (macOS-only; not buildable in the Linux dev environment).
- Real vLLM deployment and real OCR engine (adapters + deterministic test
  stand-ins ship; heavy vendors are opt-in/future).
- OFX/QFX import parsing (planning only).
- Vector store / retrieval (no consumer yet — backlog).
- Debt-payoff/retirement/scenario planning, annual report, dashboard chat UI,
  first-run setup wizard, extended audit coverage (backlog).
- Reverse proxy / monitoring / rate limiting ("Future Containers").
