# 0068 — Interactive Year chart: tap to see a month, advisor explains it

Date: 2026-07-25
Status: Accepted

## Context

The Overview's Year chart (ADR 0066) drew each month's income and spending,
but a tap jumped straight into that month's full Overview — the user never
saw the bar's actual value, and there was no way to ask *why* a bar is tall
("what made up the income or the expenses?", user request 2026-07-25).

## Decision

1. **A chart tap selects; it never navigates.** Tapping a bar (iOS) or a
   month column (web) shows that month's exact figures — in, out, kept, and
   end-of-month net worth — in a card/strip beside the chart. Tapping the
   same month again clears the selection. Navigation to the month's full
   Overview is an explicit, labeled action ("Open month", iOS; the web
   Overview has no historical month mode, so no equivalent there).

2. **"Explain" hands the question to the advisor — no new endpoint.** The
   selection card offers "Explain income" / "Explain spending". These open a
   new advisor chat with a grounded question pre-asked ("What made up my
   income in April 2026? …"). The advisor's existing month-scoped tools
   (`get_income_and_tax`, `get_spending_by_category`,
   `get_spending_insights`) supply the real numbers, and the user can keep
   drilling with follow-ups — something a one-shot summary card cannot do.
   - iOS: `AppModel.askAdvisor(_:)` switches to the Advisor tab and opens a
     new chat with the question queued (the same `queuedMessage` path the
     receipt capture uses).
   - Web: the Overview navigates to `/chat?ask=…`; the chat page auto-sends
     the question and strips the param so a refresh doesn't ask twice.
   - Both clients ask with the **same wording**, so the advisor behaves
     identically (ADR 0025).

## Rejected options

- **A dedicated month-composition endpoint** (fact-sheet + guardrailed
  narrative like the year review, cached per month): duplicates what the
  advisor pipeline already does with its month tools, adds contract surface
  and a second grounding path to keep honest, and ends at a dead-end card —
  no follow-up questions.
- **Keep tap-to-navigate and add a long-press for values**: undiscoverable,
  and the primary gesture would still throw the user out of the chart they
  were reading.
- **Inline the explanation into the Year payload**: makes `getYearlyOverview`
  slow and couples a cheap read to LLM latency.

## Invariant

A tap on a chart datum shows information in place; leaving the screen (or
starting an LLM turn) always requires a separately labeled action.

## Platform exceptions (ADR 0025)

- watchOS (amended 2026-07-25, same day: "I still can't tap my watch to see
  the per month details"): the watch Year page selects months by tap exactly
  like the phone, showing that month's in/out/kept. Only the advisor-handoff
  buttons stay off the watch — the watch's chat page with its conversation
  loop is one swipe away and covers "why" questions.
- Web: no "Open month" action, because the web Overview's month mode has no
  historical month navigation to open (pre-existing platform difference).
