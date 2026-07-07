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

## Client Generation

Generated clients are derived artifacts. The OpenAPI contract is edited first, then clients are regenerated.
