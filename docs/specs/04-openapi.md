# OpenAPI

The backend API is the source of truth. SwiftUI and Angular clients generate clients from the same OpenAPI contract.

Initial contract: `shared/openapi/family-cfo.v1.yaml`.

## Design Rules

- Version the API under `/api/v1`.
- Return structured errors.
- Use stable IDs.
- Use integer minor units for money.
- Use explicit currency.
- Keep LLM responses structured enough for UI rendering.
- Avoid duplicated DTO definitions in app clients.

## Initial Endpoint Groups

- Health
- Pairing
- Authentication session
- Household context
- Accounts
- Transactions
- Bills
- Income
- Goals
- Purchase advisor
- Chat
- Reports
- Imports
- Documents
- AI runtime configuration

## Error Shape

```json
{
  "error": {
    "code": "string",
    "message": "string",
    "details": {}
  }
}
```

## Recommendation Shape

Recommendations must include:

- Answer
- Assumptions
- Impacts
- Tradeoffs
- Alternatives
- Confidence
- Calculation references

## Qualified Aggregate Contract (M124, ADR 0076)

The coordinated contract release adds:

```text
QualifiedMoney
  value: Money                 required
  incomplete_count: integer    required, minimum 0

ComputationAvailability
  status: complete | unavailable
  incomplete_count: integer    required, minimum 0
```

`complete` requires count 0 and `unavailable` requires a positive count.
`QualifiedMoney.value` is never null and never contains a synthetic substitute.
A positive count means the value sums readable contributors only. A nullable
decision uses `QualifiedMoney | null`; every non-null decision is complete.
Ordinary absence versus corruption-driven unavailability is distinguished by a
colocated availability field where both would otherwise serialize as null.

The field matrix is normative in ADR 0076 and the M124 task section. It includes
qualified descriptive totals across household context, spending/categories,
budgets, cash flow, savings, timeline totals, outlook/plan components, income,
and yearly overview; nullable derived net/remaining/coverage/rate/projection/
safe-to-spend/tax values; `unknown` or `unavailable` decision enums; nullable
rankings; `SavingsContributionSet`; server-authored
`SpendingByCategory.total`; and required `BudgetListResponse.summary`.
Existing unaffected stored values remain `Money`.

Transaction list/detail, the card-statement list and card-statement
mutation/undo pre-reads, raw exports/indexing, equality/dedupe/sign/range
filters, write comparisons, review
generation, and unstable candidate/ranking-only operations document HTTP 409
`sealed_amount_unreadable`. Aggregate-local qualification does not catch
`household_locked`; HTTP 423 remains distinct.

The contract specifies exact null/zero/empty semantics: partial zero is
`value.amount_minor == 0` with a positive count; no contributors is zero/count
0; unavailable decisions are null rather than zero; unavailable rankings are
null rather than an exact empty list; counts exclude ADR 0075 foreign-currency
exclusions.

## Client Generation

Generated clients are derived artifacts. The OpenAPI contract is edited first, then clients are regenerated.

M124 is an intentional breaking shape change. It moves contract `0.158` to
expected `0.159`, or the next unused minor if `VERSION` advances first. FastAPI
schemas, authoritative OpenAPI, recursive parity validation (`$ref`, requiredness,
nullability, array items, and enums), immutable compatibility fixture, generated
web and Swift clients, API behavior, `VERSION`, and all component `BUILD` values
must land atomically under ADR 0074. Mixed old/new artifacts are unsupported;
rollout and rollback are coordinated. Item 1 changes documentation only; later
items own all contract, version, and generated-client files.
