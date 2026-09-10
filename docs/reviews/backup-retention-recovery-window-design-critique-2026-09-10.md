# Bounded design critique: backup retention and recovery window

**Date:** 2026-09-10  
**Disposition:** Resolve the qualification, coverage, migration, and lifecycle seams below before treating the plan as implementation-ready.

## Boundary and evidence

- **P:** `docs/plans/backup-retention-recovery-window-2026-09-10.md` (1–1035).
- **E:** `prompt-exports/oracle-plan-2026-09-10-080614-backup-retention-pla-df9d.md`, read completely. Only the generated response under `Generated Plan` / `Response` is the baseline (the response begins with `Issue #116`). The opening composed prompt and selection inventory are context, not baseline plan requirements.
- Code spot-checks were limited to archive creation/restore, SMB session/error handling, and configuration HTTP behavior. Checkout HEAD is `8cb662880b5ca423c10ba2e1b5886f3b2273a8ef`. P's asserted `b8bb86a21de2ddd74b7efe8b2c9ba0a144883cb2` object is unavailable locally; no remote fetch or issue revalidation was performed. Code findings below describe this checkout, not a verified diff against that asserted baseline.
- This review changes no plan, product code, generated client, or release state. It does not propose a different product scope.

## 1. Baseline fidelity: a small compatibility detail was weakened

Most implementation-bearing baseline content is retained or made more explicit. The move from used/free to caller-available capacity, the explicit irreversible config-audit classification, omission of a duplicated global policy from household responses, and fixed verification-scope disclosure are not omissions needing reversal. In particular, E's `integrity_verified=false` intent survives P's fixed scope and explicit integrity/key caveats; another equivalent boolean is unnecessary.

### F1 — Specify null-password preservation and legacy concurrency-token behavior

**Evidence:** E:368 explicitly says **null/omitted** password preserves the credential; E:379 makes `expected_updated_at` optional. P:178 only specifies omission, while P:186, 535, 564 add an optimistic token without specifying whether old requests may omit it. P's test matrix names explicit-null tests but does not define their expected result.

**Impact:** Implementers can accidentally clear credentials on a generated client's explicit null, or require a token that legacy config clients never send. Both conflict with the stated compatibility window. Current `api/backups.py:419–440` distinguishes null from an empty string: null preserves, while an explicitly supplied empty string clears the encrypted password. That existing empty-string behavior also needs an intentional mapping to the new explicit-clear operation.

**Precise correction:** Preserve the baseline's null/omitted rule; define empty-string handling and the clear flag's actual contract name/precedence. State whether absent update tokens use a documented legacy unconditional-write path, or whether old writes are deliberately unsupported. New clients should use compare-and-swap; a 409 must preserve the unsaved draft, reload the current token/config, and require deliberate reconciliation rather than blindly retrying the whole stale draft. Test all three password forms and tokenless old-client saves. Resolve this before WI-3/WI-5 interfaces are fixed.

## 2. Shared design contradictions and under-specified seams

### F2 — `coverage_status=met` conflicts with expiry and cannot describe a normal mature inventory

**Evidence:** E:481–486, 762–765; P §§3.2 and 8.2 (especially P:516).

P deletes archives strictly older than `as_of - weekly_until_days`, but calls coverage met only when the oldest candidate is **at or before** that cutoff. With multiple regularly produced archives, a healthy mature 90-day inventory normally has an oldest archive somewhat *newer* than the moving cutoff. Only an exact-boundary coincidence satisfies `met`; the newest-only exception can perversely mark a single expired archive as meeting coverage. Once the target has aged into existence, that mature inventory also does not satisfy the literal definition of `building`.

**Precise correction:** Define coverage against the actual discrete tier/bucket policy, not equality with the expiry instant. Keep the observed oldest date exact. Decide what constitutes sufficient coverage of the oldest partial ISO-week bucket and how sparse/off schedules affect expected coverage; then supply a total decision table for building/met/shortened/unknown. Do not silently extend retention to keep an extra pre-cutoff archive merely to satisfy the current predicate. Tests must advance `as_of` across daily/weekly cutoffs with an otherwise healthy mature inventory, and cover newest-only expired history.

