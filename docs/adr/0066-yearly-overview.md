# 0066 — The Overview's Year mode: trend chart + grounded year review

Date: 2026-07-24
Status: Accepted

## Context

The Overview answered "how is this month going?" but the user asked for the
year: a monthly trend they can drill into, plus a summary of what has been
going on and what could be improved.

## Decision

- `GET /overview/yearly?year=` aggregates per-month income, spending, net,
  and month-end net worth (reconstructed from today's balances minus later
  transactions — same approximation the debt history uses), plus year totals
  and the year's top spending categories.
- The narrative comes from `POST /overview/yearly/review`: the household's
  own runtime writes a summary + up to four suggestions **from a fact sheet
  of pre-formatted figures**, and the finished text is validated against
  those numbers exactly like chat answers (ADR 0009). A failed validation —
  or no runtime — stores a deterministic summary instead; the cached row
  (`yearly_reviews`, migration 0067) never contains an ungrounded number.
  Generation is on-demand (a visible "Write it"/"Refresh" button), never a
  hidden per-request model round.
- iOS: Overview gains a Month/Year segmented mode; Year shows a Swift Charts
  income-vs-spending chart where tapping a month jumps the whole Overview to
  that month (the existing month navigation is the drill-down). Web: same
  mode toggle with a hand-rolled bar chart (house style — no chart library)
  and an inline month focus strip.

## Rejected options

- **Streaming/generating the review on every page load** — a model round per
  visit for text that changes monthly; cache + explicit refresh matches how
  often the answer actually changes.
- **Letting the model compute year totals** — ADR 0003/0009: the engine
  computes, the model narrates. The fact sheet hands it every figure it may
  quote.

## Invariant

The year review shown to the user is either validated against the year's
computed figures or is itself computed. There is no third path.
