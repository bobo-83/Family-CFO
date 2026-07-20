# ADR 0041: Document scans live on the page that owns the result

## Status

Accepted.

## Context

Scanning grew up scattered. The iOS Overview toolbar carried a "Capture" menu
with two unrelated scans (receipt → advisor question, W-2 → income earner),
while the loan-statement scan already lived on the Debts page next to the form
it fills. A member looking to photograph a paper bill found no scan at all —
bills could only be typed in or accepted from transaction-derived suggestions.
The Overview placement was flagged by the family as arbitrary: nothing about a
read-only dashboard says "camera lives here."

## Decision

**Every scan lives where its result lives.** The Overview Capture menu is
removed; each flow moves to (or is created on) its domain page, on both
platforms (ADR 0025):

- **Receipt → Advisor chat.** A "Scan a receipt" entry in the chat's own attach
  menu; the on-device OCR flow (ADR 0011 — text leaves the phone, the photo
  doesn't unless OCR fails) is unchanged, it just starts where the question is
  asked. The question now lands in the CURRENT conversation instead of spawning
  a detached one.
- **W-2 → the new iOS Income tab.** Income becomes a first-class tab (like
  Bills and Debts, per the family's explicit call — not a Settings row):
  analyzed sources and rollup, tax estimate, earner list with delete, and the
  W-2 scan as the add-earner on-ramp. Web already had this page (Income & Tax).
  Gated by the existing `income.manage` right, newly surfaced as
  `RolePolicy.canManageIncome`.
- **Bill → the Bills page**, via a new `POST /bills/scan` endpoint mirroring the
  loan-statement scanner: photo/PDF/paste → on-box vision model → candidate
  values (biller, amount, due date, frequency) that PREFILL the add-bill form.
  Nothing is saved until the user confirms — a model never writes financial
  ground truth directly (M73 rule).

## Invariant

> A scan entry point sits on the page that owns what the scan produces, and a
> scan returns candidates that prefill a form — never a saved record. Prefill
> never overwrites what the user already typed. Any new scannable document gets
> its scanner on its own domain page on BOTH clients, not on a shared hub.

## Rejected

- **Keeping Overview's Capture menu as a shortcut hub** alongside the domain
  pages: two entry points to maintain per scan, and the hub misleads (it holds
  whichever scans someone remembered to add, not all of them).
- **Bill scan creating the bill directly**: violates the candidates-only rule
  every other scanner follows; a misread amount would silently become ground
  truth.
- **Income as a Settings row**: the family explicitly wants income to be a
  peer of Bills/Debts, and Settings is for configuration, not money data.
