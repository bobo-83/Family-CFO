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
- Guardrail validation of generated explanations: any numeric claim not traceable to the calculation's own outputs causes the system to fall back to the deterministic explanation rather than surface the unvalidated model response (implemented M4, `services/ai-orchestrator/src/family_cfo_ai_orchestrator/guardrails.py`)
- Raw prompts and raw model completions are never logged or persisted; only the guardrail-validated explanation text, model version, and prompt version are stored

### Backup Exposure

Risk: financial data leaked from backup archives.

Controls:

- Encrypted backups
- Restore testing
- Documented key handling

## Resolved Decisions

The four questions that were open through M0–M12 are resolved in
[ADR 0008: Security Hardening Decisions](../adr/0008-security-hardening-decisions.md):

- **Database encryption at rest** — host/volume responsibility (encrypted disk), not app-layer column encryption; portable backups are separately encrypted at the app layer (M8).
- **Local certificate provisioning** — the web container self-signs on first start; operators bring their own cert (mount `/etc/nginx/certs`) or front the stack with an external TLS proxy. No built-in ACME by default (M13).
- **Backup key recovery** — operator-managed, no recovery by design; losing the key loses the backups. Documented as intentional.
- **External AI providers** — local-only default; cloud/external AI is unsupported and can only be enabled through a future explicit opt-in ADR that supersedes ADR 0008.
