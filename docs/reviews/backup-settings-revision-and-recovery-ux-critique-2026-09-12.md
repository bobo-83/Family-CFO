# Bounded critique: backup settings revision and recovery UX

## Scope and verdict

Reviewed checkout: `8871a45917a35f9452a2ef81c5a5308c0781608e`, branch `fix/issue-116-tiered-backup-retention`. This is a planning/correctness review, not an implementation, release, or certification of a distributed TestFlight build. No source or plan edits, tests, deployment, or GitHub mutations were performed.

Inputs:

- **P:** `docs/plans/backup-settings-revision-and-recovery-ux-2026-09-12.md` (1–1160).
- **B:** `prompt-exports/oracle-plan-2026-09-12-092621-backup-cas-plan-1dd3-2126.md`, generated response beginning at line **136**, ending before the continuation footer at line 1236. The entire export was read; its preceding prompt and selection were **not** treated as preservation requirements.

**Preservation result: no implementation-bearing baseline omission or weakening found.** A direct comparison preserves the detailed contract matrix, migration/backfill, all-writer coverage, restore invalidation, queue/reconciliation rules, feedback, recovery predicate, tests, rejected alternatives, and execution items. The changed passages add provenance/non-goals, resolve provisional filenames with revalidation, expand build commands, and strengthen pre-merge deployment evidence. The web build requirement is not lost: `scripts/check-web-i18n.sh:113–136` runs the production build. Do not shorten these accurate specifics merely to make the document less detailed.

**Readiness: resolve the P1 findings before implementation; settle the P2 transitions/dependencies in the relevant spec gate.** Most substantive gaps below are inherited from B, not losses during transcription. JSON UUID CAS, strict legacy comparison, presentation-only review status, and the no-synchronous-pruning boundary remain sound choices; no generalized settings framework or second concurrency authority is needed.

## Severity-ranked findings

### 1. P1 — A successful PUT must not silently adopt another writer's revision

**Classification:** requirement absent from both; response ownership/concurrency seam. **Plan:** §§2.3, 3.4–3.7; P:358–367, 420–421, 523–529.

The plan retains an authoritative reload after persistence and instructs both queues to use every successful response's revision for the next snapshot. Those two rules are unsafe together unless the returned configuration is tied to this mutation.

**Current-code evidence:** `apps/api/src/family_cfo_api/api/backups.py:710–731` releases the operation lock when `persist()` returns `before, after`; lines 745–760 write the audit and call `_load_backup_config` afterward. `_load_backup_config` reads the row again at lines 220–224. Swift adopts the response token while baselining the sent operational snapshot (`BackupViewModel.swift:475–499`); web adopts the response without replacing visible drafts (`backups.ts:508–515`).

**Failure sequence:** A commits R0→R1; B commits R1→R2 before A's response reload; A receives R2 and drains its already-queued activation/operational snapshot using R2. B's accepted destination/policy change can now be overwritten without a conflict. The string token fixes timestamp precision, not this laundering of an unreviewed revision into A's queue.

**Bounded correction:** make the PUT configuration fields and token describe its committed `after` record, using a small response-adapter seam that can also serve GET. Independently calculated preview metadata must not replace that record's revision or policy values. Alternatively, explicitly detect an intervening revision and enter reconciliation rather than draining automatically; returning the committed record is simpler. Preserve the audit/redaction and operation-lock behavior.

**Required test:** deterministically pause A between persistence and response construction, commit B, then resume A with another snapshot queued. A's next write must use R1 and conflict with R2, never silently overwrite B. Cover activation as the queued write.

### 2. P1 — Pending and blocked activation consent needs its own session/lifetime owner

**Classification:** code-disproved current-state assumption and requirement absent from both. **Plan:** P:488–512, 530–548; especially “a new load or session replacement invalidates … queue generations as today” at line 545.

Request-completion guards are not ownership for work that has not started. The proposed pending/blocked shapes carry payloads but no initiating session/lifetime/intent identity. Preserving a failed activation unconditionally can retain it across the very boundary that should invalidate permission to submit it.

**Current-code evidence:** `BackupViewModel.swift:173–181` intentionally retains box-global configuration on session replacement and only calls household-sensitive invalidation; that helper at lines 184–213 does not invalidate the save queue. `load()` at lines 279–307 invalidates conflict/config requests, not `saveQueueGeneration`. Only `endViewLifetime()` explicitly clears the queue/password and advances the queue generation (lines 147–170). `performSave()` captures the active session when it starts, not when its snapshot was enqueued (lines 439–440, 460). The view reloads on session changes (`BackupSettingsView.swift:26–34`).

