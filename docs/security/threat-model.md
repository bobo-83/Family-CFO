# Threat Model

## Assets

- Household financial data
- Account balances and transactions
- Financial documents
- Conversation history
- AI prompts and model responses
- Pairing credentials
- Session tokens
- Backup archives

## Threats

### Local Network Attacker

Risk: interception of app-to-server traffic or unauthorized pairing.

Controls:

- HTTPS
- Short-lived pairing codes
- Device approval and revocation
- Token rotation

### Compromised Device

Risk: unauthorized access from a stolen phone or desktop.

Controls:

- Face ID local unlock on iPhone
- Revocable device credentials
- Session expiration
- Role-based access

### Malicious or Leaky Logs

Risk: sensitive data copied into logs.

Controls:

- Structured logging with redaction
- No raw document logging
- No credential logging
- Prompt redaction policy

### Unsafe AI Output

Risk: fabricated financial facts or overconfident advice.

Controls:

- Deterministic calculation references
- Confidence and assumptions
- Missing-data disclosure
- No autonomous transactions or trades

### Backup Exposure

Risk: financial data leaked from backup archives.

Controls:

- Encrypted backups
- Restore testing
- Documented key handling

## Open Questions

- Exact database encryption approach.
- Local certificate provisioning strategy.
- Backup key recovery flow.
- Whether optional external AI providers will ever be supported through explicit opt-in.
