# 0072 — Per-household encryption: key hierarchy and phased design

Date: 2026-08-03
Status: Accepted 2026-08-03 (implements issue #155; supersedes the deferral
in ADR 0070). Phase 1 shipped 2026-08-03 — see implementation note below.

## Context

ADR 0070 deferred cryptographic isolation because the current deployment is
one extended family on hardware they own. That trigger has now fired: the
operator plans to host households outside the family. Those households must
be sealed from each other, and — to the extent each household chooses — from
the box operator.

The deferral named a real logical conflict: the box's most valuable work runs
with nobody present (daily SimpleFIN sync, net-worth snapshots, scheduled
backups, idle-time AI study, report generation), and every advisor answer
reads raw rows server-side. A key those jobs can use unattended is a key the
operator holds. No design removes that conflict; this one makes it an
explicit, per-household choice instead of a hidden assumption.

## Decision (proposed)

A three-level envelope hierarchy, and two per-household operating modes.

### Key hierarchy

1. **Household data key (DEK)** — random 256-bit key per household. All of a
   household's sensitive rows and backup archives are encrypted under keys
   derived from it (HKDF with purpose labels: `rows`, `backup`, `search`).
   The DEK itself is never stored in plaintext.
2. **Wrapping keys** — the DEK is stored several times, each copy wrapped by
   a different key-encryption key (KEK):
   - **Per member**: KEK derived from the member's password (PBKDF2, same
     primitive as auth but a SEPARATE salt and purpose label — the auth hash
     must never double as a KEK). Password change re-wraps; it never
     re-encrypts data.
   - **Per paired device**: the DEK wrapped to the device's existing pairing
     public key (`paired_devices.public_key`), so the iPhone/Watch unwrap
     with their private key and never need the password. Device revocation
     deletes that wrap.
   - **Recovery key**: a one-time-displayed random key (same UX as the
     backup encryption key today). Households are told plainly: lose all
     passwords, devices, AND this key, and the data is gone — that is the
     guarantee working as designed.
   - **Box service key** (convenient mode only): the box's master key wraps
     the DEK so unattended jobs can run. Sealed mode omits this wrap.
3. **Box master key** — the existing Fernet credential-key pattern
   (`banksync`), promoted to a first-class keyring file outside the database
   and excluded from household backups.

### The two modes (per household, chosen at creation, changeable)

- **Convenient (default)**: DEK carries a box-service wrap. Everything works
  exactly as today — 6 a.m. syncs, snapshots, idle study, scheduled backups.
  Honest claim: *your data is sealed against database dumps, stolen disks,
  and other households' backups — not against the box operator.*
