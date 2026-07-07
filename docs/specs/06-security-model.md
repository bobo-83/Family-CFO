# Security Model

Family CFO is designed for local self-hosted use with sensitive household financial data.

## Data Classification

### Restricted

- Credentials
- Session secrets
- Pairing secrets
- Financial documents
- Bank statements
- Tax documents
- Investment data
- Payroll data

### Sensitive

- Transactions
- Account balances
- Goals
- Bills
- Reports
- AI conversation history

### Internal

- Runtime configuration
- Model configuration
- Audit events

### Public

- Documentation
- Open source code
- Synthetic test fixtures

## Required Controls

- HTTPS for app-to-server communication.
- Secure pairing for mobile clients.
- Local authentication.
- Role-based users.
- Encrypted backups.
- Secret redaction in logs.
- No telemetry.
- No cloud AI calls for sensitive data unless a future explicit opt-in ADR permits it.

## Mobile Authentication

The iPhone app should use Face ID where available for local unlock. Server authorization remains token-based and revocable.

## Pairing

Initial pairing should use a short-lived QR code from the home server. Pairing creates a device record and scoped credentials.

## Logging

Logs must avoid sensitive raw financial data, credentials, document contents, and model prompts containing household details.

## Threat Model

See `docs/security/threat-model.md`.
