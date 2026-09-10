# Domain Model

## Core Concepts

### Household

A household owns financial context, users, accounts, goals, reports, and settings.

A household has ONE base currency (ADR 0075, #152). Every total the app computes
for it — net worth, the emergency fund, safe-to-spend, a purchase's impact, a
retirement projection's grounded savings — is in that currency and only that
currency. Multi-currency households remain deferred; this rule holds instead of
crashing. Currency codes are canonical upper-case ISO 4217 wherever they are
stored or compared; "usd" and "USD" are the same currency, never two.

### User

A person who can authenticate and access the household according to a role.

Roles:

- Owner
- Adult
- Viewer
- Child profile

### Account

An account represents a financial container.

An account carries its own currency, which may differ from the household's base
currency (`POST /accounts` accepts any ISO code, and bank sync creates accounts
in whatever currency the provider reports). Such an account is real: it is
listed on the Accounts tab and by the advisor's `get_accounts`, with its balance
in its own currency. It is never summed into a base-currency total, never
converted, and never silently dropped — each total that would otherwise have
counted it discloses it (`excluded_accounts` in a tool payload, the generic
warning on a calculation, `accounts_outside_base_currency` on the Overview), and
an emergency-fund designation on it is ignored and said to be ignored.

Types:

- Checking
- Savings
- Credit card
- Brokerage
- Retirement
- HSA
- 529
- Mortgage
- Auto loan
- Student loan
- Real estate
- Other asset
- Other liability

### Transaction

A dated money movement with amount, currency, account, merchant, category, import source, and review state.

### Bill

A recurring obligation such as mortgage, utilities, insurance, subscriptions, phone, internet, or childcare.

### Income Source

Income includes salary, bonus, RSUs, stock options, side income, and other recurring or expected inflows.

### Goal

A target with a purpose, amount, date, priority, and funding source.

A goal is declared in a currency and shown in that currency. One declared outside
the household's base currency is never relabelled: it is not the emergency
fund's target, purchase impact skips it and says so, and only a base-currency
emergency-fund goal tracks the live designated fund (ADR 0075).

Examples:

- Emergency fund
- Vacation
- Retirement
- College
- Vehicle
- Renovation

### Scenario

A what-if calculation using current context plus user-provided changes.

Examples:

- Buy a laptop
- Take a vacation
- Refinance a mortgage
- Retire at 55
- Accelerate debt payoff

### Recommendation

An explainable answer grounded in financial engine outputs and optionally expanded by the reasoning model.

## Money Rules

- Store amounts as integer minor units plus currency.
- Do not use floating point for persisted money.
- Preserve original imported values.
- Track assumptions used for projections.

## Auditability

Financial engine outputs must include:

- Input references
- Calculation version
- Assumptions
- Warnings
- Output values

## Qualified Amounts and Availability (ADR 0076)

A read-side monetary aggregate distinguishes a descriptive leaf from a decision:

- `QualifiedMoney` is a wire value containing `value: Money` and
  `incomplete_count`. Its value is the sum of readable contributors only; the
  count is the number of distinct relevant unreadable stored amount cells.
- An internal unreadable source is the immutable, request-local identity
  `(household_id, table, row_id, column)`. Composite fields union source sets and
  derive a count; they never add child counts.
- An internal amount candidate retains safe non-amount metadata with an optional
  amount until eligibility, attribution, matching, grouping, and detection are
  resolved. It is not a zero-valued transaction or statement and never crosses
  the wire.
- `ComputationAvailability` distinguishes a complete non-additive computation
  from one whose membership, cadence, median, pairing, order, or projection can
  change because of an unreadable candidate.

Non-amount predicates are applied before decode when possible. An unreadable
cell counts only if it can affect the field; unknown sign is conservatively
relevant. The same source counts once within a composed field, while independent
fields each disclose their own relevant omission. A decision is evaluated only
when the union of every dependency it uses is empty. Otherwise its money value
is null or its non-money state is `unavailable`/`unknown`, while independent
qualified leaves remain visible.

`CategorySpendingTotals` is one authoritative scan containing qualified
per-category buckets, categorized total, uncategorized total, and overall total;
uncategorized is never derived by subtraction. Card aggregates choose the newest
eligible statement using non-amount metadata before decoding only the balance
they use. A corrupt unused minimum does not qualify a balance-only aggregate,
and an unreadable newest balance never falls through to an older statement or
running balance.

These identities and candidates are transient application concepts, not stored
entities. Existing strict raw records remain strict and are never populated with
placeholder amounts.

## Initial Aggregate Boundaries

- Household
- Financial Account
- Transaction Import
- Goal
- Scenario
- Report
- Conversation
- AI Runtime Configuration
