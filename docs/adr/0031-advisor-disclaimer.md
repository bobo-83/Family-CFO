# ADR 0031: Advisor is educational, not financial advice — disclaimer everywhere

## Status

Accepted.

## Context

Family CFO answers open-ended money questions with a local LLM ("the Advisor").
Even though a deterministic engine owns the numbers and guardrails reject
fabricated figures, the model can still be wrong, incomplete, or out of date, and
its answers read authoritatively (confidence scores, "Answered by <model>"). As
the project goes open-source and self-hosters run it for their own families, we
need to set expectations plainly and reduce liability: the app organizes finances
and explains figures; it is **not** a financial, investment, tax, or legal
advisor, and no advisory or fiduciary relationship is created by using it.

This is about honest expectation-setting and the "as is" posture the MIT
`LICENSE` already establishes — not a substitute for legal counsel, which anyone
operating this as a *hosted service for others* should obtain separately.

## Decision

**The Advisor is presented as educational/informational guidance, never
professional advice, and a disclaimer is visible wherever advice is given or the
project is described.** Concretely:

1. **`DISCLAIMER.md`** at the repo root carries the full text: no professional
   advice / no fiduciary relationship, AI output can be wrong (verify before
   acting), the user owns their decisions, self-hosted and provided "as is" (→
   `LICENSE`), and not affiliated with any institution.
2. **README** has a short Disclaimer section linking to `DISCLAIMER.md`.
3. **In-app, on every advisor surface**, an always-visible one-line disclaimer:
   *"Educational guidance from a local AI — not financial, tax, or legal advice.
   It can be wrong; verify before acting."* It ships on **both** clients (iOS
   `ChatView`, Angular `chat`) per [ADR 0025](./0025-cross-client-feature-parity.md),
   with identical copy kept in sync with `DISCLAIMER.md`.

The in-app line is **persistent** (not a one-time dismissible banner) so it is
present at the moment a user reads any answer, and a web test asserts it renders.

## Invariant

> Every place the app gives an answer, or the project describes itself, states
> that this is educational guidance and not professional advice. The wording is
> identical on iOS and web; changing it means changing all three surfaces
> (`DISCLAIMER.md`, `ChatView`, `chat`) together.

## Rejected

- **A one-time "I understand" gate.** Higher friction and, once dismissed, absent
  exactly when a user acts on a later answer. A persistent line is always in view.
- **Relying on the LICENSE warranty clause alone.** It governs the software as
  distributed but says nothing about the *advice* framing a user sees in the UI;
  the in-app line is what a user actually reads before acting.
- **Only a README note.** Most users never read it. The disclaimer has to live on
  the advisor screen itself.
