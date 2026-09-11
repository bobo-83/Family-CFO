# ADR 0077: Box-global tiered backup retention and recovery-window visibility

## Status

Accepted (2026-09-10, issue #116). This ADR amends the M8 flat-count backup
retention decision without rewriting the historical milestone record. It keeps
ADR 0008's operator-held backup key, ADR 0065's system-administrator boundary,
ADR 0072's whole-box archive design, and ADR 0074's contract-release rules.
Implementation is owned by the ordered work in
`docs/plans/backup-retention-recovery-window-2026-09-10.md` and must not begin
before this ADR and the Spec Kit amendments are accepted.

## Context

M8 assumed one backup per day and retained the newest seven completed local
backups. Backup frequency later gained 15-minute, hourly, six-hour, daily, and
weekly choices without changing that count. At a six-hour cadence, seven copies
span only about 36 hours. Issue #116 records the practical consequence: by the
time a stale-key incident was investigated, every pre-incident archive had been
pruned.

The mismatch is broader than the count. Every archive contains the whole
database and shared staging tree, and `backup_jobs` are box-global, but cadence,
SMB destination, credentials, and a shared byte cap currently live on household
rows. Scheduled selection can therefore depend on which household is visited
first, while a manual backup uses the acting administrator's active household.
There is no truthful singular policy or recovery window until ownership is made
global.

Current local and SMB retention also acts only after a final-name write or
upload, has no capacity observation, and can confuse an unavailable remote
inventory with an empty one. File deletion and metadata updates are not one
atomic operation. Concurrent backup, restore, or delete operations are not
excluded across API processes. A status page based only on configuration would
therefore promise more recovery depth than the visible files prove.

## Decision

### 1. One box-global persisted configuration

The active backup stream has one application-owned singleton configuration,
keyed by the constant `global` and carrying no household foreign key. It owns:

- frequency and the SMB destination/credential reference;
- independent local and off-box retention policies;
- independent local and off-box logical maximum sizes;
- independent caller-available free-space reserves;
- optimistic `updated_at`, migration/restore review state, activation time, and
  opaque destination generations used to scope journal causality.

Fresh installations use daily cadence, tiered `3 / 14 / 90` day policies for
both destinations, unlimited logical caps, and a 1 GiB reserve per destination.
A policy is either `keep_all` with null horizons or `tiered` with cumulative
horizons satisfying
`1 <= keep_all_days <= daily_until_days <= weekly_until_days <= 3650`.
Equal adjacent horizons deliberately disable the corresponding intermediate
band. Maximums are null or positive; reserves are non-negative.

The database is authoritative after the singleton exists. Existing household
columns and the legacy count/off-box-age environment settings remain for one
rollback-compatible release only as bootstrap inputs; normal reads stop using
them. API editing remains authenticated and persisted rather than moving to
startup-only environment values.

### 2. Deterministic UTC tier selection

Retention planning is a pure, synchronous function over an explicit UTC
`as_of`, an immutable inventory, one destination policy, and its logical cap.
It performs no filesystem, database, SMB, logging, or audit I/O.

For a tiered policy:

- recent `[as_of - keep_all_days, as_of]`: keep every eligible archive;
- daily `[as_of - daily_until_days, as_of - keep_all_days)`: keep the newest
  eligible archive in each UTC calendar date;
- weekly `[as_of - weekly_until_days, as_of - daily_until_days)`: keep the newest
  eligible archive in each ISO week (Monday 00:00 UTC);
- expired: strictly before the outer cutoff, delete unless the newest invariant
  protects it.

An archive exactly at the outer cutoff is in the weekly band. Stable ordering is
`taken_at DESC, archive_key ASC`; it selects the newest and each bucket winner.
After time selection, the same order supplies oldest-first logical-cap removal.
Policy results name `newest`, `recent`, `daily_bucket`, `weekly_bucket`,
`expired`, `bucket_superseded`, `capacity_limit`, or `protected_anomaly` so the
same inputs and `as_of` always produce the same auditable plan.

The newest eligible archive per configured destination is always preserved,
even when expired or larger than the logical cap. `keep_all` disables time
pruning but not the optional cap. Sparse cadence leaves empty buckets; the
system never synthesizes or borrows a backup from another bucket.

### 3. Strict inventories protect uncertain evidence

Local managed time is `backup_jobs.started_at`. A recognized remote filename
uses its matching job's `started_at` when available and otherwise SMB mtime,
explicitly marked `remote_modified_at`.

Only completed, present, nonzero, readable, compatible managed archives occupy
retention buckets. Missing local files are excluded and reconciled as pruned by
locked maintenance, never by the read-only status path. Unreadable,
size-mismatched, future-dated, future-version, orphaned, unrecognized, partial,
or otherwise anomalous files are protected from automatic deletion and excluded
from recovery claims. A recognized remote archive remains independently
eligible when its local row/file is absent or pruned.

SMB listing failures are typed unavailable results, never empty inventories.
Remote status uses a bounded alternating oldest/newest one-byte probe and
reports whether endpoint qualification is complete, partial, or unavailable.
Every remote candidate selected for deletion receives its own read probe outside
that status budget. Unknown `.enc` and `.partial` files are reported and never
managed as Family CFO archives.

Protected files do not enter the configurable logical managed-byte cap, but
their physical bytes can consume free-space headroom. The resulting state is
constrained or degraded; uncertainty never licenses evidence deletion.

### 4. Capacity is an observation, not a guarantee

Local capacity uses `statvfs` caller-available bytes. SMB capacity uses the
public `smbclient.stat_volume()` caller-available value so quotas and permissions
are respected. The contract exposes `total_bytes` and `available_bytes`, never a
derived "used" value that could conflate quota with physical use.

A `CapacityObservation` reports `ok`, `warning`, `insufficient`, `unknown`, or
`unavailable`, the configured reserve, a nullable next-backup estimate, nullable
acceptability, one UTC observation time, and safe reason codes/text. The estimate
is 110% of the largest of the three newest valid local completed archives,
rounded up to MiB; after local creation, SMB uses the exact ciphertext size.

Known capacity is insufficient when caller-available bytes cannot cover reserve
plus estimate (or is at/below reserve when no estimate exists). Warning uses a
second buffer equal to the larger of reserve and estimate. Unsupported capacity
is unknown, not zero; authentication/network/share failures are unavailable.
Unknown observations allow an attempted write, while actual `ENOSPC` or upload
failure remains authoritative. No preflight claims a guarantee because another
process or NAS client can consume capacity after observation.

Activated policies may remove already-disposable time/cap candidates before a
write, then re-query capacity. Local insufficiency fails the new job before the
dump. SMB insufficiency skips upload but preserves the completed local job and
records a stable remote failure. Retention, inventory, capacity, or journal
failure after local completion never rewrites that completion to failed.

### 5. Atomic lifecycle and one mutation owner

Local ciphertext is written as `{job_id}.enc.partial`, flushed and fsynced, then
promoted with same-filesystem `os.replace`. SMB uploads use a same-folder
`.partial` name and public `smbclient.rename`; an existing UUID final name is a
conflict, not an overwrite. Inventory ignores partials, caught failures clean
them best-effort, and locked maintenance may remove only unowned partials older
than 24 hours.

Backup, restore, retention maintenance, and explicit archive deletion share one
`BackupOperationLock`. PostgreSQL production uses a fixed session advisory lock
held on a dedicated connection for the full synchronous operation; SQLite tests
use an explicit process-local seam. Scheduled/maintenance work skips and
journals when busy, while manual conflicting mutations return 409. The existing
manual cooldown becomes box-global but remains separate from exclusion.

After acquiring the mutation lease, the operation owner assembles one immutable
`BackupExecutionConfig` from process paths/key/deadline plus the singleton's
cadence, destination, policies, caps, and reserves. No mutating caller may build
or retain an operational configuration snapshot before ownership. Manual and
scheduled creation call the same locked lifecycle; neither accepts separate
legacy retention arguments. Scheduling acquires first, loads the singleton and
latest completed job once, makes one box-level cadence decision, and runs at
most one job under that same lease—there is no household loop, duplicate
pre-lock due decision, or household-attributed target selection.

The synchronous operation owns the lock, not the HTTP waiter. Cancellation or a
client disconnect cannot release it while blocking I/O continues. The lease
also covers terminal job/remote-result writes, create/delete audit callbacks,
restore boundary and final audit work, and response-driving result/capacity
reads. Dump/restore and supported SMB operations have a positive configured I/O
deadline. Before a destructive or terminal phase the PostgreSQL adapter verifies
that its dedicated connection still owns the lock; connection loss aborts
further deletion, promotion, terminal writes, and finalizers. `SmbClientGate`
serializes each process's public SMB helper use through final connection-cache
reset because that cache is process-global.

A cadence-independent maintenance pass runs every worker interval even when
frequency is `off` or a backup is not due. Under the lock it reconciles missing
files/interrupted jobs, inspects partials/inventories/capacity, records health,
and applies retention only when activated. Every automated deletion preserves
the job row with a stable prune reason; retry after file-delete/metadata failure
is idempotent.

### 6. Upgrade and restore pause destructive policy

Bootstrap deterministically selects a legacy household configuration: prefer a
complete SMB configuration, then greatest `updated_at`, then ascending household
ID; distinct non-empty candidates set a conflict marker. It copies cadence,
destination/credential ciphertext, and the former shared cap into both new caps.

Every upgraded installation starts with `retention_review_required=true` and no
activation epoch. Automatic time/cap pruning is disabled, while status still
computes pending prune count/bytes. The legacy local count and observed history
are translated into a horizon large enough to preserve visible history; an
unrepresentable horizon becomes `keep_all`, never a clamp or bootstrap failure.
Legacy remote age `0` becomes `keep_all`; positive representable `N` becomes
`N / N / N`. Only an explicit, valid, optimistic system-administrator save with
`confirm_retention_policy=true` activates deletion. Confirmation does not prune
inside the request; the next independent locked maintenance pass applies it.

Restore deliberately preserves current operational configuration and encrypted
SMB credential in memory across whole-database rollback. It fully extracts the
archive's document tree into a same-filesystem sibling before changing live
state. SQLite archive files are migrated in that scratch location before live
promotion. PostgreSQL custom dumps cannot be migrated in place. Before taking
the rollback image, every destructive backend rotates the current destination
generations to an archive-external marker and pauses retention; the rollback
image therefore carries an identity that the older archive cannot reproduce.
PostgreSQL restores and migrates live under the lease, with compensating restore
from that image on caught failures. The restored database is brought to the
current schema, current settings are re-upserted, both destination generations
rotate, the review pause is re-enabled, source/interrupted rows are reconciled,
and reset events are journaled before the staged document directory replaces the
live tree.
The prior document tree and database rollback image remain available until the
post-restore audit callback, response-driving reads, and their post-read lease
certification all succeed.

Migration, database, finalization, document-extraction, and document-promotion
failures never report success. A caught failure after database mutation restores
the pre-request database and document pair, then verifies the prior schema and
archive-external settings marker. It durably reapplies the captured settings with
new generations and the review pause before returning a redacted error. If lease
ownership was lost, compensation first acquires a fresh exclusive lease; if
another owner already holds it, the failed operation performs no unowned rollback
and returns the distinct redacted operator-intervention failure instead. This
prevents one failed process from overwriting a successor's valid work. The
database and filesystem do not offer a shared transaction: process termination,
host/power loss between destructive steps, or lease loss followed by failed
reacquisition is not crash-atomic and requires operator recovery from a verified
backup. The coherent-pair guarantee covers completed calls and caught I/O,
migration, promotion, audit-finalization, and response-certification failures
while exclusive ownership is retained or reacquired.

### 7. Journal and recovery status tell only what is known

Automated pruning and operational failures use a box-global
`backup_retention_events` journal with no household foreign key. Events carry a
unique deterministic key, operation, destination generation, action/reason,
archive time/source/size when known, policy snapshot/revision, redacted detail,
and occurrence time. Retries and destination-wide events are idempotent.
Destination generations rotate when local path identity, SMB host/share/folder,
or restore state changes, so history from NAS A cannot describe NAS B.

Configuration is the target; `GET /backups/status` is the observation. For local
and off-box it reports configured state, destination/coverage status, policy and
review gate, pending prune totals, visible and nullable readable counts, probe
status/count, qualified oldest/newest dates, timestamp source, anomaly and
metadata counts, capacity, stable reason codes, and fixed verification scope
`inventory_read_probe`.

Destination state is `not_configured`, `empty`, `healthy`, `constrained`,
`degraded`, or `unavailable`. Coverage is `not_applicable`, `empty`, `building`,
`met`, `incomplete`, `shortened`, or `unknown`. Coverage evaluates the outer
intersecting UTC day/ISO-week bucket, not a continuous-success promise. `met`
requires a qualified candidate in that target bucket, a newer candidate, and
sufficient endpoint qualification. Retention/cap/explicit deletion causality can
make it `shortened`; mature sparse/failing history is `incomplete`; pending
review, probe/inventory uncertainty, missing causality, or protected anomalies
is `unknown`.

Overall status never lets healthy local storage hide an unavailable configured
Synology destination. Dates come only from qualified endpoints. Every API and UI
uses “oldest readable backup currently visible” or “recovery candidate,” not
“guaranteed restore point.” Inventory/read probing does not verify key
availability, authenticated decryption, archive integrity/completeness,
database migration, or destructive restore.

### 8. Security, audit, clients, and compatibility

Every configuration, inventory, status, destination-check, create, restore, and
delete route requires the box right `backups.manage`; household roles cannot
acquire it. Local paths resolve beneath the configured backup directory, and
remote inputs preserve basename/extension checks. APIs, logs, audits, and the
journal expose only allowlisted reason codes and redacted text—never SMB
credentials, raw exceptions, UNC paths, usernames, archive content, or financial
data.

Configuration audit remains an irreversible household-scoped administrative
action and names changed groups without values. Automated pruning belongs only
in the global operational journal. No undo snapshot includes credentials and no
undo claims to recreate a deleted archive.

OpenAPI remains authoritative. Configuration adds independent policies,
caps/reserves, review/activation and optimistic-write fields while keeping the
old shared `max_bytes` alias for one contract window. Destination check adds
capacity; remote list distinguishes unavailable from empty; status is additive.
New web and iOS clients preserve drafts on 409, reject stale session/request
completions, refresh status after mutations, and present matching localized,
accessible states. Policy/cap/reserve changes require explicit activation rather
than destructive blur autosave.

At the pinned baseline the first dependent client moves contract `0.159` to
`0.160`, resets component builds, and adds an immutable compatibility fixture;
if the baseline advances, use the next contract instead. API and worker deploy
before dependent web/iOS clients. Swift/generated Swift/Xcode changes and Apple
verification occur only on macOS with Xcode.

### 9. Ordinary household advisor access is out of scope

Backup configuration, inventory, and recovery status are box-global
system-administrator information. The M16 executor is household-scoped and has
no authenticated system-administrator context. Adding a read-only backup tool
would therefore violate the authorization model rather than satisfy the normal
visible-domain rule.

No ordinary advisor tool is added and no test asserts that a tool is absent. A
future system-administrator advisor needs its own authenticated box-global
executor and ADR.

## Invariant

For each configured destination, identical inventory, policy, cap, and UTC
`as_of` produce identical retention decisions; the newest eligible archive and
all uncertain evidence survive automatic pruning. Configuration is never
presented as observed recovery, capacity is never presented as guaranteed, and
only a lock-owning system operation may mutate box-global backup storage.

## Consequences

- Retention depth no longer collapses when cadence increases, while operators
  retain independent local/off-box control.
- A singleton, operational journal, capacity abstraction, and production lock
  add schema and implementation complexity, but remove competing sources and
  make destructive actions auditable.
- Upgrade/restore review can leave pending deletion work until an administrator
  confirms it; preserving evidence is preferred to silently enforcing defaults.
- Logical caps can shorten configured history, and protected files can consume
  physical headroom; clients must display constrained/degraded states rather
  than hide the conflict.
- Remote status can be partial or unavailable and may use mtime fallback. Honest
  uncertainty is preferred to a false empty or exact snapshot claim.
- Restore remains the only check of key, authentication, archive contents,
  migrations, and database viability.

## Rejected alternatives

- **Increase the flat count.** Coverage would still vary with cadence and would
  not provide a long daily/weekly tail.
- **Use one fixed tier policy.** It prevents operator choice and cannot express
  different local and Synology needs.
- **Keep household-scoped settings.** It preserves ambiguous selection for a
  whole-box stream and cannot yield truthful global status.
- **Store editable policy only in environment variables.** Startup settings
  cannot support the existing authenticated client-editing workflow or resolve
  household ownership. Environment values remain bootstrap-only compatibility.
- **Use one shared local/remote cap.** A local constraint could unexpectedly
  prune off-box history, or vice versa.
- **Derive used bytes.** SMB quota semantics make `total - available` an
  unreliable claim about physical utilization; expose caller-available bytes.
- **Decrypt or restore-test during every status read.** It is expensive,
  secret-dependent, and potentially destructive. Status stays a bounded
  inventory/read probe and says so.
- **Delete anomalous or unknown files to satisfy caps.** It can destroy the only
  evidence after corruption or rollback. Protect and surface them.
- **Let each HTTP request own the lock lifetime.** Cancellation could advertise
  false availability while synchronous I/O still mutates storage.
- **Expose backups to the ordinary advisor.** A household executor cannot safely
  authorize box-global system-administrator data.
- **Add a cloud backup provider.** It violates the self-hosted boundary and is
  unrelated to fixing retention truthfulness.
