# ADR 0047: The advisor finds savings waste-first, protecting what the family values

## Status

Accepted.

## Context

The advisor could report spending but had no notion of *where to cut*. A member
asked what a seasoned budgeting app looks for and whether it should read their
transactions to learn what they enjoy and suggest trimming non-essentials. The
right answer is yes — but the craft is cutting **waste before joy**: a nagging
app that tells you to give up the hobby you love gets deleted; a good coach finds
the forgotten $15 subscription first and ties every suggestion to your goals.

The app already had the raw materials (spending by category, recurring-charge
detection, goals, and the study job's insights about what the family enjoys) but
no tool that combined them into savings opportunities, and no needs/wants split.

## Decision

**Add a `find_savings` advisor tool (chosen approach: waste-first, protect what
you enjoy) plus a needs/wants taxonomy, and grounding rules that make cuts
tasteful and goal-linked.**

`savings.find_savings` computes, over the last 3 complete months:

- **Needs vs. wants split** — categories are classified essential/discretionary
  by a default keyword taxonomy (`classify_category`); returns essential and
  discretionary monthly totals.
- **Discretionary spend ranked** largest-first — the honest "biggest wants."
- **Subscriptions** — small (≤ $100) recurring charges from the existing bill
  detector: the subscription-sized leakage to review for "still using this?".
- **Possible waste** — duplicate streaming/music services, and discretionary
  categories whose latest complete month crept ≥30% above their trailing average.
- **Valued activities** — the study job's insights, so the advisor knows what to
  PROTECT (your tennis, your family dinners).
- **Goals** — open goals with their gap, so every suggested trim is tied to one.

Grounding rules route "where can I cut / how do I save" to `find_savings` and
require: cut waste first, then the largest discretionary — but never suggest
cutting a `valued_activities` item, tie each trim to a goal, offer options, never
moralize.

## Refinement (post-launch, same feature)

Probing real data showed two problems the launch version had: recurring charges
that were actually loan/bill payments got called "subscriptions", and lumpy
one-off purchases (a renovation, a trip) inflated both the discretionary ranking
and the "creep" flags. So:

- Subscriptions exclude any recurring charge whose merchant matches a liability
  account or a bill (substring both ways — "Department of Education" ↔ "U.S.
  Department of Education").
- Creep flags only a **moderate** rise (1.3×–2×) over a real baseline, and never
  an activity in `valued_activities`.
- Spending is split into **recurring habits** (spread across months — where trims
  stick) and **one-off purchases** (concentrated in a single month — already
  spent). The advisor trims recurring, and never asks the user to "cut" a one-off.

## Invariant

> A savings suggestion cuts waste (duplicate/forgotten subscriptions, fees,
> category creep) before discretionary lifestyle, never proposes cutting an
> activity the household clearly values, and ties each trim to a goal. Needs are
> never on the chopping block. The advisor presents options; the family decides.

## Rejected

- **Strict 50/30/20 coaching** (cut wants to hit a ratio): more directive but
  impersonal — it would put the member's tennis on the cut list. The family chose
  waste-first-protect-what-you-enjoy.
- **Waste-only (never touch lifestyle)**: safest but leaves the biggest levers
  untouched; the chosen approach still surfaces the largest discretionary, framed
  as options, while protecting valued activities.
- **A user-maintained essential/discretionary tag per category**: more precise
  but more input; the keyword taxonomy is a sensible default. A per-category
  override can come later if the default proves wrong for a household.
- **Fine-tuning "what they value" into the model**: ADR 0040 stands — the study
  job already distills valued activities into retrievable, deletable insights.
