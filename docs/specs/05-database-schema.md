# Database Schema

Primary database: PostgreSQL.

The schema must protect precision, auditability, and privacy.

## Initial Tables

- households
- users
- household_memberships
- auth_sessions
- pairing_sessions
- accounts
- account_balances
- transactions
- transaction_categories
- bills
- income_sources
- goals
- scenarios
- financial_calculations
- recommendations
- conversations
- conversation_messages
- imports
- import_files
- documents
- document_extractions
- reports
- ai_runtime_configs
- audit_events
- backup_jobs

## Money Storage

Use:

- `amount_minor` as signed integer
- `currency` as ISO 4217 code

Do not persist financial amounts as floating point.

## Encryption Requirements

The schema design must support encryption for sensitive fields and encrypted backups. Final encryption implementation is defined by the security model.

## Audit Requirements

Persist enough information to explain:

- Which inputs were used
- Which calculation version ran
- Which assumptions were applied
- Which model and prompt version produced explanation text

## Migration Rules

- All schema changes use migrations.
- Migrations must be reversible where practical.
- Never include production data.
- Fixtures must be synthetic.
