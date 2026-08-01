# ADR 0070: Grant-based RSUs valued at a live quote (M-rsu-grants)

## Status

Accepted.

## Context

The compensation profile modeled RSUs as a flat dollar figure per year with a
vest cadence ("$100k/yr, quarterly"). The user's reality: a fixed number of
XYZ units granted every two years, vesting quarterly, worth whatever the
stock is worth. The flat figure can't answer "how many shares vest when," and
its dollar value goes stale the moment the stock moves — which also weakens
the RSU sell-by runway (ADR 0069) it feeds.

## Decisions

1. **Grants are the source of truth; the schedule is derived, then owned by
   the user.** A grant row is (ticker, units, grant date, vest years, cadence).
   Creating one derives equal whole-share tranches (remainder front-loaded)
   into `rsu_vest_events` — ordinary editable rows, not a projection. Undoing
   a grant deletion restores the schedule *as edited*, not re-derived.
2. **One live quote per ticker, cached in `stock_quotes`.** Fetched from a
   keyless JSON endpoint (Yahoo's chart API; stooq's CSV API is defunct —
   verified 404) with a 6s timeout, refreshed on every
   pull-to-sync, on grant creation, and on demand — never fetched inline by a
   read path. A quote is an enhancement, not a dependency: every fetch failure
   degrades to the cached (or absent) value.
3. **The valuation substitutes at one seam.** `rsu_service.
   effective_income_profiles` returns income profiles with `rsu_annual_minor`
   replaced by the next 12 months of vests (inclusive horizon) at the cached
   quote. Taxes, the W-2 baseline, expected income events, and the runway all
   swapped one loader call; no consumer grew RSU-specific logic. Households
   without grants — or without a quote yet — keep the flat figure untouched.
4. **The runway speaks in shares.** With grants and a quote, the ADR 0069
   sell-by card adds `sell_units` = ceil(deepest shortfall ÷ share price):
   "Sell ≈ 12 XYZ by Aug 4." Same computation, more actionable unit.

## Rejected

- **Fetching quotes inline in read endpoints** — a market-data hiccup must
  never break the Overview; reads only ever see the cache.
- **Fractional share units** — whole shares keep derivation exact and honest;
  the editable schedule absorbs any plan-specific rounding.
- **Pulling vesting schedules from SimpleFIN** — the protocol carries
  accounts/balances/transactions only; future grants simply aren't in the data.
- **Keeping the flat figure authoritative with a "live adjustment"** — two
  competing RSU numbers on one screen is how trust dies; substitution at one
  seam keeps a single figure everywhere (ADR 0025 spirit).

## Invariant

> Every RSU dollar shown anywhere traces to (vest events × cached quote) when
> grants exist, and to the declared flat figure otherwise — never a blend.
