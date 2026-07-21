# ADR 0053: Filing a deposit under the Income category counts it as income

## Status

Accepted. Builds on M61–M63 (income analysis) and ADR 0049 (suspected income).

## Context

Income analysis counted only two things: **auto-detected recurring deposits**
and deposits with an explicit **"include" override** (the income page's "Add as
income"). A user categorizing a transaction as **Income** — via the Categorize
screen, or the suspected-income "confirm as income" flow (ADR 0049), which
recategorizes to the Income category — had **no effect** on the analysis.

On real data this hid real money: a household filed **$59,797 and $58,838** RSU
deposits (and others) under Income, but the analysis showed none of them —
annual income read **$46,969**. Worse, these large deposits often have a matching
outflow (a transfer into a brokerage leg), so the internal-transfer heuristic
actively *excluded* them, and nothing let the user's category decision override
that.

## Decision

**A positive inflow filed under the Income category counts as income**, treated
as an explicit "include" signal:

- `repository.income_categorized_ids(since=…)` returns the ids of Income-filed
  inflows in the window.
- `recurring_income_candidates` folds them into `included_ids`
  (`included_ids |= income_categorized_ids − excluded_ids`), so they (a) survive
  the internal-transfer filter and (b) are counted in the rollup and shown as a
  source — exactly as an "include" override already is.
- An explicit **"exclude" override still wins** — if the user later says "not
  income," that beats the category.

This also makes the suspected-income "confirm as income" action actually show up
in income (it recategorizes to Income), closing a gap in ADR 0049.

## Invariant

> A positive deposit the household has filed under the Income category is counted
> as income by the analysis (rollup and sources), overriding the internal-transfer
> heuristic and detection gaps — unless the user has an explicit "exclude" income
> override on it, which always wins.

## Rejected

- **Require an explicit include override in addition to the category**: that's
  the confusing status quo — the user already said "this is income" by
  categorizing it; asking for a second action is redundant.
- **Only bypass the transfer heuristic, but don't add to the rollup**: half a
  fix — it would appear in lists but not change the headline income the user
  came to see.
- **Count Income-categorized outflows too**: income is money in; a negative
  amount filed under Income is a data error, not income. Positive only.
