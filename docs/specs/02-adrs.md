# ADRs

Architecture Decision Records live in `docs/adr`.

## Initial Decisions

- [0001: Use a specification-driven monorepo](../adr/0001-specification-driven-monorepo.md)
- [0002: Use FastAPI for the backend API](../adr/0002-fastapi-backend.md)
- [0003: Separate deterministic finance from LLM reasoning](../adr/0003-deterministic-financial-engine.md)
- [0004: Use a local AI runtime abstraction with vLLM first](../adr/0004-local-ai-runtime-abstraction.md)
- [0005: Use OpenAPI as the client contract source of truth](../adr/0005-openapi-source-of-truth.md)
- [0006: Use Docker Compose for the home server](../adr/0006-docker-compose-home-server.md)
- [0007: Require replaceable component interfaces](../adr/0007-replaceable-component-interfaces.md)
- [0008: Security hardening decisions](../adr/0008-security-hardening-decisions.md)
- [0009: Agentic tool-calling over deterministic financial primitives](../adr/0009-agentic-tool-calling.md)
- [0015: Institution connections via SimpleFIN; transaction dedupe](../adr/0015-bank-connections-and-dedupe.md)
- [0019: Bank-sync cadence & provider rate limits](../adr/0019-bank-sync-cadence-and-rate-limits.md)
- [0020: Safe-to-spend recurring obligations & subscription forecast](../adr/0020-safe-to-spend-recurring-obligations.md)
- [0021: Activity-log undo framework](../adr/0021-activity-log-undo-framework.md)
- [0022: Edit bills inline from the Bills tab](../adr/0022-edit-bills-inline.md)
- [0023: Every mutation is undoable (undo-completeness rule)](../adr/0023-every-mutation-is-undoable.md)
- [0024: Bills tab redesign — the payment timeline](../adr/0024-bills-payment-timeline.md)
- [0025: Cross-client feature parity (iOS ↔ Angular dashboard)](../adr/0025-cross-client-feature-parity.md)
- [0026: Overview cash outlook; safe-to-spend reframed as a stress test](../adr/0026-overview-cash-outlook.md)
- [0027: The month spending plan — "left to spend this month"](../adr/0027-month-spending-plan.md)
- [0028: Every statement input accepts paste](../adr/0028-statement-inputs-accept-paste.md)
- [0029: One monorepo version, verified at every seam](../adr/0029-monorepo-version.md)
- [0030: No personal identifiers or environment specifics in the repo](../adr/0030-no-personal-identifiers.md)
- [0031: Advisor is educational, not financial advice — disclaimer everywhere](../adr/0031-advisor-disclaimer.md)

## ADR Rules

**Record the decision as part of the change that makes it — a decision that
lives only in chat or a commit message does not exist.** Every non-trivial
**product, finance, UX, architecture, security, storage, or runtime** decision
gets written down before (or with) the code that implements it:

- **Cross-component / architectural / security / runtime / storage** → a new **ADR**
  in `docs/adr` (this section indexes them).
- **Product / finance / UX behaviour** (how a number is computed, what a screen does,
  what a gesture means) → an ADR *or* an update to the relevant spec in `docs/specs`
  (e.g. `01-prd.md`, `08-mobile-spec.md`), whichever fits.
- Always capture **the rejected options and the tradeoff** — the "why not" is the
  part future-you can't reconstruct.
- State an **invariant** the decision protects, so it can be enforced by a test or
  review (see ADR 0019/0020/0021 for the pattern).
- **Cross-client parity (ADR 0025):** a user-facing feature change ships on BOTH
  the iOS app and the Angular dashboard in the same change; a platform-bound
  exception must be named in the feature's ADR.
- **Do not overwrite history.** Supersede with a new ADR; mark the old one Superseded.
