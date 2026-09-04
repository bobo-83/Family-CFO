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
- **Sealed**: no box-service wrap. A member, paired device, or recovery unlock
  puts the DEK in server memory until a separate 30-minute sliding key-session
  TTL expires. Member-driven reads extend it; background work and auth-session
  lifetime do not. Background work runs only while that key session is open.
  Scheduled backups still run — the backup subkey is derived ahead
  under the DEK and held as an ENCRYPT-ONLY key (write new archives, cannot
  read rows), so off-box copies continue even when sealed. Honest claim:
  *the operator can read your data only while an in-memory key session is open
  and only by modifying the running software — not from disk, dumps, or backups.*

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

## Implementation note — Phase 1 batch 2 (shipped 2026-08-04)

- Sealed: transaction merchants/descriptions/notes, account names and
  institutions, bill/income/goal names, audit summaries and undo tokens
  (they name entities and snapshot values), and report narratives.
  SQL that grouped or ordered by those columns moved to
  decrypt-then-compute in the service layer (merchant rankings, name
  sorts, goal tiebreaks).
- **Deviation resolved 2026-08-04 (#184): transaction amounts are now
  sealed too.** Every SQL aggregation (sums, sign rules, dedupe
  equality, merchant rankings, duplicate flagging) moved to
  decrypt-then-compute at the repository seam; measured cost 3.4 µs per
  amount with a per-process token cache, ~5 ms at current volumes.
  Migration 0080 turns amount_minor into sealed Text; legacy digit
  strings read through until the sealer runs.
- Known plaintext residuals, accepted for now: report `summary_json` and
  extraction `structured_fields_json` (JSON columns), bill-suggestion
  dismissal merchant keys and transaction `import_hash` (both plaintext-
  derived fingerprints), category/role/device names, and the RSU grant
  ticker/schedule tables.
- Verification pattern worth keeping: the full test suite runs green
  twice — once with no master key (legacy passthrough) and once with
  `FAMILY_CFO_MASTER_KEY` forced, which makes every existing test
  exercise the decrypt seams.

## Implementation note — Phase 3 (shipped 2026-08-04)

- `households.sealed_mode` + nullable `household_keys.wrapped_dek`
  (migration 0079): sealed = no box wrap. The DEK lives in an in-memory
  session keyring with a 30-minute sliding TTL, opened by a proven member
  password (login), a device key-session (the phone unwraps its ECIES wrap
  locally and posts the key), or — operationally — the recovery key.
  Foreground member reads extend that TTL; background reads do not. The auth
  session and key session have separate lifetimes: signing out revokes the
  bearer token but does not erase the key immediately.
- A stored canary (rows-subkey ciphertext of a fixed plaintext) validates
  every posted or unwrapped key before it is trusted, so a wrong key can
  never silently poison new writes.
- Sealing preconditions: at least one member wrap AND a recovery key. The
  sealing session keeps its key; a box restart locks the household until
  the next sign-in. Locked reads/writes surface as HTTP 423
  (`household_locked`); worker jobs skip locked households instead of
  crashing the tick for everyone else (skip, not defer — see below). Whole-box backups keep running — the dump carries
  the sealed household's ciphertext, which is the point.
- Deviation from the sketch: no durable background-work queue — every
  worker job already polls on minutes-scale intervals, so "queue and drain
  on next session" was taken to reduce to "the next poll tick after an
  unlock runs the work".

  **That inference was wrong, and #115 is its consequence.** The unlock and
  the poll tick happen in *different processes*: a sign-in opens the session
  keyring in the API container's memory, while the jobs ran in the worker
  container, which has its own address space and can never observe it. So the
  next tick after an unlock was locked exactly like the one before it, and a
  sealed household got no unattended work at all — not delayed, never.

  **Resolved by moving the work to the key** (#115, `sealed_worker.py`). The
  API runs the jobs for the sealed households it currently holds keys for; the
  worker is handed the households it can open on its own and no longer
  attempts sealed ones. Ownership is disjoint by construction, so nothing runs
  twice and nothing is dropped:

  | process | households | opened by |
  | --- | --- | --- |
  | worker | not sealed | the box wrap |
  | API | sealed, unlocked in this process | the session keyring |

  A pass runs when a household unlocks — a member signing in, a device
  posting its key, the recovery key — via an unlock listener on the keyring,
  and on a periodic tick for work that comes due mid-session. The
  implementation deliberately does NOT use this ADR's own word "drain": a
  drain empties a queue that filled while you were away, and there is no
  queue — these are the worker's pollers relocated, each running what is due
  now. Sealed mode still costs something, and the honest form of it is
  narrower than "waits": cadence-gated jobs self-heal, but a net-worth
  snapshot is stamped for the day it runs, so days when nobody signs in stay
  missing from a sealed household's trend. Encrypted backups were never
  affected: they copy ciphertext under the box-level backup key.

  The load moves with the work, and it is CPU, not GPU: embedding is a small
  CPU-only ONNX model (ADR 0017) and study is an HTTP call the API waits on,
  so nothing new competes with vLLM. What is new is the timing — this work
  now runs while the separate in-memory key session is open, even briefly after
  sign-out, and never extends that session itself. There is no off switch:
  disabling it while encryption is on would
  silently reinstate #115 under a config flag.
- The recovery key currently unlocks via operational tooling (unwrap +
  key-session), not a self-serve UI flow — deliberate: recovery is rare
  and high-stakes; a guided flow is future work if hosting demands it.
- **Discoverability (#96, shipped 2026-08-11)**: nothing announced sealed
  mode — a household only found it by scrolling the Backups screen. The
  Overview household card (web) and Settings → Household (iOS) now OFFER
  it, to members holding `backups.manage`, when `GET /household/key-status`
  reports encryption on and mode `convenient`. A missing precondition does
  not hide the offer; it names itself ("create a recovery key first"),
  which a greyed-out control never could. The copy states the price out
  loud — no reads after a restart until someone signs in, background work
  deferred, no operator override for a lost recovery key. Sealed stays off
  by default, and the offer never switches anything on.
- Dismissal of that offer is per DEVICE (`localStorage` /
  `UserDefaults`), the posture the advisor disclaimer already uses, not per
  household: the offer explains a household decision to a *person*, and one
  member's Dismiss must not silence the strongest protection this app has
  for the co-owner who would have acted on it. No contract change — the
  existing key-status response already says everything the offer needs.

## Implementation note — password change (shipped 2026-08-11, #97)

- "Password change → re-wrap only" was designed in Phase 2 and had no caller:
  a password could be SET (invite accept, member create) or PROVEN (login),
  never replaced. `POST /auth/password` is that caller — authenticated, and
  requiring the current password again, because a session can be an
  unattended laptop.
- **The ordering matters more than the re-wrap.** `ensure_member_wrap` does
  replace an existing wrap when the household is unlocked — `_upsert_wrap`
  deletes the member's row before inserting, so a retired password stops
  being a key rather than becoming a second one. But when a household is
  sealed AND locked, the member's own wrap is the only key in reach and it
  opens with the password they still have; handed an unfamiliar one it
  unwraps nothing, logs, and returns having changed NOTHING. The old wrap
  survives. So the change path proves the CURRENT password through the seam
  first (which unlocks), and only then establishes the new one —
  `on_password_changed` is that sequence, and calling `ensure_member_wrap`
  once with the new password is the bug it exists to prevent.
- That state is not exotic: the session keyring's TTL is 30 minutes while a
  login session lasts hours, so a member who signs in and changes their
  password later that afternoon arrives sealed-and-locked.
- The re-mint is **verified, not assumed** — the stored wrap must open with
  the new password and clear the canary — and a failure refuses the whole
  change (409) rather than moving the hash. Half a change is worse than
  none: the member would authenticate and decrypt nothing.
- Every other session for that member is revoked, the calling one survives,
  and `auth.password_changed` is IRREVERSIBLE in `UNDO_POLICY`: an undo token
  carrying the old hash would be a stored credential, and re-minting a wrap
  for the password being retired would defeat the action.

## Implementation note — Phase 4 + onboarding (shipped 2026-08-04, #181/#180)

- **Isolation**: every background job (snapshots, indexing, reports, bank
  sync, autofile) skips a sealed+locked household per item and continues;
  the whole-tick guard remains only as a backstop.
- **Fair use**: per-household sliding-window cap on both chat endpoints
  (FAMILY_CFO_CHAT_HOURLY_LIMIT, 0 = off — the single-family default), a
  60s cooldown on on-demand backups, and GET /ai/usage — the operator's
  per-household view of chats, felt latency, and storage bytes.
- **Onboarding (#180)**: POST /households/hosted (system admin) mints a
  seeded household shell + a one-time owner invite; the family's first
  owner sets their own password on acceptance (which mints their member
  key — Phase 2 composes). Deliberate posture: this does NOT consult
  allow_multiple_households — that flag locks PUBLIC signup, which stays
  shut; hosting is an explicit operator act.
- **Abuse-surface review** (recorded, not all enforced): auth brute-force
  limits are per-IP+account (existing); invite minting and pairing-session
  creation are rights-gated and one-valid-per-target; the remaining soft
  spot is advisor/vision cost per household, addressed by the chat cap —
  study ticks already yield to interactive use and run one month per tick.

## Implementation note — key generation counter (shipped 2026-08-17, #0091)

Written after an incident, so it records what actually happened rather than
what the design assumed.

**What went wrong.** A household was sealed, and shortly after a member removal
rotated its key. Both acts ran in the API process. The background worker had
cached that household's DEK before either, and `_resolve_dek` returned its
process-local cache *before* consulting anything else — so the worker never
learned that the key it held had been retired. It kept reading (failing) and,
worse, kept *writing*: a bank sync re-encrypted every account's `institution`
under the dead key. ~34 hours later a restart cleared the cache. 225 values
across six columns could no longer be opened by any surviving key.

The comment on the cache read "the wrap only changes on rotation, which
restarts the process." Rotation restarts nothing. A cache invalidated by an
assumption is not invalidated.

**The two fixes.**

- **`household_keys.key_generation`** — a counter bumped by seal, unseal,
  rotate, and box-wrap healing. Cached DEKs (both the box-wrap cache and the
  session keyring) carry the generation they were read at and revalidate
  against it every `DEK_CACHE_REVALIDATE_SECONDS` (5s). Nothing is pushed to
  anyone; a stale cache disproves itself. The 5s window is the deliberate
  trade: `decrypt_text` runs per value, thousands of times per page load, so
  reading the row every time is not an option. Worst-case staleness went from
  unbounded to seconds.
- **Rotation refuses to strand its own key.** For a *sealed* household,
  rotation deletes the member and recovery wraps (a password cannot be
  re-derived server-side; a recovery key is displayed once) and re-mints device
  wraps from stored public keys. With no live device, the new key existed only
  in one process's memory — with every row already re-encrypted under it. That
  is precisely how the August rotation left its key unrecoverable.
  `rotation_would_strand_key` now reports that state and `delete_member`
  returns 409 *before* removing anyone: removing a member without the rotation
  that must follow is unsafe, and so is a rotation that cannot place its key,
  so if the second cannot run the first must not either.

**Why not just drop the cache.** Correct and far too slow — see the per-value
call pattern above. Why not push invalidation between processes? It needs a
channel the box does not have (no broker, and the worker is a separate
container), and a missed message fails silently in exactly the direction that
caused the incident. A counter the reader checks fails safe.

**Coverage.** `household_crypto` is held at 100% line coverage by
`make coverage`, wired into the backend CI job. Not a repo-wide target and it
should not become one — it is the list of modules where an untested branch has
already cost real data.

**Two follow-ups, both closed in the same PR rather than left open.**

- *Rotation could still strand its key* (#112). Passing the precondition proved
  a wrap *could* be minted, not that one *was* — and `ensure_device_wrap`
  swallows failures, correctly, because wrap upkeep must never fail a login. A
  single corrupt stored public key on a one-device household was enough to
  re-encrypt everything under a key nothing held, and still return `True`.
  Sealed rotations now mint the device wraps **before** touching a row, via a
  counting sibling (`_mint_device_wraps`), and abort if none land — free, since
  nothing has moved yet. If a later step fails, the minted wraps hold a key the
  canary does not match, so a device unlock is refused and the household stays
  on its old key: fails closed, still openable by a member password.
- *An unreadable amount counted as zero* (#110). `_dec_amount` returned 0 so
  that one bad row could not crash an aggregation. The cost was worse than the
  crash: the row became a real zero inside spending, cash flow and
  safe-to-spend, and two of them did exactly that for six days while the only
  signal was a log line. It now raises `SealedAmountUnreadableError`, mapped to
  409 with its own code — 409 and not 423 because signing in again cannot
  repair a damaged record and the client must not offer that remedy.

  The asymmetry with text is deliberate: `[encrypted — key mismatch]` is visible
  and unmistakable, so losing a merchant name degrades a page rather than
  refusing it. An amount has no such tell — a damaged one is indistinguishable
  from a legitimate zero. In a financial app a wrong number is the one output
  worth refusing to produce. If a future product call prefers degraded-but-
  flagged totals, the seam is that single raise.

**Still open.** A sealed household gets no background work at all unless a
session happens to be live, which is how the worker came to be the only thing
touching this data in the first place.