**Failure sequence:** session A has an in-flight save plus pending activation; session B replaces A and loads configuration; the A completion is rejected, but the new preserve-on-failure logic retains A's activation. A later B retry can submit it with B's authentication and freshly loaded token. Box-global configuration may be shared; destructive confirmation and unsent password input are not transferable user intent.

**Bounded correction:** stamp pending, in-flight, blocked, and reconciliation work with a session epoch, presentation/queue epoch, and intent identity at enqueue time. Check that owner before starting a request, adopting a conflict fetch, retrying, and publishing feedback. Explicit session/lifetime invalidation discards old pending/blocked consent and password snapshots; it is not a retryable save failure. Scope “never clear pendingActivation” to recoverable failures in the same owner. Define separately what an explicit reload/restore does to unsent work; do not rely on “as today.”

**Required tests:** A→B and A→B→A with delayed saves and conflict GETs; disappearance/reappearance while a server-accepted write completes; reload/restore while activation is queued. No old activation may be issued under a replacement owner, and old cleanup must not clear a newer owner's busy state. A cancelled caller in a still-current presentation must produce either a deliberately retained, reload-before-retry intent or an explicit discard—not an accidental unconditional replay.

### 3. P1 — The staged box/TestFlight gate does not yet bound publication, destructive verification, or schema rollback

**Classification:** added rollout detail with missing safety dependencies; rollback gap also exists in B. **Plan:** §4.6 (P:827–838), §7, WI-8.

The exact-head/runtime/build acceptance record is a useful addition and should remain. Three operational choices still materially affect the design/order:

1. **TestFlight upload is not TestFlight-only.** `scripts/release-testflight.sh:169–175` refreshes the box's OTA bundle by default. The plan's bare command therefore publishes an additional client artifact before its staged acceptance record is complete. Use `SKIP_OTA=1 scripts/release-testflight.sh` for this bounded candidate gate, or explicitly include OTA publication, identity, audience, and verification in the approved rollout. A processed build and a tester-group distribution decision are separate gates; establish the intended test audience rather than assuming upload is contained.
2. **“Real upgraded box” plus manual steps 7–13 includes a database restore and destructive-policy activation.** Specify whether this is an isolated test box with synthetic archives, or an operator-approved live box with a maintenance/recovery plan. `BackupSettingsView.swift:103–112` correctly describes restore as replacing all data. No pruning *inside* activation does not mean no independent maintenance pruning afterward: the existing activation hint explicitly permits pruning on the next pass (`BackupSettingsView.swift:302–303`). Do not make a production restore/fault injection a routine implicit acceptance step; use the isolated migrated box for destructive cases, and narrowly bounded live persistence checks if needed.
3. **Prior image tags are not a database rollback procedure.** P:241–247 adds a non-null column with no server default; old code neither supplies nor advances it. API startup runs `alembic upgrade head` (`docker/entrypoint-api.sh:17–18`), while the old image does not contain migration 0095. A return to old images is not demonstrated to start successfully against the upgraded schema, even before new clients ship. Specify quiescing old writers during migration, and either a tested downgrade using migration-aware tooling before old-code restart or a verified pre-migration recovery path, including treatment of writes accepted after upgrade. After client distribution, stopping further distribution alone does not remove already-installed revision-dependent clients.

Also pin source-build versus image-pull mode explicitly for the candidate. `scripts/patch.sh:39–53,192–220` honors image-tag overrides; a clean checkout and matching marketing version alone do not establish that its code was deployed. Record image identity/build provenance alongside runtime version without dumping configuration or secrets.

**Required verification:** rehearse upgrade and the chosen rollback on a disposable upgraded database; verify old/new-client behavior during each stage; retain the existing exact-commit/API/web/TestFlight acceptance linkage. No deployment is authorized or performed by this critique.

### 4. P2 — Blocked-save recovery is incomplete when reconciliation fails or the failure is not a conflict

**Classification:** under-specified state machine in both. **Plan:** P:428–455, 530–558, 641–646.

`blockedSave.current` is optional, but Retry requires that fetched revision. Neither document says how to proceed when that GET fails, how its failure is surfaced, or when Retry/Use current becomes enabled. The generic “fetch separately” rule also conflates validation, authorization, busy conflict, transport ambiguity, and ownership cancellation.

**Evidence:** today's Swift conflict fetch deliberately swallows its own error and leaves reconciliation data absent (`BackupViewModel.swift:518–535`). Web only installs its conflict state when a GET returns data (`backups.ts:537–562`). These are not complete reusable blocked states. Current web retry bypasses `ensureSaveLoop` (`backups.ts:568–583`), so adapting that method without a lane rule preserves a second submission path.