Also define precedence when states overlap. P:507–511 currently allows an inventory containing only unreadable/protected files to look simply empty; P:524–525 allows empty local plus unavailable configured SMB to aggregate to empty. Neither should erase the anomaly/unavailability warning.

### F3 — The oldest-only read probe cannot establish the advertised candidate count, newest date, or safe deletion eligibility

**Evidence:** E:542–552, 728–730; P:273–274, 309–313, 479–483.

The SMB algorithm stops probing after finding the oldest readable file, yet the response claims a count and both endpoints of **readable** candidates. If A (oldest) is readable and B (newest) is permission-denied, this algorithm can count B and report its date without testing it. Retention also promises to protect unreadable archives, so treating all unprobed files as readable can delete the very anomalies P says to preserve. Conversely, treating every unprobed file as ineligible makes remote retention largely ineffective.

**Precise correction:** Separate listing-qualified from read-probed state. For fields that assert readability, qualify every contributing item with a bounded read probe; this still requires no decryption or full download. Alternatively, explicitly change count/date semantics to listing candidates and expose probe qualification separately, but that is a contract/UI decision, not an implementation assumption. Establish a bounded probe budget and unknown/partial outcome instead of making unsupported positive claims. Test readable oldest/unreadable newest and unreadable non-oldest deletion candidates.

Destination-specific eligibility must also be explicit: P's generic exclusion of `already-pruned`/orphan items must not disqualify a valid remote copy merely because its matching local row is pruned or absent after rebuild. The remote section correctly permits recognized names without metadata; qualify the generic rule as local-only where appropriate.

### F4 — Bootstrap horizon arithmetic does not deliver the claimed preservation, and valid legacy values can violate the new schema

**Evidence:** E §3.3 (E:430–438); P:89, 171–174, 205–218, 279.

Replacing `count × 7` with cadence/observed history is a reasonable correction to unnecessary horizon inflation, but neither formula alone preserves the existing oldest recovery candidate. For example, a legacy inventory of 100 daily archives produces an approximately 100-day outer horizon; the oldest archive can still lose to a newer archive in the same ISO-week bucket during the first pass. The plan's claim that the change “prevents immediate deletion” is therefore too strong. A mid-interval cadence change also means the selected cadence does not describe how older archives were produced.

Separately, custom count/observed history can compute a horizon above 3650, and a positive legacy remote age can also exceed 3650. Both inputs are then incompatible with the mandatory new tier bounds. Silently clamping would shorten history; throwing would prevent singleton bootstrap and backup/status operation.

**Precise correction:** Define whether upgrade safety preserves the oldest existing candidate, every existing archive for a transition period, or only the configured theoretical horizon. If preserving the oldest date is intended, add bounded one-time protection for that boundary candidate rather than restoring an ongoing count floor. Choose an explicit non-destructive overflow path (for example, keep-all plus mandatory review when legacy values cannot be represented), and test same-week oldest replacement, cadence changes, and above-limit legacy values. The stronger protection, if selected, must be represented in WI-2/WI-3 rather than left to prose.

### F5 — Recovery causality requires destination identity and deletion facts missing from the journal

**Evidence:** E §3.8 and §3.9; P §§7 and 8, particularly P:449–458 and 514–518.

The journal records only `local | offbox`, an archive key, an optional job ID, policy snapshot, and event time. It does not retain the deleted candidate's snapshot timestamp or an opaque destination generation. After switching from NAS folder A to B, a recent cap event from A can falsely mark B shortened. For a remote file without a retained job row, deletion removes the only timestamp evidence needed to determine whether the deleted candidate was inside today's target. “Recent” is not a defined horizon, and an arbitrary event limit can lose relevant causality or preserve obsolete warnings indefinitely.

The idempotence promise also has no durable event identity/unique key; a fresh UUID per retry does not deduplicate events. Explicit hard deletion may erase the local row while recording only a household audit event that the global status service does not read.

**Precise correction:** Associate observations/events with an opaque destination generation (no raw target or credentials), retain the minimal deletion-time candidate facts needed for coverage reasoning, define event identity/deduplication, and specify which policy/destination epochs and time range status considers. Include explicit deletion in that reasoning without relying on household audit visibility. Define how a later successful pass clears transient failure degradation. Test target changes, policy expansion/contraction, missing remote metadata, duplicate retries, and explicit deletion before finalizing WI-3's schema.

