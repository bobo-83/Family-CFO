# ADR 0075: A balance outside the base currency is excluded from every total, and disclosed — never converted

## Status

Accepted (2026-09-06, issue #152). Relates to ADR 0003 (the engine stays
deterministic and offline) and ADR 0014 (exchange rates remain a conversational
tool). Refines the M122 / #130 rule that only base-currency arithmetic excludes a
foreign-currency account.

## Context

The app is single-currency per household: `households.base_currency` is the unit
of every total on the Overview and of every figure the advisor's tools return.
But the write side never enforced that. `POST /accounts` accepts any ISO code,
and bank sync creates accounts in whatever currency the provider reports — so a
mixed-currency household is one ordinary form submission, or one linked
institution, away.

The read side assumed it could not happen. `compute_net_worth_with_ref`,
`emergency_fund_inputs` and (through it) `compute_safe_to_spend` fed every
balance to `Money.__add__`, which raises `CurrencyMismatchError` on the first
mismatch. One EUR savings account in a USD household therefore:

- 500'd `GET /household`, the home screen on both clients, and took `GET /goals`
  and the Overview's top-goal card with it whenever an emergency-fund goal
  existed;
- failed the advisor's three most-asked questions (`get_net_worth`,
  `get_emergency_fund`, `get_safe_to_spend`), `project_purchase_impact`, and the
  deterministic fallback the chat turn drops to when the model is off — so the
  turn had no floor;
- stalled the daily net-worth snapshot pass for every household after the broken
  one, because the loop caught only `HouseholdLockedError`;
- and, worse than a crash, `_grounded_retirement_inputs` summed a EUR pension's
  raw minor units and labelled the sum in the base currency — a plausible wrong
  answer.

Everything else that sums (`reconstruct_net_worth`, `compute_debt_outlook`,
`_asset_and_debt_summary`, the rest of safe-to-spend) already filtered on
currency and, in `compute_debt_outlook`'s case, counted what it skipped as
`unmodeled`. The crash sites were the outliers, not the rule.

## Decision

1. **Every base-currency figure excludes an out-of-base balance and says so.**
   One helper, `finance_service.partition_balances_by_currency`, splits the
   household's balances into base-currency and foreign; every aggregating path
   uses it. The foreign half IS the disclosure: it reaches the household as a
   typed list of `{name, balance}` where `balance` is `Money` in the account's
   OWN currency, so nothing is ever shown as a base-currency figure.

2. **Never convert.** ADR 0003 makes every calculation deterministic and
   auditable; `get_exchange_rate` is an ADR 0014 live-data tool — optional, an
   outbound call to a third party, absent in tests and on any box without egress.
   Routing net worth through it would make the home screen depend on the
   internet and yesterday's snapshot non-reproducible. Conversion stays available
   to the *advisor* as a conversational tool: "your EUR savings is roughly this
   much at today's rate" is a fine sentence for the model to say, labelled
   approximate, and the wrong thing for the engine to bake into a total.

3. **Never refuse on write.** `POST`/`PATCH /accounts` keep accepting any ISO
   code. Refusing would leave every household that already has such an account
   broken, would force sync to drop a real account from a linked institution,
   and would contradict #130, which already treats a foreign account as real and
   first-class on the Accounts tab and in `get_accounts`.

4. **Disclosure is eligibility-specific per figure, and global on the
   Overview.** Currency equality is necessary but not sufficient for a total to
   include an account (net worth skips 401(k) loans; the emergency fund counts
   liquid or designated accounts; safe-to-spend touches cash, reservations and
   debts). Each calculation's `excluded_accounts` lists only the foreign accounts
   that WOULD have been components of that figure, so it never claims an account
   was left out of a total it was never part of. `HouseholdContext.
   accounts_outside_base_currency` is the global fact — every account outside the
   base currency — and is the same list the tools report, so web, iOS and the
   advisor tell one story from one source.

5. **The persisted warning is generic; names stay live.** The
   `financial_calculations` row records `excluded_account_count`, the excluded
   currencies and one sentence ("1 account held in EUR is not counted in this
   USD figure; …") — never an account name. Account names are sealed content
   (ADR 0072) while the calculation and recommendation `warnings_json` columns
   are plaintext, and the grounding guardrail treats a warning as app-authored
   text whose digits may ground a figure while it strips the digits out of a
   `name`. A warning naming "Fidelity Brokerage 9876" would both leak a sealed
   name and make `9876` quotable as money (the #130 lesson). Names and balances
   therefore travel only in the typed `excluded_accounts` / `accounts_outside_
   base_currency` fields, attached to the live result after the audit row is
   written.

6. **"Unknown" is null, not an empty list.** A past-month figure comes from a
   snapshot or a base-currency reconstruction with no record of what it left
   out, so `accounts_outside_base_currency` is `null` for a historical Overview
   and `excluded_accounts` is `null` for `get_net_worth(month=…)`. `[]` is
   reserved for "today's accounts are known and none is foreign".

7. **The snapshot pass survives any per-household failure.** `record_snapshot_
   once` catches `Exception` per household and logs it with the household id and
   a traceback; the returned count is of households actually captured. The
   currency fix removes today's trigger; the loop's fragility was its own bug.

## Invariant

No base-currency figure — on the Overview, in a tool payload, in a persisted
calculation, in a snapshot — ever includes, converts, or is labelled with a
balance held in another currency; and no such balance is ever silently dropped
from a figure that would otherwise have counted it. Enforced by the #152
regression tests across `finance_service`, `ai_tools`, `net_worth_history`, the
household, goals, chat and advisor endpoints, and by `Money.__add__` continuing
to raise on a mismatch.

## Consequences

- A mixed household sees a base-currency Overview plus a list of what it leaves
  out; the advisor can name the excluded account and quote its balance in its
  own currency, and is told never to add it back.
- Multi-currency households remain deferred (`docs/RELEASE-CHECKLIST.md`). This
  ADR makes the single-currency rule hold instead of crash; it does not reverse
  it. A future per-household FX table would supersede decision 2 in a new ADR.
- The `HouseholdContext` field is additive under contract `0.157`. The first
  client that *reads* it moves the contract to `0.158` (ADR 0074 rule 5), API
  side first.

## Rejected

- **Refuse foreign currencies on write only.** Leaves every existing mixed
  household broken; sync would keep creating them.
- **Convert via `get_exchange_rate`.** The home screen must not depend on
  egress, and totals must be reproducible (ADR 0003 / 0014).
- **Catch `CurrencyMismatchError` in an exception handler and return 409.** A
  tidier error, but the Overview still fails to render. The household needs a
  number and a disclosure, not a better error.
- **Name the excluded accounts in the warning string.** Rejected for the
  privacy and grounding reasons in decision 5.
- **A client-derived Overview note** from `listAccounts()` × `context.currency`.
  The API owns what a total includes; one server-authored list keeps the
  Overview, the phone and the advisor consistent.
