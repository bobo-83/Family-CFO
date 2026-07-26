# 0069 — The RSU sell-by runway

Date: 2026-07-26
Status: Accepted

## Context

The Overview stacked three cash cards (30-day outlook, left-to-spend,
stress test), but the user's actual operating question was sharper: "do I
have enough cash to pay bills when due — and when must I sell RSUs to
close a gap? I need at least 4 business days heads up" (user, 2026-07-26).

## Decision

The 30-day cash outlook now derives the runway from its own projection:

- `first_shortfall_date` — the first day the projected balance goes
  negative (same-day outflows before inflows, so it errs early).
- `shortfall` — the DEEPEST projected gap, not the first crossing: selling
  only enough for the first dip would leave later payments uncovered.
- `sell_by_date` — 4 business days before the first shortfall
  (`RSU_SALE_NOTICE_BUSINESS_DAYS`): place the trade, settle, transfer.
  Weekends are skipped; market holidays are NOT modeled, so around a
  holiday the notice errs toward acting a day early, never late.

The sell-by is THE headline of the Overview's cash outlook card (iOS and
web) and a red row atop the watch glance. A covered horizon says so in one
green line. All fields are optional in the contract — absent while covered.

## Rejected options

- **A separate "runway" endpoint/card** — the outlook already computes the
  projection; a second source could disagree with it.
- **Configurable notice period** — 4 business days is the user's stated
  need; a setting can come later if it ever varies.
- **Netting the sale estimate for taxes** — RSU sale proceeds vs
  withholding is compensation-profile territory; the card states the CASH
  gap to raise and leaves sizing the sale to the advisor.

## Invariant

The sell-by date, the shortfall date, and the day-by-day projection come
from one computation; the card can never contradict the drill-down.