**Bounded correction:** define a small transition table for blocked-without-current / refreshing / ready-to-retry. Preserve drafts when GET fails, expose “Reload current settings,” and disable revision-rebased retry and Use current until an owned GET succeeds. Edits while blocked must not silently restart the loop. Treat 401/403 as authentication/permission recovery, not repeated mutation retries; 422 must permit fixing the draft; a lost response requires reconciliation; owner invalidation follows finding 2. Route every Retry through the same serialized lane and coalesce/disable duplicate taps. CAS only rejects duplicates using the *same* stale token; sequential retries rebased onto new responses can produce multiple accepted activations/audits.

**Required tests:** failed PUT + failed GET + successful explicit reload; edits while blocked; two Retry taps; Retry racing autosave; 401/403/422/428 handling; cancellation before versus after server acceptance. A required-but-missing new token should never silently downgrade a new client to legacy tokenless writes.

The optional payload-match shortcut (P:552–558) should say “the box now matches this non-secret draft,” not claim proof that this request committed or its audit succeeded. Another writer can reach the same state. A named simpler equivalent is to omit this optional inference and use the already-required explicit reconciliation path; no idempotency service is needed.

### 5. P2 — Exact retained snapshots, later edits, and actual activation controls are not reconciled

**Classification:** unresolved decision/testability in both. **Plan:** P:450–452, 515–541, 732–742, manual step 8.

Both documents require retrying the exact failed activation, but only define how later **operational** edits update it. They do not define retention edits made while blocked, or a second explicit activation captured while an earlier activation is in flight. A blind requeue of the older failed item can replace a newer confirmed item; a success can clear the wrong pending item. Retrying an older tiered policy while the form displays a newly edited keep-all policy is especially misleading for destructive consent.

**Bounded correction:** retain an immutable sent snapshot plus an intent ID; only that ID may be acknowledged/removed. Specify whether a later confirmed activation supersedes an earlier one. Unconfirmed retention edits must remain unsent; Retry must either show which preserved policy it will activate or require a new confirmation for the revised policy. Adjacent success must not claim the current control value is saved if it changed since the acknowledged snapshot. Preserve pending password intent only in memory and clear only the acknowledged password generation, not a newer edit.

**Current UI contradiction:** queued activation is specified as a manual acceptance scenario, but `BackupViewModel.swift:233–236` disables activation while saving, and `BackupSettingsView.swift:291–303` uses that predicate. Web similarly disables the button during `configSaving()` or conflict (`backups.html:484–486`). Direct ViewModel/component method tests can exercise queuing, but do not prove the advertised user interaction is reachable.

**Material decision:** deliberately allow one explicit activation to be queued behind operational work, with a visible pending state and duplicate protection, or keep the current disabled controls and describe the queue regression as a defensive/event-ordering test rather than an ordinary mid-save tap. Focus-loss plus activation can still create a same-interaction ordering case; specify it instead of assuming a disabled button is tappable. Add a real view/template interaction test, not just a method-level test, for the chosen policy.

### 6. P2 — OpenAPI-first order and verification ownership need concrete correction

**Classification:** contradiction/dependencies in both, plus executable-gate issues in P. **Plan:** P:374–396 versus 1073–1077 and WI-2→WI-3; §§4.1, 4.3–4.4 and file impact.

