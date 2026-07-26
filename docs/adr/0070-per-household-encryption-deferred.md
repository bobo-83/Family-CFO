# 0070 — Per-household encryption at rest: considered, deferred

Date: 2026-07-26
Status: Deferred (see issue #155)

## Context

Reviewing multi-household isolation (user question, 2026-07-26): SimpleFIN
connections, accounts, and transactions are strictly household-scoped, every
session binds to one household, and system-admin rights (ADR 0065) unlock
only box-level surfaces — no cross-household data reads exist in the API.
But isolation is enforced by the APPLICATION, not cryptography: Postgres
rows are plaintext with household-id scoping, so box shell access or the
whole-box backup key reads everything.

## Decision

Defer per-household encryption. For the current deployment — one extended
family on hardware they own — the application-layer isolation plus
encrypted credentials and encrypted backups is the right cost/benefit.
Issue #155 records the design sketch and the revisit trigger: households
that don't fully trust each other or the box operator, or any hosted
multi-tenant offering.

## Rejected options (for now)

- **Per-household data keys wrapped by member credentials** — defeats
  server-side aggregation (net worth, budgets, the advisor's grounded
  tools) and unattended jobs (daily sync, snapshots), which would need
  standing key access that weakens the guarantee anyway.
- **Full-disk encryption as a substitute claim** — protects against
  stolen hardware, not against the box operator; must not be marketed as
  household-vs-household isolation.

## Invariant

Until #155 lands, no surface may CLAIM cryptographic isolation between
households; docs and UI must describe isolation as application-enforced.
