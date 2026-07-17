# ADR 0026: Overview redesign — the cash outlook, and safe-to-spend reframed as a stress test (M112)

## Status

Accepted. Extends ADR 0024 (payment timeline) and ADR 0003 (deterministic
finance). Shipped on iOS and the Angular dashboard together (ADR 0025).

## Context

After the Bills redesign, the two screens contradicted each other: Bills said
"$8,254 due in 14 days — covered" while the Overview's headline said
"−$12,797 free to spend right now." Both numbers were correct. Safe-to-spend
subtracts the emergency fund, a 30-day bill horizon, monthly debt payments, and
the **full credit-card balances**, while counting **no incoming paychecks** — a
worst-case solvency snapshot presented as a spending allowance. The user:
"I feel like there is a disconnect."

## Decisions

1. **The Overview leads with the lived cash picture, not the worst case.** A
   new **Cash outlook** card shows (a) the *same* due-vs-cash verdict as the
   Bills tab — same figures, same words (ADR 0025 vocabulary parity applies
   within the app too), and (b) a **30-day projection**: cash today + expected
   paychecks − expected payments, headlined by the **lowest point** the balance
   reaches and when. A drill-down lists every event with its running balance.
2. **Paydays are inferred, deterministically, from history.** The income
   analysis's detection pipeline (M61–M63: transfer exclusion, user overrides,
   recurring grouping) is now shared (`recurring_income_candidates`) and its
   candidates are stepped forward from their last sighting by their cadence.
   The same engine that names your income sources predicts your paydays — no
   new configuration asked of the user (minimize duplicate input).
3. **Payments come from the payment timeline** (ADR 0024), projected across the
   window: bills recur by their stored frequency (a weekly bill lands ~4 times
   in 30 days); loan/lease payments recur monthly; a credit card contributes
   **exactly one** payment (today's balance on its inferred due day) — the
   statement after next is unknowable and is never guessed. Future occurrences
   anchor on the item's own due-date cadence; an overdue charge claimed "today"
   does not shift the schedule. Same-day outflows apply **before** inflows, so
   the lowest point errs low.
4. **Safe-to-spend stays, renamed to what it is: a stress test.** The card and
   detail screen now say: "if every commitment were called today — full card
   balances, all bills, the emergency fund held back — with no paycheck
   counted." A negative stress test beside a green outlook is no longer a
   contradiction; it's cushion-if-everything-goes-wrong beside
   how-the-month-actually-plays-out. The calculation itself is unchanged.
5. **One read-only endpoint** — `GET /overview/cash-outlook` (`getCashOutlook`)
   — feeding both clients. No new mutations (nothing to undo, ADR 0023).

## Invariant

> The Overview and Bills screens never disagree: the due-vs-cash verdict is the
> same figures from the same computation. The outlook's every event is traceable
> to a real recurring pattern (a detected income source or a timeline item) —
> never a guess; a card's unknowable next-next statement is omitted, not
> estimated. The stress test is labeled as a stress test, never as spending
> allowance.

## Rejected

- **Make safe-to-spend income-aware** — mixing horizons in one number destroys
  its meaning as the conservative floor; two clearly-named numbers beat one
  muddled one.
- **Hide safe-to-spend** — the worst-case cushion is genuinely useful; the
  problem was framing, not existence.
- **A month-scoped "left to spend" budget number (Simplifi-style)** — needs a
  spending-plan concept (planned vs actual by category) that Family-CFO models
  via Budgets; a possible future layer, not this change.
- **Asking users to enter paydays** — recurring-income detection already knows
  them; a thin history simply yields no projection (honest degradation).