### F6 — Logical-cap accounting contradicts anomaly protection language

**Evidence:** P:275 excludes protected anomalies from the logical cap, but P:92, 383, 509 and the verification matrix say protected anomalies can prevent that cap being met.

**Precise correction:** Choose one accounting definition. The simpler design consistent with P:275 is: logical cap counts only eligible managed archives; protected bytes can defeat the **physical free-space reserve**, not that logical cap. Correct the contrary status/tests/copy accordingly. If recognized-but-protected archive bytes must count toward the logical cap, include them consistently in the sum without making them deletable. Unknown foreign files should not become managed bytes merely to simplify accounting.

Likewise, P:349's warning threshold (“one additional reserve or estimate”) needs one formula and null/zero cases. Otherwise status, destination-check, and clients can disagree at the same observed capacity.

## 3. Focused code-disproved assumptions

### F7 — `_friendly()` is not a redaction boundary today

**Evidence:** E §§3.5–3.6, 3.12; P §§5.1 and 11. `apps/api/src/family_cfo_api/smb_backup.py:47–56` returns `f"SMB error: {exc}"` for unrecognized errors. The same file logs raw exceptions at 114–115; `backup_processing.py:369` also logs a raw SMB upload exception.

**Impact:** Passing all new errors through the named helper does not satisfy the plan's no-path/user/raw-exception disclosure requirement. This is not merely an exception-class verification task.

**Precise correction:** Explicitly change `_friendly()` to a safe allowlisted classification with a constant redacted fallback, and remove raw exception interpolation from the touched backup logging paths. Test an unknown exception containing synthetic UNC/user/credential-like text, not only known authentication messages. Keep the no-secret guarantee; do not remove it because the existing helper violates it.

### F8 — Correct the “plaintext is never written” invariant, without expanding encryption scope

**Evidence:** E:271; P:76. `backup_processing.py:330–334` dumps to `TemporaryDirectory()/database.dump` and reads those bytes before encryption. `services/backup/src/family_cfo_backup/adapter.py:54–57` writes the pg_dump output file; its SQLite counterpart copies the database at 96–97.

**Precise correction:** Say that no **final backup archive** is persisted unencrypted and missing-key validation occurs before the dump. A plaintext temporary dump already exists; eliminating it would be an encryption/pipeline redesign not required here. Also scope capacity claims to the filesystem actually queried: observing `backup_dir` does not preflight a different filesystem used by `TemporaryDirectory`. Preserve ordinary temporary-dump write failure handling; do not imply the new encrypted-archive estimate covers all scratch space.

## 4. Requirements absent from both documents

### F9 — Concurrent status/check calls can close a mutation's SMB connection

**Evidence:** Both documents introduce concurrent thread-pool status work but lock only mutation operations. `smb_backup.py:38–44` registers sessions without a scoped cache, and every verify/upload/list/download/delete helper resets the default connection cache in `finally` (75, 90, 117, 127, 139).

**Impact:** A status listing or destination check finishing in the API process can reset connections while another thread is uploading or downloading a restore. The new cross-process mutation lock does not protect that read/check interaction. The risk follows directly from the shared-cache cleanup, even when both calls use the same target.

**Precise correction:** Give SMB connection lifetime explicit ownership. A small serialized SMB-helper boundary within each process is a simpler safe alternative to redesigning the library integration; alternatively use genuinely isolated operation-owned sessions/caches. Never globally reset another operation's connections. Test interleaved list/check completion during upload/download and different target credentials. This belongs in WI-4 before moving the routes off the event loop.

### F10 — Maintenance, cancellation, and lock ownership need an executable lifecycle

**Evidence:** E §3.7; P §§4.3, 6.3–6.4 and execution flows. P calls for “locked maintenance” deleting partials only when **no operation owns the lock**, while the cleaner itself must own it. No independent maintenance trigger is named; the sole scheduled flow returns when cadence is off/not due. Current `adapter.py:54–78` uses subprocesses without a timeout.