- §3.5 says authoritative YAML first, but the execution order and WI-3 dependency put it after API implementation. Resolve this explicitly: accept/update the authoritative schema before adapting Python, while retaining a coordinated publication boundary for API, fixture, generated clients, version, and build metadata. Do not publish a required-field API shape as an intermediate `0.160` release. `docs/specs/README.md:5–21` and `AGENTS.md:25` establish the spec/source-of-truth boundary.
- Migration/backfill tests are assigned chiefly to `test_backup_settings_repository.py`, whose restore helper test directly calls a re-upsert (`:511–537`). That does not exercise a real old-schema archive, migration, rollback verification, and compensation. Name `apps/api/tests/test_migrations.py` for actual upgrade/downgrade/backfill and `test_backup_lifecycle.py` for restore failure/compensation. Existing seams include cancellation/lock coverage at `test_backup_lifecycle.py:163–190`, migration failure at `:771–822`, and rollback-marker verification at `:837–890`. Verify fresh revisions after both successful restore and verified compensation, while preserving the preimage comparison in `backup_processing.py:1903–1919`. This need not expand restore production behavior.
- State which new migration/CAS tests run on real PostgreSQL and SQLite, not merely that PostgreSQL is installed. Reuse the existing CI-required PostgreSQL lane (`.github/workflows/backend-api.yml:36–52`) and its explicit `FAMILY_CFO_REQUIRE_POSTGRESQL`/test-database configuration. The exact local gate block should not silently skip that lane when claiming full validation.
- On a clean machine the listed compatibility commands precede `npm ci`, but the web compatibility script runs npm generation/build without installing dependencies (`scripts/check-client-compatibility.sh:43–48`). Put dependency bootstrap before those gates.
- P:776's “macOS/Xcode **or a compatible Swift toolchain**” contradicts the repository's prohibition on generating/modifying Swift/iOS code from Linux (`AGENTS.md:21–25`). Both the iOS compatibility command, which temporarily regenerates files (`check-client-compatibility.sh:31–54`), and Swift drift generation belong in the macOS/Xcode lane.
- ViewModel unit tests do not themselves prove rendered picker labels, focus submission, accessibility announcements, or control-adjacent feedback. Name the view-level/manual seam for those requirements rather than declaring all of them covered by `BackupViewModelRetentionTests.swift`. Also include the dashboard XLIFF catalog updates for new copy; the existing i18n gate requires translated locales (`scripts/check-web-i18n.sh:128–133`).

### 7. P2 — Nail down the remaining protocol/presentation edge definitions rather than letting implementations diverge

**Classification:** small implementation-bearing ambiguities in both. **Plan:** P:181–212, 341–348, 616.

- **No-op:** does this mean only an empty normalized patch, or also a nonempty patch equal to current values? Current repository behavior returns early only for an empty patch (`repository.py:4981–4989`); a nonempty equal patch advances the timestamp. Keep that narrower definition unless a behavior change is intended. Specify whether the existing reviewed-with-no-value-change audit remains (`api/backups.py:745–760`), and test no-op with current, stale, and dual tokens.
- **Precondition validation:** choose exact request string bounds and omitted/null handling. Define 422-versus-428 precedence when both confirmation and tokens are missing, and ensure the legacy `max_bytes` alias still follows the tokenless equal-cap exception rather than being swept into the new-cap precondition rule. Current validator/adapter seams are `schemas.py:1947–1971` and `api/backups.py:686–693`. Add table-driven API cases; retain exact comparison and dual-token AND.
- **Overall review badge:** “every configured non-healthy destination is review-only” is vacuously true when there are none. Require actual overall API `degraded` plus at least one review-only destination, then the existing no-other-fault condition. Test all-healthy, off-box-not-configured, unknown extra reason, and review plus constrained/unavailable. This tightens presentation only; do not alter recovery enums or reason generation.

### 8. P3 — Correct the runtime provenance reference; do not re-open the diagnosed root cause

**Classification:** incorrect reference introduced by P, not baseline preservation loss. **Plan:** P:16.

The link labeled as the pinned runtime uses `1.7.0`, but `apps/ios/FamilyCFO/FamilyCFO.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved:49–55` pins **1.12.0**, revision `3d3a8457661daf7fb260ceeb9f0e24e5204ba5fb`. The investigation also identifies 1.12.0 (`docs/investigations/backup-retention-activation-recovery-warnings-2026-09-11.md:41,141`). Point the provenance to that pinned version/investigation, or label 1.7.0 as historical illustrative documentation rather than current evidence. No external runtime behavior was re-audited here, and this does not undermine the generated round-trip diagnosis or justify a global transcoder change.

## Questions that materially change design or order

1. Will PUT return its own committed configuration record, or explicitly reconcile if its later reload sees another revision? This must be settled before API and queue implementation (finding 1).
2. Which boundaries discard unsent activation consent—session replacement, household switch, explicit reload, restore, disappearance—and what, if anything, remains as an unconfirmed visible draft? Box-global storage alone must not decide intent ownership (finding 2).
3. While blocked, is Retry permission to resend the displayed preserved snapshot or permission to confirm a newly edited policy? Can a newer confirmed activation supersede the failed one, and can the real UI queue it behind autosave (findings 4–5)?
4. Is the pre-merge box disposable/synthetic or live, who receives the candidate TestFlight build, and is OTA deliberately excluded? What tested schema/data rollback is available before the first migration and before distributing new clients (finding 3)?

These questions require bounded decisions, not a rewrite. Preserve the baseline's accurate implementation detail and fold the answers into its existing contract, queue, verification, and rollout seams.
