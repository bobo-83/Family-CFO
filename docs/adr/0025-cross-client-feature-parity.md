# ADR 0025: Cross-client feature parity (iOS ↔ Angular dashboard)

## Status

Accepted. Generalizes the M96 "uniform experience" rule (same capability behaves
identically on every *screen*) to the level of *clients*.

## Context

The Bills payment timeline (ADR 0024) shipped on iOS with the Angular dashboard
left on the old category-list design; bill *editing* (ADR 0022) had also shipped
iOS-only. Each gap was individually reasonable ("the phone is the primary
client") and collectively wrong: the household's data has one truth, but what a
family member could *see and do* depended on which device they picked up. The
user made it explicit: **"Any feature changes in iOS should also happen on the
Angular dashboard and vice versa."**

## Decision

**A user-facing feature change lands on both clients as part of the same change,
not as a follow-up.** Concretely:

1. A change to what a user can **see** (a new view, figure, or status) or **do**
   (a new or changed action) on one client is not done until the other client
   has it too. One shared backend endpoint feeds both (ADR 0005 contract-first:
   spec → both generated clients).
2. **Parity is of capability, not pixels.** Each client keeps its own idiom —
   iOS may use a tap-to-edit sheet where the dashboard uses an inline form; a
   drill-down subpage on the phone may be a section on the web. What must match
   is what the user can learn and change, and the vocabulary used for it
   (section names, status words, figures).
3. **Allowed exceptions** are capabilities tied to a platform: camera/share-
   extension capture, widgets, push notifications (iOS); operator/admin surfaces
   like AI-runtime management or OTA hosting (dashboard). An exception is named
   in the feature's ADR, not assumed.
4. **The check is part of done:** before closing a feature, ask "does the other
   client now show/do this?" If it can't in this change for a stated reason,
   the ADR records the gap and the reason — an unrecorded gap is a bug.

## Invariant

> What a household member can see and do about their money is the same on the
> phone and the dashboard. Any intentional exception is platform-bound and
> written down in an ADR.

## Rejected

- **iOS-first, dashboard follows "later"** — "later" demonstrably drifted into
  never until the user caught it; the gap this rule exists to prevent.
- **Pixel-identical UIs** — forcing one design across SwiftUI and Angular makes
  both worse; parity of capability is the useful guarantee.
- **Automated parity tests across clients** — no shared UI layer to assert on;
  the enforceable points are the shared OpenAPI contract and the ADR checklist.
