# ADR 0015: Institution connections via SimpleFIN; transaction dedupe (M27)

## Status

Accepted.

## Context

Users want statements pulled from their financial institutions automatically,
and repeated imports (re-syncs, overlapping CSV exports) must not create
duplicate transactions. The privacy stance (README/ADR 0008) forbids designs
that *require* routing bank credentials through a third-party cloud.

## Decisions

### 1. Connector seam; SimpleFIN first

A `BankConnector` protocol (list accounts, fetch transactions since a date)
keeps providers pluggable. The first implementation is **SimpleFIN** — the
protocol built for self-hosted finance apps:

- The user connects their bank at SimpleFIN Bridge and pastes a one-time
  **setup token** into Family CFO; we exchange it for a read-only **access
  URL**. Bank credentials never touch this server.
- Opt-in per household, mirroring the external-AI precedent (ADR 0008): data
  flows *in* (read-only accounts/transactions); nothing about the household
  flows out beyond the token exchange and authenticated GETs.

Rejected for v1: Plaid/Yodlee-style aggregators (credentials through a vendor
cloud; keys + contracts — conflicts with the privacy-first default). **OFX
DirectConnect** (no third party at all) is the preferred second connector and
stays on the backlog — the seam exists for it; bank-by-bank quirks make it a
milestone of its own.

### 2. The access URL is a credential and is encrypted at rest

`institution_connections.access_url` is Fernet-encrypted with the existing
`FAMILY_CFO_BACKUP_ENCRYPTION_KEY` (one operator secret to manage; documented).
No connection can be created until that key is set. Access URLs never appear in
API responses or logs.

### 3. Two-tier dedupe

- **Provider id (hard):** `transactions.external_id` + a unique index on
  `(account_id, external_id)` makes provider-sourced imports idempotent —
  re-syncing any window can never duplicate a row. Two identical coffees on the
  same day survive (different provider ids).
- **Content hash (soft, fallback):** `transactions.import_hash` =
  SHA-256(account | date | amount_minor | normalized payee). Used when no
  provider id exists (CSV): an incoming row whose hash matches an existing
  transaction in the same account is skipped and counted as a duplicate.
  Documented tradeoff: identical same-day/same-amount/same-payee rows in a CSV
  are treated as one (standard behavior in Firefly/Actual); the workaround is
  editing the description before re-import.

Dedupe applies to both the new sync flow and the existing M7 CSV pipeline
(closing a real gap: re-uploading the same CSV used to duplicate everything).
Sync and import responses report `imported` vs `duplicates_skipped` so the
behavior is visible, never silent.

## Consequences

- New tables (`institution_connections`, `connection_accounts`) and additive
  `transactions` columns/indexes; migrations as usual.
- Accounts named by the provider are auto-created on first sync and mapped via
  `connection_accounts`; users rename them like any account.
- SimpleFIN Bridge is a paid service operated by a third party; deployments
  that reject any third party wait for the OFX connector. The app works fully
  without any connection (CSV/manual unchanged).