- **Sealed**: no box-service wrap. The DEK exists in server MEMORY only while
  a member session (or paired device session) is active; background work for
  that household queues and drains during the next active session ("sync on
  open"). Scheduled backups still run — the backup subkey is derived ahead
  under the DEK and held as an ENCRYPT-ONLY key (write new archives, cannot
  read rows), so off-box copies continue even when sealed. Honest claim:
  *the operator can read your data only while you are logged in and only by
  modifying the running software — not from disk, dumps, or backups.*

The invariant from ADR 0070 carries forward and sharpens: UI and docs must
state exactly the claim of the household's mode, and never more.

### What gets encrypted

Envelope-encrypt the CONTENT columns; leave ids, foreign keys, dates, and
household scoping plaintext for indexing and retention jobs:

- transactions: merchant, description, amount_minor
- accounts: name, institution
- chats/conversations, household memories, advisor feedback: full text
- documents and extractions: blobs and text
- income sources, bills, goals: names and amounts

Aggregation (net worth, budgets, income detection, advisor tools) is
computed at request time inside a session that holds the DEK — in sealed
mode that is the only time it CAN be computed, which is consistent: the
advisor also only answers while you're logged in. Dedup and vector indexing
run wherever sync runs (key present by construction). Amount-range queries
that today use SQL move to decrypt-then-filter in the service layer; on this
box's data volumes (tens of thousands of rows per household) that is
milliseconds, and the repository layer is already the single choke point
where decryption slots in.

### Rotation and lifecycle

- **Member removed** → generate a new DEK, re-encrypt the household's rows
  (background job during an admin member's session, old+new keys in memory),
  re-wrap for remaining members/devices. Removal is complete only when
  rotation finishes — the UI must say "removing…" until then.
- **Password change** → re-wrap only (seconds).
- **Device revoked** → delete its wrap; rotation optional (the device never
  held the DEK at rest — it held a wrap unusable without its private key).
- **Backups** → each archive encrypted under the household's backup subkey;
  the existing manifest (app_version, schema_revision) gains `key_id` so
  restore can say "this archive needs household X's key" instead of failing
  opaquely. The whole-box backup key stops being a skeleton key.

## Phasing (each phase ships alone and is useful alone)

1. **Phase 1 — per-household DEK, box-wrapped (convenient mode for all).**
   Keyring file, DEK table, envelope encryption of the column list above,
   per-household backup subkeys. No UX change, no feature loss. Protects
   dumps/disks/cross-household backup reads. The bulk of the plumbing.
2. **Phase 2 — member, device, and recovery wraps.** Login/pairing unwrap
   paths, re-wrap on password change, recovery-key UX, rotation on member
   removal. Box wrap still present — no feature loss yet.
3. **Phase 3 — sealed mode.** The toggle, the in-memory keyring with
   session-bound TTL, queued background work draining on session start,
   encrypt-only backup subkey, honest-claim UI copy for both modes.
4. **Phase 4 — hosting hardening (beyond #155).** Per-household resource
   quotas, rate limits, admin-surface audits — cryptography is necessary but
   not sufficient for hosting strangers.

## Rejected alternatives

- **Client-side-only encryption (server as blob store)** — kills server-side
  aggregation and the grounded advisor; a different product.
- **Full-disk encryption as the answer** — still recommended for the Sparks
  as baseline hygiene, but it protects against stolen hardware only and must
  never be described as household isolation (ADR 0070's invariant).
- **Per-household Postgres databases/schemas without encryption** — better
  blast-radius, same operator-readability; considered as a COMPLEMENT to
  Phase 1, not a substitute.
- **Standing key escrow for sealed households ("the box holds the key but
  promises not to look")** — that is convenient mode with dishonest copy.

## Consequences

- Sealed households trade background freshness for the guarantee: data syncs
  when someone opens the app, not at 6 a.m. This is the honest price and the
  design says it out loud.
- The repository layer grows a decrypt/encrypt seam and every aggregation
  inherits a session-keyring dependency; tests gain a fixed-key test keyring.
- Restore flows must handle "archive present, key absent" as a first-class
  state (message, not failure) — composes with the 0.127.0 version-manifest
  guard.
- Operator honesty becomes a product surface: mode claims appear in UI copy
  and docs, and are enforced by review against the ADR 0070 invariant.

## Implementation note — Phase 1 (shipped 2026-08-03)

- `household_keys` (migration 0076): per-household DEK, stored only wrapped
  by `FAMILY_CFO_MASTER_KEY` (environment, never the database). Content is
  encrypted under an HKDF-derived `rows` subkey; values carry an `enc1:`
  prefix and legacy plaintext reads through until
  `python -m family_cfo_api.tools.encrypt_existing` seals it.
- Columns sealed in Phase 1: conversation messages, recommendation answers,
  household memories, advisor feedback notes, document extraction text —
  the personal-content columns with no SQL-side filtering.
- **Correction to the design**: "per-household backup subkeys" assumed
  per-household archives, but backups are whole-database dumps. Column
  encryption achieves the intended property directly — the dump inside any
  backup now carries ciphertext for household content, so no backup key is
  a content skeleton key. Per-household archive keys return in Phase 2/3 if
  backups become per-household exports.
- Not yet sealed (needs the aggregation refactor): transaction
  merchants/descriptions/amounts, account names, bill/income/goal names.
  That is the remainder of Phase 1, tracked in issue #155.
