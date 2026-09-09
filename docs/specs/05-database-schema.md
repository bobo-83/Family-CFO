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

## Qualified Aggregate Persistence (M124, ADR 0076)

M124 requires **no SQL migration**. Unreadable source identities and candidate
metadata are immutable, request-local application values and are never stored.
Repair changes no schema: the next read recomputes the aggregate and naturally
returns count 0 when every selected cell is readable.

Where a service already persists a `financial_calculations` record, complete and
incomplete work pass through one application-owned attempt boundary. An
incomplete attempt writes exactly one existing audit row with an application
schema version (for example `qualified-attempt/1`), `engine_invoked=false`, the
union-derived `incomplete_amount_count`, qualified component leaves, null
unsafe decisions, and a generic warning. It must not claim the deterministic
engine ran or change the engine version unless engine behavior changes.
Non-calculation aggregate endpoints gain no audit rows.

`inputs_json`, `outputs_json`, assumptions, and warnings never store unreadable
source row IDs, table/column names, ciphertext, sealed names, neighboring sealed
text, or guessed values. Cancellation before the persistence commit may leave
no row; after commit the single valid row remains. A retry writes a new
self-contained attempt and never accumulates counts across requests. Existing
schemaless calculation JSON is sufficient, and cached yearly reviews remain
stored but are suppressed while current dependencies are incomplete.

## Migration Rules

- All schema changes use migrations.
- Migrations must be reversible where practical.
- Never include production data.
- Fixtures must be synthetic.