**Precise correction:** State that cleanup acquires the mutation lock non-blockingly and deletes eligible stale partials while **it owns** the lock. Name whether maintenance runs independently of backup cadence, or explicitly accept that off/not-due schedules defer cleanup, reconciliation, and policy application. Identify who observes crashed running jobs: completed-only inventory cannot reconcile their promoted files/rows by itself. A minimum safe policy is to surface them as interrupted/protected and never silently certify them as complete.

Thread-pool dispatch is not a cancellation strategy. The synchronous worker must retain the lock until its real I/O ends, even if the HTTP waiter is cancelled. Define bounded I/O/deadline behavior and lock-loss handling; do not release the lock on request cancellation while a background write still runs. Test cancelled HTTP waits, stuck/failing I/O, crash-after-promotion, and scheduled contention through the seam. Separately test the production advisory-lock adapter's acquire/release with independent connections: a patched SQLite lock cannot establish cross-process PostgreSQL exclusion.

### F11 — Restoring the database also restores the policy and journal that now control external-file deletion

**Evidence:** Both designs move authoritative configuration/journal into the whole-box database without a post-restore ownership rule. `backup_processing.py:326–343` snapshots a running job before completion metadata exists; `backup_processing.py:512–530` restores the database and then migrates it. `adapter.py:65–82` uses `pg_restore --clean --if-exists`. The HTTP path already treats post-restore identity specially (`api/backups.py:357–370`).

**Impact:** Restore rolls policy, destination credentials, and journal evidence back with the financial database, while the physical archives remain newer. This can reactivate an old target or more aggressive cap on the next worker pass. The restoring archive's own job can reappear as running, and later files become orphaned. Restoring an older schema may also invoke singleton bootstrap again. A lock only prevents concurrent mutation; it does not choose the settings that become authoritative afterward.

**Precise correction:** Decide whether restored backup configuration becomes authoritative automatically, requires review before automatic pruning resumes, or whether current operational configuration is deliberately preserved through restore. Do not silently choose preservation, which would change existing whole-database restore semantics. Specify reloading/invalidation and post-restore status for interrupted jobs and vanished journal evidence. Add a focused restore fixture with a changed target/cap and newer physical files. This decision affects WI-3 bootstrap and WI-4 execution ordering, not just operator documentation.

### F12 — Client responses need ordering and context ownership, not only stale clearing on failure

**Evidence:** E §§3.10–3.11 and P §§9.1, 10.1 require refresh after several mutations and clearing on failure, but do not define ownership of overlapping completions.

**Impact:** Slow pre-save status A can finish after post-save status B and overwrite B's recovery date/policy. Likewise, an older failure can clear newer successful status. `@MainActor`, Angular signals, and cancelling the waiter do not by themselves make completions current. Configuration autosaves can also race using the same optimistic token.

**Precise correction:** Use request-generation/config-version checks and authenticated-session ownership before applying config/status results; serialize or deliberately coalesce valid autosaves. Invalidate pending results when the screen/session changes. Tests should complete requests in reverse order, cross a config save, and change the authenticated session. Status failure isolation remains correct, but must apply only to the still-current request.

## 5. Questions that materially change design or order

1. **Upgrade promise (F4):** Is preservation about the configured horizon, the oldest currently available candidate, or all existing copies during transition? This decides whether WI-2/WI-3 need one-time archive protection.
2. **Coverage meaning (F2–F3):** Does `met` mean tier-bucket coverage, and must counts/newest dates certify a read probe for every included file? This determines the status contract and SMB work budget before WI-5 or client generation.
3. **Restore ownership (F11):** Should restored operational settings resume automatic pruning immediately, require review, or be preserved separately? Decide before schema/bootstrap and restore integration.
4. **Maintenance lifetime (F10):** Must retention/reconciliation progress when backup frequency is off or no backup succeeds? If yes, an explicit maintenance trigger and its locking/error tests are dependencies, not an incidental helper.
5. **Compatibility writes (F1):** Are tokenless old-client config writes supported during the alias window? If yes, document their weaker concurrency semantics; if no, change the compatibility claim and rollout gate explicitly.

The remaining corrections are engineering consistency decisions, not reasons to ask the operator to redesign the feature. Preserve the plan's accurate concrete interfaces, atomic promotion, independent destinations, authorization boundary, and verification detail while resolving these specific gaps.
