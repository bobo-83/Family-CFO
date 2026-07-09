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

---

## Release v0.2.0 (2026-07-09)

Delta since v0.1.0 — seventeen milestones (M14–M32):

- **AI advisor**: agentic tool-calling over the deterministic engine (M16); local AI + turnkey deploy on by default (M17); chat + sign-up UI (M19); photo attachments with describe-then-ground vision routing (M21); live exchange rates + self-hosted price search (M24/M28); conversational memory (M30); per-response model attribution (M25); playful persona over unchanged grounding (M31); AWQ quantization guidance, measured 2.5× (M29).
- **Model ops**: curated + Hugging Face model picker with hardware-fit planning (M22); one-click apply via the model-manager sidecar (M23); swap-model.sh.
- **Data**: debt payoff + retirement projections (M14); annual reports (M15); SimpleFIN institution connections with two-tier transaction dedupe incl. the CSV pipeline (M27).
- **Platform**: dashboard redesign + iPhone-class mobile support (M20/M26); deploy/patch/doctor/e2e scripts with system requirements (M17/M18); security passes — SSRF allowlist, login rate limiting, upload caps, CSPRNG pairing secret, prod docs gating (M18), single-household bootstrap lockout + full audit coverage (M32).

Verification at tag time: financial-engine 65, ai-orchestrator 24, model-manager 5, api 247, web 67 — all green; builds clean; compose config valid; contract in sync; live GB10 deployment serving Qwen2.5-32B-AWQ + Qwen2.5-VL-7B.

Still deferred (tracked in `docs/specs/12-implementation-tasks.md` backlogs): the iOS app (needs macOS), OFX DirectConnect + OFX/QFX parsing, real OCR engine + PDF line-item parsing, vector store/retrieval, multi-currency households, budget management UI, monitoring container, long-conversation summarization. *(Post-tag: M34 closed OFX/QFX parsing, real OCR, and PDF line-items.)*
