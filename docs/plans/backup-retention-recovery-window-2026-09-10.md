# Issue #116: Tiered Backup Retention and Recovery-Window Visibility

**Plan date:** 2026-09-10
**Evidence baseline:** live GitHub `main` at `b8bb86a21de2ddd74b7efe8b2c9ba0a144883cb2`; contract `0.159`; API/web/iOS builds `0`
**Planning checkout:** `fix/issue-119-incomplete-monetary-aggregates` at `8cb66288`; the necessity verdict and current-state claims were verified against live `main`, not inferred from this separate working branch.
**Issue:** [#116 — Backup retention is about a day and a half](https://github.com/bobo-83/Family-CFO/issues/116)

## Verdict

Yes, issue #116 still needs to be fixed. The issue is open with no linked implementation, current `main` still sets `DEFAULT_BACKUP_RETENTION_COUNT = 7` and prunes completed local backups by a flat count, and the iOS screen still tells the operator that the box keeps “the last 7” (`apps/api/src/family_cfo_api/config.py:8-10,55-63`; `apps/api/src/family_cfo_api/backup_processing.py:461-470`; `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupSettingsView.swift:590-594`). Later off-box age and shared size caps do not solve the reported loss of recovery depth.

The fix is a backup-subsystem change, not a larger count. It must establish one box-global configuration for the one whole-box backup stream; use independently configurable local and off-box UTC time buckets; preserve the newest copy and protect anomalous archives; preflight caller-available capacity without claiming a guarantee; reconcile inventory and pruning metadata; expose configured targets separately from observed recovery candidates through OpenAPI; and show the same truthful states in web and iOS. Existing encryption, manifest/version gates, destructive-restore behavior, and local-success/off-box-failure semantics remain unchanged.

## Goal

Give a system administrator a bounded, cadence-independent recovery history—by default every archive for 3 days, one per UTC day through 14 days, and one per ISO week through 90 days—while allowing the local and Synology policies, logical byte caps, and free-space reserves to be configured independently. The API and both clients must state the oldest currently visible recovery candidate and capacity/coverage health without promising that an archive’s encryption key, integrity, or database restore has been verified.

## Decisions made for this plan

The user selected these requirements during the up-front planning checkpoint:

1. Tier horizons are operator-configurable rather than immutable product constants.
2. Local and off-box destinations have independent retention and capacity settings.
3. Time buckets are based on archive age in UTC and do not scale with the chosen backup cadence.
4. Issue #116 closes only after the full outcome ships: tiered retention, destination-capacity handling, an OpenAPI-backed effective recovery-window model, and matching web and iOS disclosure.

Repository evidence resolves the remaining design choices:

- Backup archives and `backup_jobs` are box-global, and `backups.manage` is a box right granted only to system administrators (`apps/api/src/family_cfo_api/models.py:708-728`; `apps/api/src/family_cfo_api/rights.py:39-47`; `apps/api/src/family_cfo_api/repository.py:333-346`). Therefore the active destination, cadence, and policies move out of arbitrary household rows into one box-global persisted configuration; no `household_id` is added to archive or retention records.
- Ordinary advisor chat is an explicit non-goal. Its executor is household-scoped and has no authenticated system-administrator context, so exposing box-wide backup inventory there would violate ADR 0065. The spec must record this exception to the M16 grounded-tool expectation; tests must not assert tool absence.
- The public `smbclient` API available throughout the declared `smbprotocol>=1.12,<2` range already provides `stat_volume()` for caller-available/total bytes and `rename()` for same-share atomic promotion. No private protocol call or exploratory implementation spike is required; capability/error behavior still needs deterministic fakes and a real-NAS manual check.

## Background and current state

### Why seven copies stopped meaning one week

M8 assumed one backup per day and retained the newest seven completed jobs (`docs/specs/11-milestone-roadmap.md:514-530,552-559,580-584`). The scheduler now supports `every_15min`, `hourly`, `every_6h`, `daily`, and `weekly`, plus `off`, but the local count is still seven (`apps/api/src/family_cfo_api/backup_processing.py:35-46,87-114`). At a six-hour cadence, those copies span only about 36 hours. Issue #116 records the consequence: by the time issue #108’s stale-key incident was investigated, every pre-incident archive was gone.

Issue #108 and merged PR #111 fixed the corruption mechanism; they could not recreate pruned archives. The issue’s proposed 3-day/14-day/3-month shape remains unimplemented. `docs/specs/README.md:1-29` also says the milestone log is historical and requires the current Spec Kit/ADR path before new behavior.

### Current ownership mismatch

Every backup contains the whole database and shared staging tree. Jobs are global and have no household foreign key (`apps/api/src/family_cfo_api/models.py:708-728`). Nevertheless, cadence, SMB host/share/folder/credentials, and the shared byte cap live on each `households` row (`apps/api/src/family_cfo_api/models.py:119-135`; `apps/api/src/family_cfo_api/repository.py:1788-1826`).

`run_due_backups()` loops through households but checks recent completion against the global job list (`apps/api/src/family_cfo_api/backup_processing.py:48-114`). In practice, a recent job selected through one household suppresses later household configurations. Manual creation similarly chooses the acting administrator’s household settings for whole-box work (`apps/api/src/family_cfo_api/api/backups.py:175-205`). A recovery-status endpoint cannot truthfully name “the” off-box window until this ambiguity is removed.

A singleton settings row is a new repository storage pattern—the codebase has global collections, process settings, and household-keyed overrides, but no persisted singleton today. It is still preferable here because the state is box-global, authenticated, UI-editable, and audited. Environment-only policy would fit startup-owned operator settings but could not support existing API/client editing; leaving active values on household rows would preserve competing sources.

### Execution and retention sequence

Scheduled and manual paths converge on `run_backup_once()` (`apps/api/src/family_cfo_api/backup_processing.py:303-409`; `apps/api/src/family_cfo_api/api/backups.py:166-218`):

1. Create a pending job and mark it running.
2. Dump the database and archive the staging tree.
3. Add app/schema metadata and encrypt the archive.
4. Write the local `.enc` file under its final name.
5. Attempt the SMB upload under its final name.
6. Mark the job completed, recording remote `synced`, `failed`, or `skipped` state.
7. Apply local count retention.
8. Apply the same household byte cap separately to local and remote inventories.
9. Apply the global remote age cap.

Manual creation does not pass `offbox_backup_retention_days`; scheduled creation does (`apps/api/src/family_cfo_api/api/backups.py:178-206`; `apps/api/src/family_cfo_api/backup_processing.py:99-114,305-314`). Local count pruning deletes the file and retains the job with `pruned_at`, while local size pruning deletes both file and row (`backup_processing.py:411-425,461-470`; `repository.py:4469-4496`). File deletion precedes metadata mutation, so a metadata failure can leave a live row pointing to a missing file. Local post-completion retention failures escape; remote retention failures are warning-only. There is no cross-process lock. The existing 60-second manual cooldown is process-local and keyed by `session.household_id` (`apps/api/src/family_cfo_api/api/backups.py:145-163`), so administrators in different active households—or different API processes—can still overlap whole-box work. Direct-to-final writes can also leave partial artifacts.

### Current inventories and capacity behavior

Local retention queries completed, unpruned jobs globally, oldest first by `completed_at` with no stable tie-break, and does not verify the referenced file (`apps/api/src/family_cfo_api/repository.py:4478-4487`). Remote inventory lists every `.enc` file by filesystem modification time; listing errors are swallowed and returned as `[]`, so an unavailable share looks empty (`apps/api/src/family_cfo_api/smb_backup.py:93-116`).

No active local or SMB capacity query exists. The shared logical cap is enforced only after the new local write and remote upload, so enough space must already exist (`apps/api/src/family_cfo_api/backup_processing.py:330-400`). `POST /backups/destination-check` only performs an SMB write/remove probe (`apps/api/src/family_cfo_api/api/backups.py:453-489`; `apps/api/src/family_cfo_api/smb_backup.py:59-75`).

Remote mtime is upload time, not necessarily snapshot time. Versioned remote filenames contain a job UUID, so the service can use the matching retained job’s `started_at` when available and fall back to mtime with an explicit timestamp-source marker. Unknown `.enc` files must not be treated as Family-CFO-managed data or deleted.

### Existing invariants

The plan must preserve all of the following:

- No encryption key means backup failure before the dump begins, and no **final backup archive** is persisted unencrypted. The existing adapters do write a plaintext database dump inside `TemporaryDirectory`; eliminating that scratch file is outside this issue.
- The operator-held archive key has no recovery path, and rotation affects only future archives (`docs/adr/0008-security-hardening-decisions.md:51-58,73-76`).
- Whole-box archives remain compatible with sealed-household design; do not invent per-household archive keys or lineage (`docs/adr/0072-per-household-encryption-design.md:151-163,200-247`).
- Manifest `app_version` and `schema_revision` restore gates remain unchanged (`apps/api/src/family_cfo_api/backup_processing.py:188-218,339-343,514-517`; `docs/adr/0074-per-component-build-numbers.md:91-97`).
- Restore remains destructive, and the recovery UI must retain its warnings (`docs/guides/backup-and-restore.md:69-89`).
- A failed SMB upload/inventory/capacity check must not convert a valid local backup into a failed backup.
- All configuration, inventory, create, delete, and restore operations remain protected by `BACKUPS_MANAGE`.
- Preserve existing remote restore/delete basename and `.enc` validation. Add local archive-path resolution and containment beneath `Settings.backup_dir`; current restore simply joins the configured directory and stored path (`apps/api/src/family_cfo_api/backup_processing.py:492-493`).

## Design clarifications

These choices resolve current-state hazards and make the implementation and verification boundaries explicit:

- **Legacy local migration:** derive a candidate outer horizon from migrated cadence and observed history, but do not claim arithmetic alone preserves every existing copy. Every upgraded installation starts with automatic pruning disabled until a system administrator explicitly confirms the migrated policy; unrepresentable (>3650-day) legacy values fall back to `keep_all` plus review instead of clamp/failure.
- **SMB feasibility:** use public `smbclient.stat_volume()` and `smbclient.rename()` available at the project’s minimum declared dependency; retain compatibility/error tests and a real-share manual check, but no discovery spike.
- **Capacity representation:** expose `available_bytes` for the authenticated process/account, not a derived `used_bytes` that would conflate SMB quotas with physical utilization.
- **Anomalous archives:** unreadable, size-mismatched, future-version, orphaned, or unrecognized files are protected from automatic deletion and excluded from recovery claims. They do not count toward the logical managed-archive cap, but their physical bytes can defeat the free-space reserve; the status becomes degraded/constrained instead of destroying evidence.
- **Remote time:** use a recognized filename’s matching job `started_at` where possible and identify mtime fallbacks. Do not silently present upload mtime as exact snapshot time.
- **Hosted-household compatibility:** retain the existing deprecated `offbox_backup_retention_days` approximation for older clients, but do not add a new global policy object to each household row. New clients read the policy from `/backups/config` or `/backups/status`.
- **Audit undo:** `backup.config_updated` is already classified irreversible because secret-bearing configuration cannot be replayed safely (`apps/api/src/family_cfo_api/undo_actions.py:142-160`). Keep that classification; do not create an undo snapshot containing credentials.

## Detailed design

### 1. Spec gate

No product code or generated-client change begins until the relevant specifications are updated and accepted in repository order:

1. `docs/specs/01-prd.md`
   - Require cadence-independent short-, medium-, and long-term recovery coverage.
   - Require independent local/off-box policy and capacity configuration.
   - Distinguish configured target from observed recovery candidates and prohibit guarantee language without a restore test.
2. New `docs/adr/0077-box-global-backup-retention.md`, indexed in `docs/specs/02-adrs.md`
   - Decide box-global ownership, singleton persistence, UTC bucket rules, capacity behavior, locking/atomicity, and the advisor non-goal.
   - Record rejected alternatives: larger flat count, household-scoped policies, environment-only editable policy, and one shared local/remote cap.
3. `docs/specs/03-domain-model.md`
   - Define `BackupSettings`, `RetentionPolicy`, `BackupInventoryItem`, `RetentionDecision`, `CapacityObservation`, `RecoveryWindow`, and `BackupRetentionEvent`.
4. `docs/specs/04-openapi.md`
   - Define the additive configuration, destination-check, remote-list, and recovery-status contract.
5. `docs/specs/05-database-schema.md`
   - Define singleton configuration, `backup_jobs.prune_reason`, and the operational retention journal.
6. `docs/specs/06-security-model.md`
   - Define system-administrator scope, secret/error/path redaction, and irreversible automated deletions.
7. `docs/specs/07-ai-orchestration.md`
   - Record backup configuration/inventory/recovery status as an explicit ordinary-advisor non-goal because the executor is household-scoped.
8. `docs/specs/08-mobile-spec.md`
   - Define iOS edit, validation, recovery, loading, degraded, and accessibility states.
9. `docs/specs/09-angular-dashboard-spec.md`
   - Define matching web behavior and translation requirements.
10. `docs/specs/10-docker-spec.md`
    - Define database ownership of active settings and the one-release legacy environment bootstrap/deprecation path.
11. `docs/specs/12-implementation-tasks.md`
    - Add the ordered work items and release/verification gates from this plan.

Do not rewrite historical M8 claims in `docs/specs/11-milestone-roadmap.md`. At most, add a short pointer to the accepted ADR if repository convention requires a supersession note.

### 2. Box-global configuration and persistence

#### 2.1 `backup_settings` singleton

Add migration `0093_box_global_backup_settings.py`, parented to current head `0092_uppercase_currency_codes`. Create one application-owned row keyed by the constant string `global`; no household foreign key is allowed.

| Column | Type | Rule |
|---|---|---|
| `key` | `String(16)` PK | Exactly `global` |
| `frequency` | `String(20)` | `off`, `every_15min`, `hourly`, `every_6h`, `daily`, or `weekly` |
| `smb_host` | `String(255)` nullable | Existing meaning |
| `smb_share` | `String(255)` nullable | Existing meaning |
| `smb_folder` | `String(500)` nullable | Existing meaning |
| `smb_username` | `String(255)` nullable | Existing meaning |
| `smb_password_encrypted` | `Text` nullable | Existing encryption; never returned |
| `smb_domain` | `String(120)` nullable | Existing meaning |
| `local_retention_mode` | `String(16)` | `tiered` or `keep_all` |
| `local_keep_all_days` | integer nullable | Recent all-archive horizon |
| `local_daily_until_days` | integer nullable | Cumulative daily horizon |
| `local_weekly_until_days` | integer nullable | Cumulative weekly horizon |
| `offbox_retention_mode` | `String(16)` | `tiered` or `keep_all` |
| `offbox_keep_all_days` | integer nullable | Same semantics, independent value |
| `offbox_daily_until_days` | integer nullable | Same semantics, independent value |
| `offbox_weekly_until_days` | integer nullable | Same semantics, independent value |
| `local_max_bytes` | `BigInteger` nullable | Logical cap for managed local archives |
| `offbox_max_bytes` | `BigInteger` nullable | Logical cap for managed remote archives |
| `local_min_free_bytes` | `BigInteger` | Reserved caller-available local space |
| `offbox_min_free_bytes` | `BigInteger` | Reserved caller-available SMB space |
| `legacy_conflict_detected` | boolean | Multiple distinct legacy household configs were found |
| `retention_review_required` | boolean | Upgraded/restored settings require explicit administrator confirmation before automatic pruning |
| `retention_activated_at` | timezone datetime nullable | Policy epoch; null disables automatic retention/cap deletion |
| `local_destination_generation` | UUID string | Opaque identity for local journal/status causality |
| `offbox_destination_generation` | UUID string | Rotated when SMB host/share/folder identity changes |
| `local_path_fingerprint` | `String(64)` nullable | Internal canonical-path fingerprint used only to rotate the opaque generation; never returned |
| `created_at` | timezone datetime | UTC |
| `updated_at` | timezone datetime | UTC; optimistic update token |

Fresh-install defaults:

- `frequency = daily`.
- Both policies are `tiered` with `3 / 14 / 90` day cumulative horizons.
- Both maximum byte caps are null (unlimited by logical inventory size).
- Both free-space reserves are `1_073_741_824` bytes (1 GiB).
- SMB fields are null, `legacy_conflict_detected = false`, `retention_review_required = false`, `retention_activated_at = now`, and both destination generations are fresh UUIDs.

Validation at Pydantic and repository boundaries:

- `tiered` requires all horizons and `1 <= keep_all_days <= daily_until_days <= weekly_until_days <= 3650`.
- Equal adjacent values are allowed and disable the corresponding intermediate band.
- `keep_all` requires all horizon fields to be null.
- Maximum caps are null or positive; API input `0` normalizes to null for backward compatibility.
- Free-space reserves are non-negative.
- SMB completeness rules remain unchanged. Omitted or JSON `null` `smb_password` preserves the stored encrypted value. An explicitly supplied empty string retains current behavior and clears only the encrypted password; it does not silently clear host/share/user fields. A later all-fields clear control is not required by this issue.
- Database check constraints enforce mode/value consistency where portable. Repository methods repeat validation so non-HTTP callers cannot create invalid state.

#### 2.2 Repository interface

Add:

- `get_backup_settings(engine) -> BackupSettingsRecord`—read the singleton and transactionally bootstrap it if missing.
- `update_backup_settings(engine, patch, *, expected_updated_at) -> BackupSettingsRecord`—partial update with optimistic conflict detection; return a typed conflict that maps to HTTP 409.
- `list_backup_settings_legacy_candidates(engine)`—migration/bootstrap-only query.
- `record_backup_retention_event(...)` and `list_backup_retention_events_for_status(...)`, scoped by current destination generation and target horizon.
- `activate_backup_retention(...)`, called only by an explicit valid administrator save, sets the policy epoch and clears the migration/restore review gate.
- Destination-generation rotation helpers: rotate off-box identity when host/share/folder changes; rotate local identity when the internal canonical `backup_dir` fingerprint changes.

Stop normal reads from household backup columns after bootstrap. Keep the columns and old repository helper for one rollback-compatible release, mark them deprecated, and remove them in a separately planned cleanup after all supported installations have materialized the singleton.

#### 2.3 Legacy household selection

Build the initial global destination deterministically:

1. Consider households with any non-default cadence, destination, credential, or cap value.
2. Prefer a complete SMB configuration (`host`, `share`, `username`, encrypted password) over incomplete/non-SMB rows.
3. Within the same completeness class, choose the greatest `updated_at`.
4. Break equal timestamps by ascending household ID.
5. If distinct non-empty configurations exist, copy the winner but set `legacy_conflict_detected = true`.

Copy cadence, SMB fields, encrypted password, and `backup_max_bytes`; initialize both destination-specific maximum caps from the old shared value. Do not revive `backup_destination_path`, because the active implementation is userspace SMB. Every existing installation sets `retention_review_required = true` and `retention_activated_at = null`; a conflict also sets `legacy_conflict_detected = true`. Until a system administrator reviews and explicitly saves, backups continue but automatic time/cap pruning is disabled and status returns the computed pending-prune count/bytes. This preserves all currently visible managed archives across cutover without keeping an ongoing count floor.

#### 2.4 Legacy retention bootstrap

Alembic must not make durable settings depend on whichever process environment happened to run migration. Migration creates schema; the first transaction-safe `get_backup_settings()` materializes policy using database evidence plus legacy settings:

- A truly empty installation receives fresh defaults.
- Local legacy count:
  - Parse `FAMILY_CFO_BACKUP_RETENTION_COUNT`, falling back to 7 for missing/invalid/non-positive input.
  - Compute the former cadence horizon as `ceil(max(0, count - 1) * cadence_minutes / 1440)` for an active migrated cadence.
  - Compute observed horizon as the ceiling in days from bootstrap `as_of` to the oldest completed, unpruned job; use zero if none.
  - If `max(90, cadence_horizon, observed_horizon + 1) <= 3650`, initialize `3 / 14 / derived_outer_days`; otherwise initialize `keep_all` and retain the mandatory review flag. Because pruning is inactive until confirmation, same-week bucket competition and prior cadence changes cannot delete an existing copy during bootstrap.
  - If cadence is `off`, omit the cadence term and use observed history; maintenance/status still runs while scheduling is off.
- Remote legacy age:
  - `0` maps to `keep_all`.
  - Positive `N <= 3650` maps to tiered `N / N / N`, which keeps every managed archive through the old age cutoff.
  - Positive `N > 3650` maps to `keep_all` plus mandatory review; never clamp or fail bootstrap.
- Once the singleton exists, database values are authoritative and legacy environment variables are ignored.

Retain `Settings.backup_retention_count`, `Settings.offbox_backup_retention_days`, Compose wiring, and `.env.example` entries for one release as bootstrap-only deprecated inputs. Emit one structured warning when bootstrap consumes either value. A later cleanup removes the fields and legacy household columns.

#### 2.5 API compatibility and downgrade

`GET/PUT /backups/config` stays the client configuration seam but becomes box-global:

- Add `local_retention`, `offbox_retention`, `local_max_bytes`, `offbox_max_bytes`, `local_min_free_bytes`, and `offbox_min_free_bytes`.
- Retain deprecated `max_bytes` for one contract window. If neither new maximum field is supplied, a supplied `max_bytes` updates both. If either new field is supplied, ignore the alias. Return the alias only when both caps are equal; otherwise return null.
- Use explicit-field tracking to distinguish omitted from null.
- `expected_updated_at` and `confirm_retention_policy` are optional during one compatibility window. Tokenless legacy requests use documented last-write-wins behavior for legacy fields but never activate a pending policy; new clients always send the token. A mismatch returns 409 without changing data.
- Omitted/null password preserves the secret; an explicit empty string clears the password exactly as today.
- Add `confirm_retention_policy: boolean = false`. Legacy/tokenless saves may update representable destination/cadence fields but cannot activate a migrated/restored policy. Only a valid request with `confirm_retention_policy=true` clears `legacy_conflict_detected`/`retention_review_required` and sets `retention_activated_at`.
- Confirmation does not prune in the request. The response returns pending prune count/bytes, and the next independent locked maintenance pass applies it. A 409 preserves the client’s unsaved draft, reloads current config/token separately, and requires deliberate reconciliation—never a blind stale-draft retry.

Downgrade behavior:

- Before dropping new tables, copy singleton cadence/SMB fields back to every household and copy a shared cap only when local/off-box caps are equal; otherwise write null and require operator review.
- Tier policies cannot be represented by old code. The rollback guide must require explicit legacy environment values before starting the downgraded release.
- Downgrade never recreates deleted files or historical rows.

### 3. Deterministic time-bucket selector

Create `apps/api/src/family_cfo_api/backup_retention.py` as a pure, synchronous module with no filesystem, database, SMB, logging, or audit I/O.

#### 3.1 Types

- `RetentionMode`: `tiered | keep_all`.
- `RetentionPolicy`: mode and the three cumulative horizons.
- `BackupInventoryItem`: destination, archive key, UTC `taken_at`, timestamp source, size, optional job ID, managed/present/readable/compatible state, and non-sensitive anomaly codes.
- `RetentionDecision`: archive key, `keep | delete`, reason (`newest`, `recent`, `daily_bucket`, `weekly_bucket`, `expired`, `bucket_superseded`, `capacity_limit`, or `protected_anomaly`), and optional bucket key.
- `RetentionPlan`: immutable decisions, `as_of`, and the exact policy/cap snapshot.

#### 3.2 UTC boundaries and ordering

For an explicit timezone-aware UTC `as_of`:

- Recent band: `[as_of - keep_all_days, as_of]`; keep every eligible archive.
- Daily band: `[as_of - daily_until_days, as_of - keep_all_days)`; keep the newest eligible archive in each UTC calendar date.
- Weekly band: `[as_of - weekly_until_days, as_of - daily_until_days)`; keep the newest eligible archive in each ISO week, Monday 00:00 UTC through the next Monday.
- Expired: strictly before `as_of - weekly_until_days`; delete unless protected by the newest invariant.
- An archive exactly at the outer cutoff belongs to the weekly band.
- A future timestamp is protected, tagged `clock_skew`, and degrades status.

Stable order is `taken_at DESC, archive_key ASC`. The first eligible item is the deterministic newest; the first item per date/week wins. The same ordering governs oldest-first logical-cap removal after tier selection.

Timestamp choice:

- Local managed job: `backup_jobs.started_at`, the snapshot boundary used by restore audit logic.
- Recognized remote filename with retained job metadata: matching job `started_at`.
- Recognized remote filename without metadata: SMB `modified_at`, explicitly marked `remote_modified_at`.

#### 3.3 Eligibility and invariants

- Always keep the newest eligible archive per configured destination, even if expired or larger than the cap.
- `keep_all` never issues time-policy deletions; logical caps may still prune eligible archives while preserving newest.
- Local failed, pending, running, already-pruned, missing, zero-byte, unreadable, size-mismatched, known-incompatible, unrecognized, and orphan items do not occupy buckets. A recognized remote file remains independently eligible even when its matching local row is pruned or absent; local metadata is timestamp/version evidence, not remote ownership.
- Missing managed local files can be reconciled as pruned during maintenance; every other anomaly is protected from automatic deletion and reported. Known-newer archives must survive a downgrade.
- Protected anomalies count against physical capacity but not the configurable logical Family-CFO archive cap. They can defeat the physical reserve, producing constrained/degraded state, but the logical-cap calculation itself remains satisfied or unsatisfied using eligible managed bytes only.
- Sparse cadence leaves empty buckets. Never synthesize a backup or substitute an adjacent bucket.
- A repeated plan with identical inventory, policy, cap, and `as_of` is identical. Repeated delete/reconcile operations treat an already-missing file and a duplicate journal event as idempotent outcomes.
- Complexity is `O(n log n)` time and `O(n)` space.
- No persistent count floor remains. Upgrade preservation is handled once during bootstrap.

### 4. Inventory and reconciliation

#### 4.1 Local inventory

Replace the retention query with a stable `started_at DESC, id ASC` query over completed, unpruned rows. For each row:

- Require a non-null relative `storage_path`; resolve it and verify it remains beneath `Settings.backup_dir`.
- Check regular-file existence, nonzero length, read permission, and actual versus recorded size.
- Missing file:
  - Exclude from recovery candidates.
  - Read-only status reports a metadata mismatch without mutating.
  - Locked maintenance marks it pruned with `prune_reason = missing_file`.
- Unreadable or size-mismatched file:
  - Exclude from `oldest_readable_at`.
  - Retain file/row for investigation and report degraded status.
- A correctly named local `.enc` file without a job row is an orphan. Report and protect it; database rollback can legitimately create this state.

Add nullable `backup_jobs.prune_reason` values at least `policy_expired`, `bucket_superseded`, `max_bytes`, `missing_file`, and `explicit_delete`. Every automated local deletion uses file delete followed by `mark_backup_job_pruned(reason=...)`; logical-cap pruning must no longer hard-delete the row. Explicit administrator deletion may continue to hard-delete only after its irreversible audit record captures the action.

If file deletion succeeds and metadata mutation fails, record/log the failure. The next locked maintenance pass sees the missing file and completes reconciliation. If deletion reports “not found,” continue reconciliation idempotently.

#### 4.2 SMB inventory

Refactor `smb_backup.py` so `list_backups()` raises a typed, friendly-mappable inventory error instead of returning `[]` on failure. Return managed remote items in `modified_at DESC, filename ASC` order.

- Manage only filenames matching the supported Family-CFO UUID/version archive grammar. Unknown `.enc` and `.partial` files are protected and reported, never pruned.
- Parse recognized job IDs and join retained local metadata when available.
- Exclude zero-byte entries from recovery candidates and report them.
- Listing qualification and read qualification are separate. A bounded status probe alternates from the oldest and newest listing-qualified candidates, reading one byte, until each endpoint is established or `REMOTE_READ_PROBE_LIMIT` (default 32 total probes) is exhausted. Return `probe_status = complete | partial | unavailable`, `probed_archive_count`, exact endpoint dates only when established, and nullable `readable_archive_count` (non-null only when every visible managed item was probed).
- Before **deleting** any remote policy/cap candidate, probe that individual file regardless of the status-read budget. A probe failure protects the item, journals an anomaly, and prevents an unsupported deletion claim.
- A filename identifying a newer app version is known-incompatible and protected. A pre-version filename remains a candidate with `compatibility_unknown` because restore intentionally permits manifest-less archives.
- `list_remote_backups` and remote restore/delete resolve typed failures explicitly. An unavailable share is not an empty list.
- Serialize every `smbclient` helper inside each process from `register_session` through final `reset_connection_cache` with one re-entrant `SmbClientGate`. The library cache reset is process-global today; a concurrent status/check call must never close an upload/download’s cached connection. API and worker processes have independent gates, while the cross-process mutation lock excludes competing mutations.

For this plan, “recovery candidate” means visible, nonzero, successfully read-probed by the current process/account at the endpoint being claimed, and not known to be version-incompatible. It does **not** mean the historical key is present, the ciphertext authenticates, archive contents are complete, migrations can run, or a destructive restore has passed.

#### 4.3 Atomic file lifecycle

- Local: write `{job_id}.enc.partial` in `backup_dir`, flush and `fsync`, then `os.replace()` to `{job_id}.enc` on the same filesystem.
- SMB: upload to the same target folder as `{final_name}.partial`, close the handle, then call public `smbclient.rename(partial_unc, final_unc)`. UUID filenames make replacement unnecessary; a pre-existing final name is a conflict, not an overwrite.
- Inventory ignores partials. On caught upload/write failure, remove the partial best-effort.
- Locked maintenance reports stale partials; it may delete only partials older than 24 hours when no operation owns the lock.
- Cancellation before promotion cleans up best-effort. If cancellation/crash occurs after final local promotion but before job completion, preserve the file and report/reconcile the running-job anomaly; never auto-delete it as an orphan.

### 5. Capacity model and behavior

#### 5.1 Capacity primitives

Add a narrow capacity interface used by backup execution, status, and destination check:

- Local `query_local_capacity(path)` uses `os.statvfs`: total bytes are `f_blocks * f_frsize`; `available_bytes` are `f_bavail * f_frsize`, the space available to the running process.
- SMB `query_capacity(target)` calls public `smbclient.stat_volume(existing_unc_path)`: total bytes from `total_size`, and `available_bytes` from `caller_available_size` so quotas/permissions are respected. `actual_available_size` may be logged as a non-sensitive diagnostic metric but is not used to approve a write or exposed as caller-usable space.

`CapacityObservation` contains:

- `status`: `ok | warning | insufficient | unknown | unavailable`.
- Nullable `total_bytes` and `available_bytes`.
- Required configured `reserve_bytes`.
- Nullable `estimated_next_backup_bytes`.
- Nullable `can_accept_estimated_backup`.
- UTC `as_of`.
- Stable `reason_code` and optional redacted operator-facing `reason`.

Unsupported volume information yields `unknown`, never zero. Authentication/network/share failures yield `unavailable`. Refactor `_friendly()` from its current raw-exception fallback into an allowlisted classifier with constant safe fallback text; touched backup logs record error class/stable code, never interpolate raw exceptions that may contain UNC paths, usernames, or credential-like data.

Estimate the next archive as the maximum size of the three newest valid local completed archives, plus 10%, rounded up to the next MiB. With no usable history, the estimate is null. After local creation, use exact ciphertext size for SMB.

State rules:

- `insufficient`: known `available_bytes < reserve_bytes + estimate`; if estimate is null, known `available_bytes <= reserve_bytes` is insufficient.
- Define `required_bytes = reserve_bytes + (estimate or 0)` and `warning_buffer = max(reserve_bytes, estimate or 0)`. `warning` means capacity is known, not insufficient, and `available_bytes < required_bytes + warning_buffer`; if both reserve and estimate are zero/null, there is no warning band.
- `ok`: known capacity is at or above the warning boundary.
- `unknown`: capacity query unsupported/indeterminate.
- `unavailable`: destination cannot be queried.

#### 5.2 Local preflight

After acquiring the global operation lock and creating/starting the job:

1. Inventory and reconcile missing local metadata.
2. When retention is activated, apply already-disposable time-bucket deletions; while review is required, compute/report the same decisions but delete nothing automatically.
3. When activated, apply the local logical cap prospectively, deleting oldest eligible non-newest items until `managed_total + estimate <= local_max_bytes` where possible.
4. Never delete the last existing valid local archive for a backup that has not succeeded.
5. Re-query capacity after deletions.
6. If known capacity is insufficient, mark the job failed with a stable capacity code before dump/write.
7. If capacity or estimate is unknown, continue in degraded state. A real `ENOSPC` remains authoritative and fails the job.

This observes only the final encrypted archive destination. `PgDumpBackupAdapter`/`SqliteFileBackupAdapter` still write a plaintext database dump under the system temporary directory before encryption; a different scratch filesystem can fail independently and remains covered by ordinary adapter/OSError handling. Do not describe the backup-dir estimate as scratch-space preflight.

#### 5.3 SMB preflight

After successful local promotion, before upload:

1. Collect strict remote inventory.
2. When retention is activated, apply eligible time-policy and prospective logical-cap deletions while preserving the newest existing valid remote archive; while review is required, report pending decisions and delete nothing automatically.
3. Query `stat_volume()` again.
4. Compare caller-available bytes with exact local ciphertext size plus off-box reserve.
5. If insufficient, skip upload and record the existing local job as completed with remote status `failed` plus a stable/redacted capacity reason.
6. If capacity is unknown, attempt upload. If inventory itself is unavailable, do not prune; attempt upload only if the existing upload seam can address the configured target safely, and retain an unavailable/degraded status.

#### 5.4 Postflight and limits

After successful local promotion and after successful SMB promotion, re-run tier selection and the destination’s logical cap with the new item present.

- Logical caps may shorten configured coverage; status becomes `constrained` and identifies the reason.
- If newest alone exceeds the cap, retain it and report the cap unsatisfied.
- Protected anomalies do not enter the logical-cap total. Their bytes can leave physical free-space headroom insufficient, which is reported separately.
- Retention/delete failures never rewrite a completed local backup to failed. They create a degraded destination state and an operational journal event.
- Preflight is subject to TOCTOU: another process or NAS client can consume space after observation. Atomic promotion and real write errors remain authoritative, and client copy must not call a successful check a guarantee.

#### 5.5 Destination check

`POST /backups/destination-check` continues its write/remove probe and returns the same `CapacityObservation`:

- Writable plus unsupported capacity: `writable=true`, capacity `unknown`.
- Writable but below configured headroom: `writable=true`, capacity `insufficient`, `can_accept_estimated_backup=false`.
- Auth/network/share failure: `writable=false`, capacity `unavailable`.

### 6. Execution configuration, locking, and failure boundaries

#### 6.1 One execution configuration

Create immutable `BackupExecutionConfig`, assembled once from process `Settings` plus `BackupSettingsRecord`, containing database/staging/local paths, encryption key, cadence, SMB target, both policies, both logical caps, and both reserves.

The operation owner acquires the mutation lease before calling the builder. The
builder requires that lease, and the resulting snapshot is consumed only by the
same lease-owned lifecycle. Manual creation conceptually calls:

```text
run_backup_once(engine, settings, now=None, finalize=None) -> terminal_backup_job
```

Scheduled creation acquires non-blockingly, builds the same configuration, makes
the cadence decision, and calls an internal locked helper without reacquiring.
Remove the separate `retention_count`, shared `max_bytes`, and
`offbox_retention_days` arguments only after every caller migrates. This
eliminates the current manual/scheduled divergence and any stale pre-lock target
or policy snapshot.

#### 6.2 Box-global scheduling

`run_due_backups()` becomes one box-level cadence decision:

- Try the mutation lease before reading operational configuration or cadence.
- After acquisition, load the singleton and latest completed job once.
- Return `0` when frequency is `off` or a completed global job is within cadence.
- Otherwise run one backup under that same lease and return `1`.
- Remove the household loop and household-attributed target selection.
- Scheduled audit/journal entries describe box-global work; do not imply the archive belongs only to the administrator’s active household.

#### 6.3 Operation lock

Only one backup, restore, retention-maintenance, or archive-deletion operation may mutate backup storage at a time.

Add a `BackupOperationLock` seam:

- PostgreSQL production uses a fixed `pg_try_advisory_lock` key on a dedicated SQLAlchemy connection held for the full operation and released in `finally`.
- SQLite tests use a process-local lock behind the same interface; concurrency tests patch/acquire the seam explicitly rather than depending on host behavior.
- Scheduled execution skips if busy and records `lock_skipped`.
- Manual creation returns `409 backup_in_progress`. Globalize the current per-household, process-local 60-second `429` anti-hammer cooldown to the single box backup stream; it remains a separate rate-limit rule, not the cross-process exclusion mechanism.
- Restore and explicit local/remote delete acquire the same lock before source,
  path, target, policy, or capacity selection.
- The lease spans terminal job/remote-result writes, request audit finalizers,
  restore boundary/final audit work, and response-driving record/capacity reads.

Do not hold an ordinary row transaction open across `pg_dump`/SMB I/O. Repository mutations use their normal short transactions while the session-level advisory lock supplies cross-process exclusion.

#### 6.4 Independent maintenance lifecycle

Add `run_backup_maintenance()` to the worker’s existing five-minute backup tick (or a sibling five-minute tick). It runs independently of configured backup cadence, including `off` and not-due periods:

1. Try the mutation lock non-blockingly; a busy pass journals/logs `lock_skipped`.
2. While **owning** the lock, reconcile missing local metadata, inspect interrupted jobs/partials, collect both inventories, and update health events.
3. Delete stale `.partial` files older than 24 hours only while the cleaner owns the lock.
4. If `retention_review_required` or `retention_activated_at` is null, compute pending decisions for status but perform no automatic time/cap deletion.
5. Otherwise apply destination policies/caps and journal deterministic outcomes.

When maintenance owns the lock, no conforming backup mutation is active. A `running` row older than `backup_io_timeout_seconds + one worker interval` is marked failed with `interrupted`; any promoted final file is protected/reported rather than certified complete or deleted. A newer running row is reported but left alone, covering rolling-upgrade overlap with an older process that did not yet use the lock.

#### 6.5 I/O deadline and lock ownership

Add positive process setting `FAMILY_CFO_BACKUP_IO_TIMEOUT_SECONDS` (default 3600) and carry it in `BackupExecutionConfig`. Apply it to `pg_dump`/`pg_restore` subprocesses and supported SMB connection/I/O seams. The synchronous operation—not the HTTP request coroutine—owns the mutation lock. If a client disconnects or an async waiter is cancelled, the worker thread continues and holds/releases the lock only when real I/O ends. Shield/observe the thread result so cancellation cannot advertise that the lock is free.

Before every destructive retention/restore phase, verify the dedicated PostgreSQL advisory-lock connection is still alive and still owns the session lock. On connection loss, abort further deletion/promotion, retain existing files, and record degraded state. Integration-test the production lock adapter with two independent PostgreSQL connections; SQLite lock tests alone are insufficient.

#### 6.6 Restore ownership and reconciliation

Operational settings control external-file deletion, so restore must deliberately differ from ordinary whole-database rollback:

1. Under the mutation lock, capture the current singleton—including encrypted SMB credential—in memory only; never log or persist a plaintext credential copy.
2. Fully extract the archive document tar into a same-filesystem sibling directory. Any extraction/capacity/permission failure stops before live state changes.
3. For SQLite, migrate the isolated archive database file to head before promotion. For every destructive backend, pause retention and rotate the current destination generations to a fresh archive-external rollback marker, then capture a live rollback dump. PostgreSQL custom dumps cannot be migrated in place, so its archive migration remains inside the reversible live phase.
4. Restore/promote the database and require its migration to current head. A timeout or nonzero exit remains a typed, redacted failure, never a logged success.
5. Re-upsert the captured current operational settings, rotate both destination generations, set `retention_review_required = true` and `retention_activated_at = null`, and journal `restore_reset`. Restored historical policy/journal rows do not resume pruning automatically.
6. Reconcile the source local job to `completed` using the pre-restore record/path when it still exists; mark other stale restored `running` rows `interrupted`. Newer physical files whose rows vanished are protected orphans.
7. Atomically replace the live document directory with the completely staged archive tree, but retain its old-tree rollback path and the database rollback image through the final audit callback, response-driving job/capacity reads, and post-read lease certification. Discard the rollback assets only after those steps succeed.
8. On any caught failure after live database mutation, restore and verify the pre-request database/document pair by both prior schema revision and the archive-external settings marker, then reapply the captured settings with fresh generations/review pause before propagating the redacted primary failure. If ownership was lost, reacquire the exclusive lease before any compensation; if another operation owns it, perform no unowned rollback and return the operator-intervention failure. SQLite pre-migration failures leave live data untouched but apply the same policy pause.

This preserves the currently configured physical destinations/credentials across financial-data rollback and prevents an old schema or aggressive policy from deleting newer external files. The compensating sequence guarantees one coherent old/new pair for completed calls and caught failures while ownership is retained or reacquired. Database and filesystem promotion have no shared crash-atomic transaction: process/host/power loss between them, or lease loss when a successor already owns the lock, remains an operator-recovery case. Record that boundary in the ADR, restore warning, guide, and tests. Remote restore follows the same settings preservation/review gate even when no local job row exists.

#### 6.7 Failure and cancellation matrix

- Dump, encryption, local capacity, or local write failure: mark job failed; no SMB upload; do not prune still-required archives beyond safe preflight decisions already journaled.
- SMB capacity/upload failure: local job remains completed; persist remote failure code/message.
- Inventory, retention, or journal failure after local completion: preserve completed state; destination is degraded and warning-logged.
- File deletion followed by metadata failure: next pass reconciles missing file.
- Unexpected exceptions must not leave a permanent running row silently; outer orchestration records a sanitized failure when the job ID exists.
- Restore document staging/extraction failure leaves the live database/tree untouched. A caught failure after database mutation restores and verifies the pre-request database/tree, including failures in final audit/response reads, and re-enables the review pause while ownership is retained or reacquired. Rollback-verification failure and inability to reacquire after lease loss are distinct redacted operator-intervention errors; compensation never writes without exclusive ownership.
- Blocking filesystem/SMB/status work invoked by FastAPI runs in a thread pool, under the per-process SMB gate where applicable. Repository/filesystem/SMB code stays synchronous; Angular and Swift clients remain asynchronous.
- Request cancellation never releases an in-flight synchronous operation’s lock. Cleanup is best-effort and never deletes a promoted final archive merely because metadata completion is uncertain.

### 7. Retention journal and audit

Add box-global `backup_retention_events` without a household foreign key:

| Column | Type |
|---|---|
| `id` | UUID string PK |
| `destination` | `local | offbox` |
| `archive_key` | nullable string |
| `backup_job_id` | nullable string, no FK required for remote/orphan cases |
| `operation_id` | UUID/string identifying one maintenance, backup, restore, or explicit-delete pass; reused by retries |
| `event_key` | non-null unique deterministic key derived from operation, generation, archive-or-destination sentinel, action, and reason |
| `destination_generation` | opaque UUID copied from active settings |
| `action` | `pruned | explicit_deleted | reconciled | prune_failed | inventory_failed | inventory_succeeded | capacity_blocked | lock_skipped | anomaly_detected | restore_reset` |
| `reason` | stable machine code |
| `archive_taken_at` | nullable UTC datetime retained even after remote deletion |
| `timestamp_source` | nullable `job_started_at | remote_modified_at` |
| `size_bytes` | nullable integer |
| `policy_updated_at` | nullable UTC policy revision/epoch |
| `policy_snapshot` | nullable JSON |
| `detail` | nullable redacted text |
| `occurred_at` | UTC datetime |

Index `(destination_generation, occurred_at)` and enforce uniqueness on non-null `event_key` so retries—including destination-wide events with no archive key—are idempotent. Keep events durably; they are small operational facts and are needed to distinguish coverage still building from coverage shortened by pruning. Status considers only the current destination generation, uses deletion facts whose `archive_taken_at` intersects the current target, and uses the latest `inventory_failed`/`inventory_succeeded` event to clear transient degradation. Rotating host/share/folder or the local-path fingerprint creates a new generation so NAS A cannot make NAS B look shortened. Explicit administrator deletion writes this journal before/with its household audit. The table must never store credentials, raw SMB exceptions, UNC paths, usernames, archive content, or financial data.

`PUT /backups/config` keeps its existing irreversible `backup.config_updated` classification and records the action against the administrator’s active household because the existing audit system is household-scoped. The summary names changed groups (cadence, local policy, off-box policy, caps/reserves, destination) without values. Automatic pruning is irreversible and uses the global retention journal rather than pretending to be a household financial event.

### 8. Recovery-window service and OpenAPI

Add a read-only service that inventories both destinations at one explicit `as_of`, joins only current-generation retention events relevant to the active target, derives capacity, and returns schemas without mutation. Add `GET /backups/status`, protected by `BACKUPS_MANAGE`.

#### 8.1 Schemas

`BackupRetentionPolicy`:

- `mode: tiered | keep_all`.
- Nullable `keep_all_days`, `daily_until_days`, `weekly_until_days`.
- Nullable `target_oldest_at` (`as_of - weekly_until_days` for tiered; null for keep-all).

`BackupCapacityObservation` mirrors the internal model: status, total/available/reserve/estimate bytes, nullable acceptability, `as_of`, reason code, and redacted reason.

`BackupDestinationRecoveryStatus`:

- `destination: local | offbox`.
- `configured: boolean`.
- `status: not_configured | empty | healthy | constrained | degraded | unavailable`.
- `coverage_status: not_applicable | empty | building | met | incomplete | shortened | unknown`.
- `policy`, `retention_review_required`, nullable `retention_activated_at`, and pending-prune count/bytes while review is required.
- `visible_archive_count` for recognized, visible, non-partial managed archives.
- Nullable `readable_archive_count`, plus `probe_status: complete | partial | unavailable` and `probed_archive_count`.
- Nullable `oldest_readable_at` and `newest_readable_at`, returned only when the corresponding bounded endpoint probe succeeds (local qualification is complete).
- Nullable `oldest_timestamp_source: job_started_at | remote_modified_at`.
- `metadata_mismatch_count`, `protected_anomaly_count`, `compatibility_unknown_count`, and `known_incompatible_count`.
- `capacity`.
- Stable `reason_codes` plus one optional redacted reason.
- `as_of`.
- Fixed `verification_scope: inventory_read_probe`.

`BackupRecoveryStatus`:

- `as_of`.
- `overall_status: empty | healthy | constrained | degraded | unavailable`.
- Nullable `overall_oldest_readable_at` and `overall_newest_readable_at` using only successfully qualified endpoints across configured/queryable destinations.
- Exactly one local and one off-box destination object.
- Fixed `verification_scope: inventory_read_probe`.

#### 8.2 State derivation

Destination `status`:

- `not_configured`: off-box credentials incomplete; local is always configured when `backup_dir` resolves.
- `empty`: inventory/probing succeeded completely, has no recovery candidate, and has no protected anomaly.
- `healthy`: inventory succeeded, endpoint qualification is complete, no material anomalies/events exist, target is met/building as expected, and capacity is `ok` or `warning`.
- `constrained`: logical/physical limits shortened coverage, the newest eligible file alone prevents satisfying the logical cap, or known physical capacity—including protected/unmanaged bytes—cannot accept the estimate while preserving reserve.
- `degraded`: usable candidates exist **or protected evidence exists**, but clock skew, timestamp fallback, unknown capacity, migrated/restore review, metadata mismatch, protected anomaly, partial probing, or the latest unresolved inventory/prune failure prevents a healthy claim.
- `unavailable`: inventory cannot be queried.

Coverage `status` is about the outer recovery horizon, not a claim that every calendar bucket had a successful backup:

- First derive the **outer target bucket** containing `target_oldest_at`: the intersecting ISO week when a weekly band exists, the intersecting UTC day when only a daily band exists, otherwise the exact recent cutoff. Do not keep an extra pre-cutoff archive merely to satisfy status.
- `building`: the active policy is younger than its outer horizon and neither job/journal history nor a deletion fact shows the box had a candidate opportunity in the target bucket.
- `met`: at least one qualified candidate falls inside the outer target bucket, at least one newer candidate exists (so a lone newest-invariant archive cannot claim healthy depth), and endpoint probing is complete enough to establish the dates.
- `shortened`: a logical cap, capacity action, explicit deletion, or policy prune removed/disqualified a candidate that would have satisfied the current target.
- `incomplete`: the policy is mature and inventory is queryable, but no candidate reaches the outer target bucket for non-retention reasons such as schedule-off time, repeated backup/upload failures, or sparse history. The exact oldest date remains visible.
- `unknown`: pending policy review, partial/unavailable probing, inventory failure, missing causality, or protected anomalies prevent determination.
- `not_applicable`: keep-all policy.
- `empty`: inventory was fully queryable, no protected anomaly exists, and no qualified candidate exists.

Advance-`as_of` tests must cover normal mature daily/weekly histories, the oldest partial ISO-week/day bucket, a lone expired newest archive, and each overlap precedence.

Overall rules:

- Ignore unqualified archives when deriving dates.
- `unavailable` if no configured destination can be inventoried.
- `empty` only if every configured destination was successfully inventoried/probed and is empty; any unavailable destination or protected anomaly prevents aggregate `empty`.
- `healthy` only if every configured destination is healthy.
- `constrained` if any configured destination is constrained.
- Otherwise `degraded`; a healthy local destination never hides an unavailable Synology destination.

The contract and UI must say “oldest readable backup currently visible” or “recovery candidate,” not “guaranteed restore point.” Key availability, authenticated decryption, archive integrity, and database restore are evaluated only by restore.

#### 8.3 Existing contract changes

- Extend `BackupConfig`/update request with policies, separate caps/reserves, `legacy_conflict_detected`, `retention_review_required`, nullable `retention_activated_at`, optional `expected_updated_at`, optional `confirm_retention_policy`, and deprecated `max_bytes` compatibility. Destination generations/path fingerprints remain internal.
- Extend `BackupDestinationCheckResponse` with `capacity`.
- Extend `RemoteBackupListResponse` with `status`, `as_of`, and redacted reason. An unavailable listing has an empty `backups` array **and** `status=unavailable`; clients must inspect status.
- Keep `HostedHouseholdList.offbox_backup_retention_days` for one compatibility window, derived as `0` for keep-all or `weekly_until_days` for tiered, and mark it deprecated/approximate. Do not add the full global policy to each household response.
- Add standard 409 documentation for configuration concurrency and `backup_in_progress`.

`shared/openapi/family-cfo.v1.yaml` changes before clients. At the pinned baseline, new clients will require the additive status/config fields, so follow ADR 0074: bump contract `0.159 -> 0.160`, keep/reset `apps/api/BUILD`, `apps/web/BUILD`, and `apps/ios/BUILD` to `0`, and add immutable `shared/openapi/compatibility/0.160.yaml`. If `main` advances first, re-evaluate the next contract number rather than overwriting a newer fixture.

### 9. Web behavior

Data flow remains generated client → `ApiService` → Backups component (`apps/web/src/app/api-client/sdk.gen.ts:1531-1597`; `apps/web/src/app/core/api.service.ts:743-770`; `apps/web/src/app/pages/backups/backups.ts:78-290,438-462`).

#### 9.1 Loading and state

For a system administrator, initial load fetches global config, recovery status, local history, and household key status; remote inventory may be sourced from the status service or fetched for the existing detailed list. Add `getBackupRecoveryStatus()` to `ApiService`.

Refresh recovery status after backup creation, configuration save, destination check, local/remote deletion, and any remote-list refresh. Bind each config/status request to the authenticated session plus a monotonically increasing request generation and the config `updated_at` observed at start. Apply success or failure only if that ownership is still current; a slow pre-save response must not overwrite post-save status, and an older failure must not clear newer success. Serialize/coalesce valid auto-saves. On 409, preserve the unsaved draft, load current config separately, and require deliberate reconciliation rather than replaying it. A current refresh failure clears the prior status/date and shows “Recovery status unavailable” without disabling existing create/restore/delete actions.

Rename local `isOwner` concepts for this screen to `canManageBackups` and say “Only a system administrator can manage whole-box backups.” Server authorization remains authoritative.

#### 9.2 Configuration UI

Add a **Retention and capacity** card with **On this box** and **Synology** subsections. Each has:

- `Tiered` / `Keep every backup` mode.
- When tiered: “Keep every backup for,” “Keep one per day through,” and “Keep one per week through” numeric day inputs.
- Destination-specific “Maximum total size” and “Keep at least this much space free” inputs.
- Summary: “Every backup for 3 days · one daily through 14 days · one weekly through 90 days.”

Validate ordering and bounds locally. Retention/cap/reserve edits remain drafts and use one explicit **Save and activate retention** action with `confirm_retention_policy=true`; do not auto-save destructive policy changes on blur. Existing destination/cadence autosaves may remain, but serialize/coalesce them, include `updated_at`, and never let them clear a pending review. Show pending prune count/bytes before confirmation. Move/remove the current shared max-size control from the schedule card.

#### 9.3 Recovery disclosure

Add a **Recovery window** card with one row/card per destination:

- Configured policy summary and target horizon.
- “Oldest readable backup currently visible: {date}” plus timestamp-basis caveat when remote mtime is used.
- Visible managed count, nullable exact readable count, number probed, and an explicit partial/unavailable probe state.
- Coverage state; if the bounded probe cannot establish an endpoint, show that it is unknown rather than re-labeling a merely listed date as readable.
- Capacity summary, e.g. “42 GB available of 100 GB · next backup estimate 1.2 GB · 1 GB reserved.”

Required copy intent:

- Building: “Coverage is still building toward the configured target.”
- Shortened: “Storage limits shortened the configured recovery window.”
- Unknown capacity: “Capacity could not be measured; backups will still be attempted.”
- Unavailable inventory: “Synology inventory is unavailable, so its recovery window is unknown.”
- Not configured: “No off-box destination is configured.”
- Empty: “No readable backups are currently visible.”
- Every state: archive integrity and key correctness are checked during restore.

Use accessible headings, `role=status` for non-fatal observations, `role=alert` for constrained/unavailable warnings, and text alongside every color/icon. Add all new `$localize`/template strings to `apps/web/src/locale/messages.lt.xlf` and `messages.vi.xlf`; do not ship primary states as English-only fallbacks.

### 10. iOS behavior

Generated Swift contract → `BackupAPI` → `@MainActor BackupViewModel` → `BackupSettingsView` remains the seam (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupAPI.swift:7-204`; `BackupViewModel.swift:7-190,272-302`).

#### 10.1 API and view model

- Add `recoveryStatus() async throws -> BackupRecoveryStatus` to `BackupAPI` and implement it in `LiveBackupAPI` using regenerated code.
- Extend `BackupConfigDraft` for both policies, both maximums, both reserves, and optimistic `updatedAt`.
- Add editable local/off-box policy state, separate GB values, pending-prune preview, an explicit Save/Activate action, `recoveryStatus`, horizon-validation message, configuration conflict state, and a distinct `recoveryStatusError`. Retention changes are not saved on field blur.
- Carry authenticated-session revision, request generation, and config `updatedAt` through every async config/status completion. Apply only a completion still owned by the same session/generation; serialize/coalesce valid saves, and preserve the draft on 409 while loading current server state separately.
- Refresh status after the same mutations as web. Only a still-current failure clears stale recovery data; the remainder of the screen stays usable.
- Update every `BackupAPI` mock. Add a reusable default fixture/mock implementation if the test target’s organization permits, then add focused retention/recovery view-model tests rather than treating `BackupViewModelExportTests.swift` as existing coverage.

#### 10.2 SwiftUI

Add sections matching web:

- **Retention and capacity** with destination mode picker, tier fields, maximum, reserve, validation, and summary.
- **Recovery window** with target, qualified endpoint dates, visible/nullable-readable counts, probe completeness, coverage state, capacity, timestamp caveat, and status-specific warning for each destination.

Use `LabeledContent`, `Picker`, numeric `TextField`, and `Label` with SF Symbols plus text. VoiceOver labels name destination, state, and formatted date. Replace “Encrypted backups kept on the box (the last 7)” with neutral copy directing the user to configured retention/recovery information.

An older or temporarily unreachable API may not provide the new status route. Render unavailable copy and keep existing backup/restore actions usable; contract compatibility still prevents releasing the dependent `0.160` client against an older `0.159` server.

All Swift, generated Swift, Xcode-project, and iOS test work must be performed on macOS with Xcode and installed iOS/watchOS platforms, per `AGENTS.md`.

### 11. Security and advisor boundary

- Keep `BACKUPS_MANAGE` on every existing and new route; test that household roles cannot gain the box right and that a system administrator works regardless of active household role.
- Never return/replay the SMB password or encrypted credential.
- Only stable reason codes and allowlisted friendly text reach API responses, journal details, audits, or logs. Change `_friendly()` so unknown exceptions return constant redacted text, and remove raw exception interpolation from touched SMB upload/list/prune logs. Do not expose UNC paths, host/share names in status errors, usernames, raw exceptions, or stack traces.
- Validate local paths beneath `backup_dir`; preserve remote basename/extension checks; manage only recognized Family-CFO archive names.
- Configuration audit summaries name changed groups, never values.
- Do not add an advisor tool. Document the system-admin-only/whole-box rationale in the PRD, ADR, AI spec, and implementation tasks. Do not add a test that asserts the tool is absent; the repository explicitly prohibits absence assertions.
- A future system-administrator advisor requires its own authenticated box-global executor and ADR; it is not part of issue #116.

## Data and state flows

### Scheduled backup

1. Worker tick tries the global operation lock.
2. If busy, journal/log `lock_skipped` and return without duplicating work.
3. Load/build the singleton and immutable execution config.
4. Check global completed-job cadence; stop if not due/off.
5. Create and start one job.
6. Reconcile local inventory, apply safe preflight retention/cap, and query capacity.
7. On known insufficient local headroom, fail the job with no dump.
8. Dump/archive/encrypt to temporary storage; atomically promote local ciphertext.
9. Strictly inventory/preflight SMB, then atomically upload or record remote failure.
10. Mark job completed with remote outcome.
11. Apply local and remote postflight retention/caps; journal each decision/failure.
12. Release lock in `finally`.

### Manual backup

Use the same lease-owned steps and execution config. The differences are
actor/audit context, the newly box-globalized 60-second process-local cooldown,
and a 409 response when the operation lock is busy. The cooldown is retained
after a lifecycle that produces a failed job, but its 429 copy says only that a
recent attempt is cooling down; it never claims the data was saved. Create audit
and the response-driving terminal record read happen before lease release. It
must no longer omit off-box policy.

### Read-only status

1. Capture one UTC `as_of` and load global settings.
2. Build local inventory without reconciliation mutations.
3. Build strict remote inventory or a typed unavailable state.
4. Query both capacities and derive estimates.
5. Join events only from each current opaque destination generation; use retained deletion timestamps and latest success/failure events to distinguish building, met, incomplete, shortened, and unknown coverage.
6. Run bounded endpoint read probes and return their completeness explicitly. Do not prune, rewrite metadata, or test-decrypt archives.
7. FastAPI runs blocking filesystem/SMB work off the event loop.

### Maintenance

Every worker interval tries the same lock even when backup frequency is `off` or a backup is not due. While holding it, reconcile stale rows/partials, refresh inventory-health events, and—only after retention activation—apply policy/cap decisions. This is the defined trigger after configuration save and the only automatic stale-partial cleanup path.

### Restore/delete

Acquire the same global lock before selecting or touching an archive or target.
Restore captures and re-applies current operational settings, rotates destination
generations, pauses automatic pruning for review, reconciles the
source/interrupted jobs, and protects post-snapshot physical orphans. The
pre-restore audit boundary, successful post-restore audit, source/capacity result,
and explicit-delete audit remain inside the same lease. A migration timeout or
nonzero exit is surfaced only after the pre-request database/document pair is
preserved or restored and settings/review/journal safety is durable. Explicit
deletion remains distinct from automated pruning and records both
`explicit_deleted` journal facts and irreversible household audit state.

## Orchestration progress

- [x] WI-1 — Spec Kit and ADR gate (`6b1ebb4c`)
- [x] WI-2 — Pure retention model (`000b7dc6`)
- [x] WI-3 — Box-global persisted settings (`000b7dc6`)
- [x] WI-4 — Safe inventory, capacity, locking, maintenance, and restore lifecycle (`d53638d6`, `e56068e8`, `461ffd04`)
- [x] WI-5 — Recovery API/OpenAPI and compatibility contract (`dfdb820d`, `0a9f73af`)
- [x] WI-6 — Web configuration and disclosure (`23f26775`)
- [x] WI-7 — iOS parity on macOS (`25105493`)
- [ ] WI-8 — Operator docs and local final-integration matrix complete; PR CI, supported-NAS check, deployment, and live rollout verification remain pending

Final-integration evidence recorded on 2026-09-10 from macOS/Xcode 26.6:

- API coverage/lint/runtime OpenAPI: 1,225 passed and two intentionally environment-gated PostgreSQL tests skipped in the ordinary run; coverage and lint gates passed.
- Disposable PostgreSQL 17: the required migration/bootstrap and real advisory-lock conflict/connection-loss tests both passed with loud required mode enabled. This local subset does not prove the combined WI-3 cross-dialect case matrix or the separate PR-CI checkbox, so both remain open.
- Backup service: 14 passed. Web: generated client clean, 361 tests passed, source extraction succeeded, and the catalog/all-locale build gate passed for English, Lithuanian, and Vietnamese.
- Contract/version/client compatibility and Swift generation drift checks passed. The complete iOS simulator suite passed 495 tests across 80 suites.
- Supported-NAS behavior, deployment/live verification, release, issue closure, and later legacy-input removal remain external work and are not advanced here.

## Execution index

### WI-1 — Accept the backup policy specification

- **Goal:** Establish the product, architecture, data, contract, security, client, deployment, and advisor decisions before implementation.
- **Done when:** The Spec Kit amendments and new ADR in the order above are reviewed/accepted, with exact retention intervals, global ownership, capacity semantics, recovery language, and non-goals.
- **Key files:** `docs/specs/01-prd.md` through `docs/specs/10-docker-spec.md`, `docs/specs/12-implementation-tasks.md`, `docs/adr/0077-box-global-backup-retention.md`, `docs/specs/02-adrs.md`.
- **Dependencies:** None.
- **Size:** M.

### WI-2 — Build and prove the pure retention model

- **Goal:** Make keep/delete outcomes deterministic, auditable, destination-independent, and cadence-independent.
- **Done when:** Pure selector tests cover every interval boundary, tie, sparse cadence, keep-all mode, protected anomaly, cap interaction, newest invariant, and idempotence without I/O.
- **Key files:** New `apps/api/src/family_cfo_api/backup_retention.py`; new/renamed `apps/api/tests/test_backup_retention.py`.
- **Dependencies:** WI-1.
- **Size:** M.

### WI-3 — Migrate to box-global persisted settings

- **Goal:** Replace arbitrary household ownership with one validated, upgrade-safe source for cadence, SMB target, destination policies, caps, and reserves.
- **Done when:** Migration `0093`, model/repository CRUD, deterministic legacy selection/bootstrap, all-existing-copy review gate, destination generations, idempotent journal facts, conflict signaling, tokenless compatibility writes, downgrade behavior, SQLite migration tests, and PostgreSQL 17 integration coverage provisioned in backend CI pass.
- **Key files:** `database/migrations/versions/0093_box_global_backup_settings.py`, `models.py`, `repository.py`, `config.py`, migration tests.
- **Dependencies:** WI-1; policy types from WI-2 for validation/serialization.
- **Size:** L.

### WI-4 — Make inventory, capacity, and mutation lifecycle safe

- **Goal:** Supply strict local/SMB inventories, public capacity queries, atomic promotion, cross-process exclusion, reconciliation, and journaled pruning.
- **Done when:** Local and SMB fakes prove empty versus unavailable, bounded read qualification, capacity states, stable timestamps/order, anomaly protection, atomic partial cleanup, per-process SMB serialization, independent maintenance, production lock conflicts/loss, request-cancellation ownership, restore settings preservation, pre/postflight behavior, and partial-failure reconciliation.
- **Key files:** `backup_processing.py`, `smb_backup.py`, `repository.py`, `models.py`, new `test_smb_backup.py`, processing tests.
- **Dependencies:** WI-2 and WI-3.
- **Size:** XL.

### WI-5 — Publish the recovery contract and server behavior

- **Goal:** Give authorized clients one truthful global configuration and recovery-status API.
- **Done when:** Pydantic/OpenAPI schemas and routes implement every state/compatibility rule; auth, audit, redaction, status derivation, runtime OpenAPI drift, contract `0.160`, and compatibility fixture checks pass.
- **Key files:** `schemas.py`, `api/backups.py`, `api/households.py`, `shared/openapi/family-cfo.v1.yaml`, `VERSION`, component `BUILD` files, `shared/openapi/compatibility/0.160.yaml`, API tests.
- **Dependencies:** WI-3 and WI-4.
- **Size:** L.

### WI-6 — Add web configuration and disclosure

- **Goal:** Let system administrators edit both destination policies and see target, observed recovery candidates, capacity, and degraded states accessibly.
- **Done when:** Generated client, service wrapper, component/template/styles, state refresh/clearing, validation, all UI states, accessibility assertions, and Lithuanian/Vietnamese translations pass tests/build.
- **Key files:** `apps/web/src/app/api-client/`, `api.service.ts`, `pages/backups/*`, `messages.lt.xlf`, `messages.vi.xlf`.
- **Dependencies:** WI-5 contract/API.
- **Size:** L.

### WI-7 — Add iOS parity on macOS

- **Goal:** Provide the same configuration, truthful recovery states, error isolation, and accessible copy in SwiftUI.
- **Done when:** Regenerated Swift client, `BackupAPI`, view model, view, mocks, focused tests, generated-client check, and Xcode test suite pass on an installed simulator.
- **Key files:** generated `Client.swift`/`Types.swift`, `BackupAPI.swift`, `BackupViewModel.swift`, `BackupSettingsView.swift`, existing export tests, new `BackupViewModelRetentionTests.swift`, Xcode project only if needed.
- **Dependencies:** WI-5; macOS/Xcode environment.
- **Size:** L.

### WI-8 — Complete operator docs, compatibility, and rollout

- **Goal:** Make upgrade, rollback, settings precedence, capacity limitations, and release order operationally executable.
- **Done when:** Backup/API/database/deployment docs match behavior; PostgreSQL 17 is declared and provisioned for the required production-lock tests with loud-skip enforcement; legacy inputs are marked bootstrap-only; full API/web/iOS/client-compatibility matrices pass; API/worker deploy before dependent web/iOS; live status is checked before issue closure.
- **Key files:** `AGENTS.md`, `.github/workflows/backend-api.yml`, `.env.example`, `docker-compose.yml`, `docs/guides/backup-and-restore.md`, `apps/api/README.md`, `database/README.md`, release scripts/checks as consumers only.
- **Dependencies:** WI-3 through WI-7.
- **Size:** M.

## File-by-file impact

### Specifications and documentation

- `docs/adr/0077-box-global-backup-retention.md` — new ownership/policy/capacity/recovery/advisor decision and alternatives.
- `docs/specs/01-prd.md` through `10-docker-spec.md` — amendments enumerated in the spec gate.
- `docs/specs/02-adrs.md` — index accepted ADR.
- `docs/specs/12-implementation-tasks.md` — work items and rollout gates; do not rewrite historical milestone status.
- `docs/guides/backup-and-restore.md` — tier/capacity semantics, independent destinations, newest invariant, status limitations, upgrade/rollback, and removal of flat-count guidance.
- `apps/api/README.md` — global execution/configuration, endpoints, environment deprecation, tests.
- `database/README.md` — singleton ownership, migration/bootstrap, prune reasons, journal.
- `.env.example`, `docker-compose.yml` — legacy inputs labeled bootstrap-only for one release; API and worker retain identical environment during transition.
- `AGENTS.md` — declare PostgreSQL 17 as a required full-matrix tool for production advisory-lock and migration integration cases, with the local-install/skip contract.
- `.github/workflows/backend-api.yml` — provision a PostgreSQL 17 service, expose only a synthetic test database through `FAMILY_CFO_TEST_DATABASE_URL`, and set `FAMILY_CFO_REQUIRE_POSTGRESQL=1` so a missing service or accidental skip fails loudly.

### Persistence and repository

- New `database/migrations/versions/0093_box_global_backup_settings.py` — `backup_settings` (including review/activation and opaque generations), `backup_retention_events` (including non-null unique event key and deletion facts), `backup_jobs.prune_reason`; parent `0092`.
- `apps/api/src/family_cfo_api/models.py` — new tables/checks, review/activation and destination-generation state, journal causality fields/uniqueness, and prune reason; retain legacy household columns temporarily.
- `apps/api/src/family_cfo_api/repository.py` — global settings CRUD/bootstrap/activation, destination-generation rotation, stable inventory query, mark-pruned reasons, idempotent journal CRUD/status query, legacy candidates, restore re-upsert, no automated row hard-delete.
- `apps/api/src/family_cfo_api/config.py` — retain legacy values as bootstrap-only inputs and add validated `FAMILY_CFO_BACKUP_IO_TIMEOUT_SECONDS`; active policy comes from the singleton.
- `apps/api/src/family_cfo_api/audit.py` / `undo_actions.py` — confirm new action names are explicitly classified; keep backup config irreversible and never snapshot credentials for undo.

### Backup core and storage

- New `apps/api/src/family_cfo_api/backup_retention.py` — pure policy/inventory/decision/status types and selector.
- `apps/api/src/family_cfo_api/backup_processing.py` — execution config, single global schedule plus cadence-independent maintenance, operation lock/deadline ownership, local inventory/capacity, pre/postflight, atomic writes, restore-settings preservation/review reset, reconciliation, journal, status assembly, and existing restore compatibility logic.
- `apps/api/src/family_cfo_api/worker_main.py` — run maintenance every worker interval independently of backup due/off state.
- `services/backup/src/family_cfo_backup/adapter.py` and `services/backup/tests/test_adapter.py` — accept/test bounded `pg_dump`/`pg_restore` subprocess timeouts while preserving command/error behavior.
- `apps/api/src/family_cfo_api/smb_backup.py` — strict typed listing, recognized filename parser, stable ordering, bounded/read-before-delete probes, process-local client gate, `stat_volume` capacity, partial upload and same-share rename, allowlisted friendly failures and sanitized logs.

### Server and contract

- `apps/api/src/family_cfo_api/schemas.py` — policy, capacity, destination/aggregate recovery schemas; config/check/list extensions.
- `apps/api/src/family_cfo_api/api/backups.py` — global config, one execution builder, `/backups/status`, strict remote state, capacity check, locks, system-admin wording.
- `apps/api/src/family_cfo_api/api/households.py` — derive only the deprecated off-box-day approximation from global settings.
- `shared/openapi/family-cfo.v1.yaml` — source-of-truth operation/types/enums/errors/deprecations.
- `VERSION`, `apps/api/BUILD`, `apps/web/BUILD`, `apps/ios/BUILD` — expected `0.160` contract/reset at current baseline.
- New `shared/openapi/compatibility/0.160.yaml` — immutable oldest-API fixture for the new contract.

### Backend tests

- `apps/api/tests/test_backup_processing.py` — replace flat-count cases; add execution-config parity, local pre/postflight, atomicity, reconciliation, and failure boundaries.
- New `apps/api/tests/test_backup_operation_lock.py` — process-local seam tests plus PostgreSQL advisory-lock exclusion, connection-loss, cancellation ownership, and loud missing-service behavior using two independent connections.
- `apps/api/tests/test_offbox_age_retention.py` — retire/rename old age-only expectations after compatibility bootstrap tests exist.
- New `apps/api/tests/test_backup_retention.py` — exhaustive pure selector/status cases.
- New `apps/api/tests/test_smb_backup.py` — strict inventory, capacity, redaction, probe, rename/cleanup.
- `apps/api/tests/test_backups_api.py` — global config/status, compatibility aliases, auth, audit, capacity, all status states.
- `apps/api/tests/test_backup_versioning.py` — preserve restore gates and cover known-incompatible inventory protection/status.
- Migration test location used by the repository — `0093` upgrade/bootstrap/downgrade cases.
- `apps/api/tests/test_ai_tools.py` — no backup-tool absence assertion; advisor non-goal is documentary.

### Web

- Regenerate `apps/web/src/app/api-client/`, including `sdk.gen.ts` and `types.gen.ts`; never hand-edit.
- `apps/web/src/app/core/api.service.ts` — status method and new request/response types.
- `apps/web/src/app/pages/backups/backups.ts` — drafts, mappings, validation, conflict handling, recovery state, refresh and stale clearing, system-admin naming.
- `backups.html` — independent retention/capacity controls and recovery card.
- `backups.scss` — responsive destination layout and accessible status treatments.
- `backups.spec.ts` — editing, mapping, all states, refresh, accessibility, compatibility behavior.
- `apps/web/src/locale/messages.lt.xlf`, `messages.vi.xlf` — translate every new primary string.

### iOS

- Regenerate `apps/ios/FamilyCFO/FamilyCFOShared/APIClient/Generated/Client.swift` and `Types.swift`; never hand-edit.
- `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupAPI.swift` — protocol/live method and expanded config draft.
- `BackupViewModel.swift` — destination drafts, validation, status/conflict/error isolation, refresh.
- `BackupSettingsView.swift` — controls, recovery disclosure, warnings, accessibility, removal of “last 7.”
- `FamilyCFOTests/BackupViewModelExportTests.swift` — update mock protocol conformance.
- New `FamilyCFOTests/BackupViewModelRetentionTests.swift` — focused config/status behavior.
- `FamilyCFO.xcodeproj/project.pbxproj` — modify only if Xcode does not auto-discover the test source.

## Verification matrix

Tests must select seams explicitly and run on contributor machines and CI; no test may infer behavior from the presence/absence of a NAS, binary, or runtime.

### Pure policy

- Exact inclusive/exclusive recent/daily/weekly/expired cutoffs.
- Outer cutoff inclusion; UTC calendar days and ISO Monday weeks; DST irrelevance.
- Equal timestamps with archive-key tie-break and multiple archives per bucket.
- 15-minute, hourly, six-hour, daily, weekly, off, and sparse histories without cadence-dependent policy changes.
- `keep_all`; equal adjacent horizons; invalid order/bounds.
- Future timestamps/clock skew.
- Only-newest and newest-expired inventories.
- Protected missing/unreadable/size-mismatch/orphan/unrecognized/known-newer items.
- Idempotent repeated planning.
- Logical cap after bucket selection; protected files excluded from its accounting; protected physical bytes defeating free-space reserve; newest larger than cap.

### Local processing and concurrency

- Stable `started_at/id` ordering and relative-path containment.
- Read-only status does not mutate; maintenance reconciles missing file with reason.
- Unreadable/size mismatch degrades without deletion; orphans protected.
- Every automated prune retains job row and reason.
- File-delete/metadata-failure then next-pass repair.
- Atomic partial promotion, caught-failure cleanup, stale-partial age/lock rule, crash after promotion.
- Capacity ok/warning/insufficient/unknown/`ENOSPC`; first backup with unknown estimate.
- Prospective cap never deletes last valid copy before replacement.
- Manual/scheduled/maintenance/restore/delete lock contention; scheduled skip versus manual 409; unconditional release.
- Independent maintenance while cadence is off/not due; review gate computes but does not prune; stale partial cleanup while lock is owned.
- Interrupted running rows before/after local promotion and rolling-upgrade grace.
- Cancelled HTTP waiter while the synchronous thread continues; pg/SMB I/O timeout; no early lock release.
- Production PostgreSQL advisory-lock exclusion/loss with independent connections, not only patched SQLite behavior.
- Restore fixture whose snapshot contains an older target/cap/journal: current settings survive, generations rotate, pruning pauses, source/interrupted jobs reconcile, and newer physical files remain protected.
- Existing encryption, archive round-trip, destructive restore audit, and version/schema tests remain green.

### SMB

Use deterministic fakes for every branch:

- Strict empty listing versus thrown unavailable listing.
- Recognized/unrecognized/partial filename handling and traversal protection.
- Stable mtime/filename order; job-started timestamp join and mtime fallback marker.
- `stat_volume` total/caller-available bytes; unsupported, auth, network, and missing-share errors.
- Allowlisted `_friendly()` behavior and sanitized logs, including an unknown exception containing synthetic UNC/user/credential-like text.
- Interleaved list/destination-check completion during upload/download and different-target credentials; the per-process gate prevents connection-cache reset races.
- Sufficient/insufficient/unknown capacity; insufficient skips remote upload but preserves completed local job; unknown attempts upload.
- Bounded alternating endpoint probes, partial/nullable count semantics, readable-oldest/unreadable-newest and unreadable-middle cases, and mandatory pre-delete probing outside the status budget.
- Remote policy and logical cap independent of local settings.
- Partial upload cleanup and same-share `rename`; conflict does not overwrite.
- Newest valid and every protected anomaly survive pruning.

A manual supported-NAS check verifies `stat_volume`, quota semantics, temporary upload, rename, list, and delete with credentials supplied through the user-controlled/platform credential path—not pasted into chat, tests, commands, or repository files.

### API, authorization, audit, and contract

- Unauthenticated 401 and non-system-admin 403 for config/status/check/list/create/restore/delete.
- System administrator access regardless of active household role; household roles cannot acquire `BACKUPS_MANAGE`.
- Global config/status identical from different active households.
- Omitted/null password preserves, explicit empty string clears only the password, and no raw credential enters audit/journal.
- Tokenless legacy save uses documented last-write-wins for legacy fields but cannot activate retention; tokened/confirmed save uses compare-and-swap; 409 changes nothing.
- Old `max_bytes` mapping; new-field precedence; alias null when caps differ.
- 422 policy validation and distinct 409 optimistic/operation-lock conflicts.
- Deterministic legacy conflict warning and clearing after explicit save.
- `not_configured`, `empty`, `healthy`, `constrained`, `degraded`, and `unavailable`; bucket-based `building/met/incomplete/shortened/unknown` coverage and overlap/aggregate precedence.
- No stale/misleading date when inventory unavailable; remote mtime source disclosed.
- Config audit changed-group summary with no values; journal destination generations, deletion timestamps, non-null idempotent event keys (including destination-wide events), explicit deletion, and later-success clearing of transient failures.
- Manual and scheduled routes build identical execution configuration; manual applies off-box policy; maintenance progresses independently.
- Local and remote restore preserve current operational settings, rotate generations, set review-required, and never resume restored pruning state automatically.
- Deprecated hosted-household day derivation.
- Runtime OpenAPI equals `shared/openapi/family-cfo.v1.yaml`.

### Migration and upgrade

Run migration/bootstrap tests against SQLite. Add a PostgreSQL 17 service to `.github/workflows/backend-api.yml` for the migration and production advisory-lock cases, pass its synthetic test-only URL through `FAMILY_CFO_TEST_DATABASE_URL`, and set `FAMILY_CFO_REQUIRE_POSTGRESQL=1`. On contributor machines without PostgreSQL, only explicitly marked PostgreSQL integration cases may skip; when the require flag is set, an unavailable service or skipped case is a hard failure. Add PostgreSQL 17 to the full-matrix tool list in `AGENTS.md`:

- Empty install receives `3/14/90`, daily, separate unlimited caps, 1 GiB reserves.
- Legacy count 7 and custom counts at every cadence; observed oldest archive extends horizon.
- Frequency `off` preserves observed archive horizon.
- Remote age 0 becomes keep-all; positive N <=3650 becomes N/N/N; larger/otherwise unrepresentable legacy state becomes keep-all plus review without bootstrap failure.
- Every upgraded install protects all existing managed copies until explicit activation; pending prune count/bytes is read-only.
- Shared cap copies to both destinations.
- Complete/latest/tie-broken household selection.
- Multiple distinct configs set conflict marker.
- Singleton creation race resolves to one row.
- No household foreign key on settings/events/jobs; destination generations rotate only on the defined identity/restore changes.
- Database values win after bootstrap even when legacy environment changes.
- Downgrade copies representable fields and documents tier/cap loss.

### Web

Extend `backups.spec.ts` for:

- Default/global policy rendering and migrated-conflict warning.
- Valid explicit save/activation, pending-prune preview, and invalid local-only intermediate state; blur does not activate/prune.
- Independent destination modes/caps/reserves and optimistic conflict.
- Every recovery/capacity/coverage state and timestamp caveat.
- Status refresh after backup, delete, destination check, and config save.
- Reverse-order pre-save/post-save success and failure completions; only current session/request generation mutates state.
- Token conflict preserves draft and requires reconciliation; auto-saves serialize/coalesce.
- Prior date cleared only by a current refresh failure; existing actions remain usable.
- System-administrator copy and removal of “last 7.”
- `role=status`/`role=alert`, textual equivalents, and narrow viewport.
- Generated types only; no hand edits.

Commands:

```bash
cd apps/web
npm run generate:client
npm test
npm run build
npm run extract-i18n
```

Merge/translate extracted messages and build/inspect Lithuanian and Vietnamese through the repository’s locale workflow.

### iOS

Focused view-model/view tests cover:

- Generated config/status mapping and date formatting.
- Horizon validation and independent destination values.
- Optimistic conflict and migrated warning.
- Every recovery/capacity/coverage state and remote timestamp caveat.
- Status refresh after mutations, reverse-order completions across save, and authenticated-session replacement; only owned current results apply.
- Token conflict preserves draft and serialized/coalesced saves do not replay stale state.
- Existing actions remain usable when current status is unavailable.
- VoiceOver labels/text and removal of “last 7.”
- Existing export tests after protocol expansion.

On macOS/Xcode, reuse the CI workflow’s available-device selection rather than hard-coding a simulator name:

```bash
scripts/generate-swift-client.sh
scripts/generate-swift-client.sh --check
cd apps/ios/FamilyCFO
sim="$(xcrun simctl list devices available \
  | awk -F '[()]' '/iPhone/ { udid = $2 } END { print udid }')"
test -n "$sim" || { echo "No iPhone simulator available"; exit 1; }
xcodebuild test \
  -project FamilyCFO.xcodeproj \
  -scheme FamilyCFO \
  -destination "id=$sim" \
  CODE_SIGNING_ALLOWED=NO
```

### API and compatibility commands

Provision the API environment exactly as `AGENTS.md` requires, then run targeted and full checks:

```bash
cd apps/api
uv venv --python 3.12 --seed .venv
make install
.venv/bin/python -m pytest \
  tests/test_backup_retention.py \
  tests/test_backup_processing.py \
  tests/test_backup_operation_lock.py \
  tests/test_smb_backup.py \
  tests/test_backups_api.py \
  tests/test_backup_versioning.py
make coverage
make lint
make check-openapi
```

From the repository root after both clients are regenerated:

```bash
scripts/check-compatibility-fixtures.sh
scripts/check-client-compatibility.sh web
scripts/check-client-compatibility.sh ios
scripts/check-versions.sh
```

The iOS compatibility check runs on macOS. In backend CI, run the same API suite with the PostgreSQL 17 service and `FAMILY_CFO_REQUIRE_POSTGRESQL=1`; the test URL is workflow-owned synthetic data, not a user-provided secret. Confirm generated directories have no hand edits and `0.160.yaml` is immutable after merge.

## Implementation and rollout order

1. Rebase/pin current `main`; revalidate issue state, migration head, `VERSION`, and component builds.
2. Complete and accept WI-1’s Spec Kit amendments and ADR.
3. Land the pure selector and exhaustive unit tests.
4. Add migration `0093`, singleton/journal/prune-reason models, repository bootstrap, and migration tests; in the same reviewable change, declare PostgreSQL 17 in `AGENTS.md` and provision the required service/loud-skip flag in `backend-api.yml`.
5. Add strict local/SMB inventory and capacity primitives, using public `stat_volume`/`rename`, with deterministic tests.
6. Add operation locking, atomic files, reconciliation, pre/postflight selection, and journal integration in `backup_processing.py`.
7. Switch scheduler and manual route together to one global execution configuration; remove the household loop and manual off-box omission in the same change.
8. Add Pydantic config/status models and server routes; update hosted-household compatibility derivation.
9. Update authoritative OpenAPI and server contract tests.
10. If baseline remains `0.159`, bump to `0.160`, reset builds to 0, and add the immutable compatibility fixture; otherwise select the next contract under ADR 0074.
11. Regenerate/implement/test web, including locale catalogs.
12. On macOS, regenerate/implement/test iOS.
13. Update environment/Compose comments, operator backup guide, API README, and database README.
14. Run all targeted/full API, web, OpenAPI, compatibility, Swift/Xcode, version, and fixture checks.
15. Release in contract-safe order: API and worker first, then web, then TestFlight/OTA iOS. Do not expose a dependent client before every served API supports the new contract.
16. Post-deploy, verify `/health` version, `GET /backups/config`, `GET /backups/status`, a real on-demand local backup, the configured SMB capacity/upload/rename path, and both client disclosures separately.
17. Close issue #116 only when the observed oldest-candidate dates, policy summaries, capacity states, and degraded paths are truthful in both clients and the new archive is restorable through the existing guarded workflow.
18. Schedule a later cleanup to remove legacy environment variables and household backup columns only after the compatibility window and rollback evidence are complete.

## Tradeoffs and rejected alternatives

- **Larger flat count:** simple but coverage still changes with cadence and gives no daily/weekly long tail. Rejected by the incident itself.
- **One fixed tier policy:** simpler schema but contradicts the operator’s explicit need to configure local/off-box horizons independently.
- **Environment-only tiers:** appropriate for startup-owned host paths/keys, but incompatible with existing authenticated client editing and cannot resolve household-selected whole-box destinations. Retain env only as bootstrap compatibility.
- **Keep household-scoped settings:** avoids migration but preserves ambiguous selection and untruthful global status. Rejected because archives/jobs are not household-scoped.
- **One shared logical cap:** preserves current schema but lets a local constraint unintentionally prune Synology and vice versa. Rejected by per-destination decision.
- **Derive “used bytes”:** convenient UI figure but misleading under SMB quota semantics. Expose total and caller-available bytes instead.
- **Decrypt/restore-test during status:** stronger evidence but expensive, secret-dependent, and potentially destructive. Status remains a bounded inventory/read probe with explicit limitations.
- **Delete anomalies to satisfy caps:** frees space but risks destroying the only evidence after rollback/corruption. Protect anomalies and surface constrained/degraded state.
- **Add ordinary advisor access:** would expose box-global system-administrator information through a household executor. Record a non-goal instead.
- **Cloud backup destination:** outside the local/self-hosted architecture and issue scope.

## Risks and mitigations

- **Legacy destination conflict:** deterministic migration plus persistent review warning prevents silent switching.
- **Singleton is a new pattern:** ADR, constant-key constraint, transaction-safe bootstrap, optimistic update, and race tests make ownership explicit.
- **SMB server variance:** public APIs exist in the full dependency range, but NAS permissions/quotas differ. Typed unknown/unavailable states, fakes, and one manual supported-share check prevent false certainty.
- **Remote timestamp precision:** prefer job `started_at`; disclose mtime fallback and degrade rather than claim exact capture time.
- **Cap versus horizon:** allow operator choice but show constrained coverage when the cap cannot meet target; never silently claim the target is met.
- **Protected anomalies consume space:** preserve evidence, surface the conflict, and require explicit administrator deletion after investigation.
- **Preflight race:** state that capacity is observational; atomic promotion and real errors remain authoritative.
- **Status latency:** execute synchronous SMB work in a thread pool, use the bounded alternating endpoint-probe budget, return partial qualification explicitly, and never decrypt every archive on page load.
- **Mixed client versions:** compatibility aliases plus contract bump/API-first rollout protect released pairings.
- **Rollback cannot express tiers:** keep old columns/inputs one window and document required downgrade settings; never promise restoration of deleted history.
- **Historic hard-deleted rows:** recovery status begins from observable current files/metadata and cannot reconstruct old size-cap deletions.
- **Lock integration complexity:** isolate lock acquisition behind a testable seam and use a dedicated connection so long I/O does not hold a row transaction.
- **Issue scope expansion:** global ownership, atomic promotion, strict inventory, and journal/locking are included only because without them per-destination retention and “oldest available” reporting can race or lie. Encryption, archive format, key recovery, and restore mechanics remain unchanged.

## Non-goals

- Per-household archives, destinations, or recovery lineage.
- Backup-key recovery/rotation changes.
- Archive format or restore-compatibility changes.
- Cryptographic/integrity validation of every archive during status reads.
- Scheduled destructive restore canaries.
- Automatic deletion of orphaned, unrecognized, anomalous, or known-newer archives.
- Cloud backup providers.
- Ordinary household advisor access to box-global backup state.
- A capacity guarantee after preflight.
- Reconstructing rows/files already removed by legacy size pruning.
- Removing legacy settings/columns in the initial compatibility release.

## Open questions

None require another product decision. Implementation must still validate, at its pinned head, the next migration/contract numbers, exact `smbclient` exception classes across the supported range, the existing Xcode test-file inclusion behavior, and the supported NAS’s quota/rename behavior. Each is a bounded execution check with the safe fallback already specified above, not a reason to change the design.

## References

- [GitHub issue #116](https://github.com/bobo-83/Family-CFO/issues/116)
- [GitHub issue #108](https://github.com/bobo-83/Family-CFO/issues/108)
- [PR #111](https://github.com/bobo-83/Family-CFO/pull/111)
- [smbprotocol PyPI releases](https://pypi.org/project/smbprotocol/)
- [`smbclient.stat_volume()` in the supported v1.12.0 source](https://github.com/jborean93/smbprotocol/blob/v1.12.0/src/smbclient/_os.py#L591-L612)
- [`smbclient.rename()`/`replace()` in the supported v1.12.0 source](https://github.com/jborean93/smbprotocol/blob/v1.12.0/src/smbclient/_os.py#L437-L470)
- `AGENTS.md`
- `docs/specs/README.md`
- `docs/specs/11-milestone-roadmap.md`
- `docs/guides/backup-and-restore.md`
- `docs/adr/0008-security-hardening-decisions.md`
- `docs/adr/0065-system-administrators.md`
- `docs/adr/0072-per-household-encryption-design.md`
- `docs/adr/0074-per-component-build-numbers.md`
