# Investigation: Backup Retention Activation and Recovery Warnings

## Summary

The Synology-retention and schedule edits fail for the same reason: the API's microsecond `updated_at` optimistic-concurrency token is modeled as `Foundation.Date`, and the generated iOS transport sends it back at whole-second precision. The backend correctly rejects both PUTs with HTTP 409, so activation never clears the intentional migration-review gate; the warnings and both `Degraded` recovery cards therefore remain. “Back up” is a separate literal-copy defect, and the save UI makes the 409 easy to miss.

## Symptoms

- On the Retention and capacity screen, the UI shows “Automatic pruning is paused until a system administrator reviews and activates this policy” and “Older household settings differed. Review the box-global policy before activation.”
- Changing the Synology retention mode and tapping “Save and activate retention” appears not to take effect.
- The schedule label says “Back up” rather than the requested noun “Backup,” and schedule edits appear not to take effect.
- Recovery window cards show “On this box: Degraded” and “Synology: Degraded.” The local card reports `Coverage: Unknown`, `Capacity: OK`, and says coverage cannot be established because the retention policy requires administrator review.

## Background / Prior Research

- Screenshots were captured from TestFlight `0.160.0` against the deployed PR #161 backend.
- Deployment verification established API/web/OTA `0.160.0`, database revision `0094_backup_delete_intents`, and a deliberately unactivated migrated retention singleton (`retention_review_required=true`, `retention_activated_at=NULL`).
- The governing design intentionally boots upgraded installations paused and requires one optimistic, confirmed system-administrator save before pruning can activate (`docs/adr/0077-box-global-backup-retention.md:190-204`, `apps/api/src/family_cfo_api/api/backups.py:709-728`, `apps/api/src/family_cfo_api/repository.py:5045-5120`).
- Git archaeology traced the contract to `6b1ebb4c` (accepted design), `000b7dc6` (settings foundation), `e56068e8` (lifecycle gate), `dfdb820d` (recovery-status contract), and the iOS editor to `25105493`.
- Current iOS request mapping appears contract-complete: Synology uses the distinct `offbox_*` fields; the explicit activation snapshot includes both policies and capacity limits; `performSave` supplies the CAS token and confirmation flag (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupViewModel.swift:389-472`, `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupAPI.swift:98-115`).
- Read-only live SQL after the reported tap proves no update committed: `created_at == updated_at == 2026-09-11 12:31:11.101696 UTC`, no box-global `backup.config_updated` audit exists, review remains required, persisted cadence remains `every_6h`, local policy remains tiered `3/14/90`, and Synology remains `keep_all`.
- A fresh sanitized recovery probe found local `degraded/unknown` solely for `retention_review_required` plus `coverage_unknown`; Synology was `degraded/not_applicable` solely for `retention_review_required`. Inventories and sampled reads succeeded and capacity was not the cause.

## Investigator Findings

### Scope and source identity

- Read-only investigation against current HEAD `8871a45917a35f9452a2ef81c5a5308c0781608e` in the bound issue-116 worktree. No deployed runtime or source code was changed.
- Context Builder question chat: `backup-save-failure-028ECB`.
- `0.160` does not uniquely identify the TestFlight archive commit: `VERSION` moved to `0.160` at `dfdb820d`, while later editor and lifecycle commits retained that version. The exact archive build number/commit or device traffic was not available. The client-side attribution below is therefore high-confidence, not a claim that the device request itself was captured.

### Finding 1 — the CAS timestamp is not losslessly round-tripped

The generated contract represents both the GET response `updated_at` and PUT request `expected_updated_at` as `Foundation.Date` (`apps/ios/FamilyCFO/FamilyCFOShared/APIClient/Generated/Types.swift:9258-9261,9412-9417,9484-9488`). The generated PUT serializer sends the request DTO through the configured JSON converter (`apps/ios/FamilyCFO/FamilyCFOShared/APIClient/Generated/Client.swift:16938-16965`). `APIClientFactory` installs `LenientDateTranscoder` on that client (`apps/ios/FamilyCFO/FamilyCFOShared/Networking/APIClientFactory.swift:104-108`).

The round trip is non-lossless in two stages:

1. Fractional decoding uses `ISO8601DateFormatter` (`apps/ios/FamilyCFO/FamilyCFOShared/Networking/Dates.swift:3-20`). On the current Darwin toolchain, the six-digit input retained only approximately millisecond precision.
2. Encoding delegates to the default `ISO8601DateTranscoder` (`apps/ios/FamilyCFO/FamilyCFOShared/Networking/APIClientFactory.swift:47-61`), which emitted no fractional seconds.

A local generated-client reproduction copied the current generated `Types.swift` and `Client.swift` unchanged; `cmp` returned zero for both files. It used the repository-pinned `swift-openapi-runtime` 1.12.0 (`apps/ios/FamilyCFO/FamilyCFO.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved:49-56`) and a capturing transport. Actual output was:

```text
input             = 2026-09-11T12:31:11.101696Z
decoded_epoch     = 1789129871.101000071
reencoded         = 2026-09-11T12:31:11Z
generated PUT JSON = {
  "expected_updated_at" : "2026-09-11T12:31:11Z",
  "frequency" : "weekly"
}
```

A separate direct Foundation reproduction produced the same decoded epoch and whole-second re-encoding. This disproves a lossless CAS-token round trip; it is not solely an encoder defect because decoding has already reduced the original six-digit value before encoding removes the fractional part.

### Finding 2 — the exact payload deterministically produces 409 and no commit

`BackupConfigUpdateRequest` accepts a timezone-aware timestamp and normalizes it to UTC without restoring missing precision (`apps/api/src/family_cfo_api/schemas.py:1942-1968`). The endpoint passes that value to either the ordinary or confirmed-activation repository path (`apps/api/src/family_cfo_api/api/backups.py:703-728`). The repository compares the token exactly:

- ordinary save: SQL `updated_at == expected_revision`, with zero updated rows converted to `BackupSettingsConflictError` (`apps/api/src/family_cfo_api/repository.py:4937-5030`);
- confirmed activation: an in-transaction Python equality check followed by the same exact SQL predicate (`apps/api/src/family_cfo_api/repository.py:5041-5119`).

A disposable local FastAPI/SQLite reproduction bootstrapped the singleton at exactly `2026-09-11T12:31:11.101696Z`, authenticated with the synthetic demo fixture, then submitted the generated whole-second token. The schedule case produced:

```text
GET.updated_at = 2026-09-11T12:31:11.101696Z
PUT.payload    = {'frequency': 'weekly',
                  'expected_updated_at': '2026-09-11T12:31:11Z'}
PUT.status     = 409
PUT.body       = {'error': {'code': 'http_error',
                  'message': 'Backup configuration changed. Reload it and reconcile your draft.',
                  'details': {}}}
stored_unchanged = True; config_update_audits = 0
```

The confirmed activation case included `frequency`, valid local and off-box tiered policies, `confirm_retention_policy=true`, and the same whole-second token. It also returned the same 409. The row remained at its original revision and values (`frequency=daily`, off-box `keep_all`, review required, activation null), with zero `backup.config_updated` audits.

That status follows the endpoint's explicit conflict mapping (`apps/api/src/family_cfo_api/api/backups.py:730-743`) and global error envelope (`apps/api/src/family_cfo_api/main.py:149-163`). The audit and response reload occur only after the repository call returns successfully (`apps/api/src/family_cfo_api/api/backups.py:748-759`). The existing focused API CAS test also passed locally:

```text
env PYTHONPATH=.:src .venv/bin/pytest -q \
  tests/test_backups_api.py::test_backup_config_activation_is_cas_guarded_and_alias_compatible
1 passed in 0.21s
```

That existing test proves stale-token HTTP behavior (`apps/api/tests/test_backups_api.py:513-559`), but it echoes a server timestamp through Python/JSON and therefore cannot detect the Swift boundary.

### Finding 3 — both UI actions construct a PUT; failures are easy to miss

- Schedule is an independent autosave: changing `frequency` launches `viewModel.save()` from `.onChange` (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupSettingsView.swift:55-62`). It runs before and independently of retention activation.
- Retention editors bind separately to `localRetention` and `offboxRetention`; editing them does not save. The button calls `saveAndActivateRetention()` (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupSettingsView.swift:231-241,293-302`).
- The ViewModel activation snapshot contains the current operational fields plus both retention drafts (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupViewModel.swift:389-410,445-472`).
- `LiveBackupAPI` includes `local_retention`, `offbox_retention`, destination-specific caps/reserves, the CAS timestamp, and `confirm_retention_policy` in confirmed requests (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupAPI.swift:98-115`). The Synology edit is not omitted or mapped to the local policy.
- The generated client recognizes 409 as `.conflict` (`apps/ios/FamilyCFO/FamilyCFOShared/APIClient/Generated/Client.swift:17045-17060`), and `LiveBackupAPI` preserves the server message as `BackupError.configurationConflict` (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupAPI.swift:116-127`).

For a request whose completion still owns the screen, `performSave` assigns every configuration failure to `configError`; a 409 additionally fetches the current configuration while preserving the draft (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupViewModel.swift:474-515`). `configError` does not drive either top-level alert (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupSettingsView.swift:63-72`). Its only presentation is far below Schedule, inside Retention and capacity, after both pending-prune previews and immediately above reconciliation/the activation button (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupSettingsView.swift:260-302`). A schedule failure can therefore appear silent while its error is below the fold; no schedule-save success acknowledgement is presented.

Publication is conditional. Current HEAD requires the same active view lifetime, session, request generation, CAS token, and a non-cancelled task (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupViewModel.swift:917-930`). Disappearance invalidates generations and clears pending saves (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupViewModel.swift:136-167`). Those guards can discard a completion/error, but cannot undo a server commit already accepted.

The serialized queue coalesces newer drafts (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupViewModel.swift:389-435`). If the in-flight request fails, it clears every pending save behind it, so an activation queued in the narrow race after a schedule change can be suppressed by the first failure (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupViewModel.swift:427-434`). Normally the activation button is disabled while `isSaving` (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupViewModel.swift:224-235`), limiting that race. This is secondary to the reproduced timestamp defect: the first request that is sent still deterministically conflicts.

The connection footer says destination changes “save as you go,” but text/password fields save only on submit (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupSettingsView.swift:159-181,967-981`). That is an additional loss-of-edit risk on dismissal, but it does not explain the frequency picker or explicit retention button reports.

### Finding 4 — “Back up” is a literal, independent copy defect

The schedule picker is declared as `Picker("Back up", ...)` (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupSettingsView.swift:214-223`). It is unrelated to request serialization or persistence; the requested noun is “Backup.”

### Finding 5 — both Degraded cards are correct consequences of the unchanged review gate

For upgraded/non-empty installations, singleton bootstrap deliberately sets `retention_review_required=true` and `retention_activated_at=NULL`, migrates legacy cadence/destination policy, and validates that state (`apps/api/src/family_cfo_api/repository.py:4747-4833`). The invariant requires review exactly while activation is null (`apps/api/src/family_cfo_api/repository.py:4638-4647`). A successful confirmed CAS save atomically clears the review/conflict flags and sets `retention_activated_at` to the new revision (`apps/api/src/family_cfo_api/repository.py:5041-5119`).

Recovery derivation gives the observed results:

- `keep_all` coverage returns `not_applicable` before the review check (`apps/api/src/family_cfo_api/backup_recovery.py:355-361`).
- A tiered policy under review returns coverage `unknown` (`apps/api/src/family_cfo_api/backup_recovery.py:362-378`).
- Independently, `retention_review_required` is added as a reason; `coverage_unknown` is also added when applicable. Any such reason makes an otherwise unconstrained destination `degraded` (`apps/api/src/family_cfo_api/backup_recovery.py:443-513`).
- Both local and off-box builders pass the same persisted review state through those functions (`apps/api/src/family_cfo_api/backup_recovery.py:520-599,828-887`). Overall becomes degraded when configured destinations are neither all healthy nor constrained/unavailable (`apps/api/src/family_cfo_api/backup_recovery.py:916-935`).

This exactly matches the sanitized live probe already recorded above: local was `degraded/unknown` with only `retention_review_required` plus `coverage_unknown`; Synology was `degraded/not_applicable` with only `retention_review_required`. Inventories and sampled reads succeeded and capacity was OK. Therefore these cards do not evidence a capacity, SMB-read, inventory, or archive-integrity failure.

With the same otherwise-clean live evidence, a successful activation would remove the review reasons. Synology would remain `not_applicable` under `keep_all` but become healthy. Local tiered coverage would ordinarily become `building` immediately after a young activation with no outer-bucket opportunity, or `met` if existing qualified evidence satisfies the target (`apps/api/src/family_cfo_api/backup_recovery.py:382-420`); neither adds a degradation reason, so local and overall would become healthy absent another anomaly. Activation opens the pruning gate but does not prune in the request (`apps/api/src/family_cfo_api/repository.py:5041-5119`).

### Eliminated and remaining hypotheses

**Eliminated by current-HEAD code plus reproduction:**

- Missing/mis-mapped Synology policy, cadence, confirmation flag, or CAS token.
- FastAPI rounding the whole-second token back to the database microseconds.
- CAS tolerance for sub-second differences.
- Authorization as the systematic cause: GET and PUT use the same `BACKUPS_MANAGE` right (`apps/api/src/family_cfo_api/api/backups.py:625-662`); the reproduction authenticated as a system administrator and reached 409, not 403.
- Schema rejection of the reproduced payloads: both passed request validation and reached the repository conflict, returning 409 rather than 422.
- Capacity, inventory, or read-probe failure as the cause of the two reported recovery states.
- A surviving successful save in the observed deployed snapshot: unchanged original revision and values prove no surviving commit. The shared backup-operation lock prevents an in-flight restore from silently overwriting an acknowledged configuration write (`apps/api/src/family_cfo_api/api/backups.py:703-728`). Absence of an audit alone would not prove this because persistence precedes audit, and unobserved later manual database intervention cannot be logically excluded.

**Still not directly observed on the TestFlight device:**

- No device network capture/server request log proves that the reported tap sent this exact PUT and received this exact 409. Cancellation before server acceptance, transport failure, backup-operation-lock 409, queue suppression, or discarded error publication remain possible for an individual attempt.
- Nevertheless, the deployed fractional token plus the reproduced client/backend path is a deterministic blocker for every affected PUT that reached the current-compatible server. This is the high-confidence common explanation for both schedule and activation failures.

### Git history and test gaps

- `LenientDateTranscoder.encode` and pinned `swift-openapi-runtime` 1.12.0 have been unchanged since `064792f`; fractional decoding dates to `90634a6`. Both predate contract `0.160`.
- The literal “Back up” label and schedule autosave date to `e6ee945`; retention mapping, queue, conflict handling, inline error placement, and activation UI date to `25105493`.
- `ed17e4d3` later added view-lifetime/session invalidation and queue-generation guards. It did not introduce the core serialized queue, mapping, conflict fetch, or draft preservation, and it did not repair timestamp serialization.
- Swift retention tests pass `Date` directly through a mock API (`apps/ios/FamilyCFO/FamilyCFOTests/BackupViewModelRetentionTests.swift:141-146,205-242`) and assert in-memory equality (`apps/ios/FamilyCFO/FamilyCFOTests/BackupViewModelRetentionTests.swift:377-393,814-841`). They never exercise generated JSON or a six-digit timestamp.
- Recovery tests normally activate the settings helper before deriving status (`apps/api/tests/test_backup_recovery.py:18-22`), so they do not cover the exact migrated local `degraded/unknown` plus off-box `degraded/not_applicable` transition.

Missing regression coverage:

1. Generated-client GET decode -> PUT encode of a microsecond `updated_at`, asserting a lossless CAS token and successful cross-stack save.
2. End-to-end schedule and confirmed two-destination activation persistence, including revision advance, cleared review flag, activation timestamp, and audit.
3. Visible/accessible error presentation adjacent to schedule controls, plus ownership/cancellation cases.
4. Activation queued behind a failing autosave, ensuring the explicit action is not silently dropped.
5. Recovery status before and after the same confirmed activation, including the migrated local/off-box combination.
6. A UI copy regression asserting “Backup,” not “Back up.”

### Conclusion

Current HEAD proves a deterministic non-lossless optimistic-lock token path: the deployed microsecond `updated_at` becomes a whole-second `expected_updated_at`, and the API rejects both schedule and confirmed activation PUTs with HTTP 409 before persistence. The iOS DTOs and actions are present, but an owned error is rendered only in the lower Retention and capacity section and may be discarded when request ownership is lost. Because activation never committed in the observed deployed state, the intentional migration review gate remained active and independently caused both recovery cards to report Degraded. The literal “Back up” text is a separate copy defect. Attribution to the specific TestFlight tap is high-confidence but not traffic-capture proof until the archive commit or device/server request log is identified.

## Investigation Log

### Initial triage — screenshots and deployed state
**Hypotheses:**
1. The orange migration warnings are intentional until the first successful system-administrator activation, but an activation request is not persisting or the client reload overwrites local edits.
2. The iOS update DTO or view binding omits/mis-maps Synology retention and schedule fields, or save ownership/generation logic suppresses the result.
3. Recovery-window degradation is derived from the review gate and/or insufficient historical coverage rather than capacity/readability failures.
4. “Back up” is a localization/source-copy defect independent of persistence.

**Evidence:** User screenshots plus exact deployed-state verification above.

**Conclusion:** End-to-end tracing was required across Swift bindings/update construction, OpenAPI payloads, API persistence/activation semantics, repository singleton state, and recovery-status derivation.

### Git archaeology — intended activation and warning semantics

**Hypothesis:** The warnings and degraded cards might indicate failed storage, or might be the designed pre-activation state.

**Findings:** Upgraded installations deliberately bootstrap with review required and pruning paused. A successful confirmed CAS save clears the review/conflict flags and records an activation timestamp; the request itself does not prune. Tiered coverage is unknown during review, and any review reason makes an otherwise usable destination degraded.

**Evidence:** `docs/adr/0077-box-global-backup-retention.md:190-204`; `apps/api/src/family_cfo_api/repository.py:4747-4833,5041-5119`; `apps/api/src/family_cfo_api/backup_recovery.py:347-420,443-515`.

**Conclusion:** The initial warnings are intentional. Their persistence is downstream of the failed activation, not evidence of a storage/capacity failure.

### Live persistence and recovery probe

**Hypothesis:** The save may have committed and then been overwritten, or the UI might merely be stale.

**Findings:** The singleton's creation and update timestamps remain identical to bootstrap; cadence is still `every_6h`, local policy is still tiered `3/14/90`, Synology is still `keep_all`, review remains required, activation remains null, and no box-global config-update audit exists. A fresh recovery computation found successful inventory/read probes and adequate capacity; its only local reasons were review-required and coverage-unknown, and its only Synology reason was review-required.

**Evidence:** Sanitized, read-only SQL and recovery-service output captured in `## Background / Prior Research` and `## Investigator Findings`.

**Conclusion:** No configuration PUT committed. Eventual consistency, a stale GET, and storage health are eliminated.

### Generated-client and API reproduction

**Hypothesis:** Swift changes the server's exact CAS token before sending the update.

**Findings:** The current generated client decoded `2026-09-11T12:31:11.101696Z` to approximately millisecond precision and encoded it as `2026-09-11T12:31:11Z`. The resulting schedule-only and confirmed-activation requests both returned 409 against a disposable current backend; stored settings and audit rows remained unchanged.

**Evidence:** `apps/ios/FamilyCFO/FamilyCFOShared/Networking/Dates.swift:3-20`; `apps/ios/FamilyCFO/FamilyCFOShared/Networking/APIClientFactory.swift:47-61,104-108`; generated request/response types at `apps/ios/FamilyCFO/FamilyCFOShared/APIClient/Generated/Types.swift:9258-9261,9412-9417,9484-9488`; exact backend predicates at `apps/api/src/family_cfo_api/repository.py:4937-5030,5041-5119`.

**Conclusion:** Confirmed common root cause for every affected iOS PUT that reaches the current backend. The precise TestFlight request was not packet-captured, so cancellation or transport failure can still explain an individual tap, but not the deterministic incompatibility.

### UI/save-lane audit

**Hypothesis:** Synology fields or schedule are omitted from the request, or activation uses a separate broken mapping.

**Findings:** The DTO mapping is correct and distinct for local versus off-box policy. Schedule autosaves independently; retention is sent only by the explicit activation action. A failed in-flight save clears any queued activation, and `configError` is shown only below the fold inside Retention and capacity rather than beside Schedule or in the top-level error alert. The schedule picker label is literally `Picker("Back up", ...)`.

**Evidence:** `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupAPI.swift:98-127`; `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupViewModel.swift:389-515`; `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupSettingsView.swift:55-72,214-302`.

**Conclusion:** Missing field mapping is eliminated. Queue failure handling and error placement are independent defects that make the CAS rejection appear silent; “Back up” is an independent copy bug.

## Root Cause

### 1. Lossy date-time transport is being used as an exact concurrency token

The backend stores a strictly advancing, microsecond-resolution `updated_at` and requires exact equality for both ordinary updates and activation. OpenAPI maps that value to `Foundation.Date`. `LenientDateTranscoder` accepts fractional input, but Foundation already loses the final microseconds and its encoder then emits no fractional seconds. The submitted token can never equal the stored revision, so the backend correctly returns 409 before mutation.

This one defect explains both reports of settings not taking effect:

- Schedule changes call the same PUT through autosave.
- “Save and activate retention” sends the off-box policy correctly but uses the same corrupted CAS token.
- Because activation never commits, `legacy_conflict_detected` and `retention_review_required` remain true, `retention_activated_at` remains null, and no box-global audit row is written.

### 2. Conflict handling is not visible or durable enough

The 409 is assigned to `configError`, whose only presentation is below the pending-prune previews in the retention section. It does not drive the screen's top-level error alert and is not colocated with the schedule picker. Ownership/lifetime guards may discard late presentation, and a failed autosave clears a queued explicit activation. These do not cause the first 409, but they explain why a deterministic backend rejection looks like an ignored control.

### 3. Recovery warnings are consequential, not independent storage faults

The two orange policy warnings are the intended one-time upgrade gate. The recovery cards derive directly from the gate that the failed activation could not clear:

- Local tiered policy: review required ⇒ coverage `unknown`; review plus unknown coverage ⇒ `degraded`.
- Synology `keep_all`: coverage is `not_applicable`, but review required alone ⇒ `degraded`.

Live inventory, read probes, and physical capacity were healthy. After a successful activation, Synology should be `healthy/not_applicable`; local should normally be `healthy/building` immediately after activation, or `healthy/met` if qualified target-bucket evidence exists. Archive dates, retention-event completeness, and logical-cap decisions can still produce a different legitimate result.

### 4. Copy defect

The picker title is the literal verb phrase “Back up.” The requested field label is the noun “Backup.” It is unrelated to persistence.

### Eliminated hypotheses

- Local and Synology policies being swapped or omitted from the iOS request.
- Cadence, confirmation, or CAS fields being absent.
- Authorization or schema validation as the systematic cause (the exact reproduction reached 409, not 403/422).
- Backend rounding, tolerant comparison, eventual consistency, or a surviving successful commit.
- SMB inventory, archive readability, or physical capacity failure causing the displayed recovery state.

## Recommendations

1. **Replace date-time CAS with an opaque revision token in the next OpenAPI contract.** Keep `updated_at` only for display. Add a string/integer revision returned by GET and an exact `expected_revision` accepted by PUT; increment it atomically in `apps/api/src/family_cfo_api/repository.py:4937-5119`. Implement dual-token server compatibility first, then regenerate web/Swift clients and move `BackupViewModel`/`BackupAPI` to the opaque token. If both old and new tokens are present and disagree, reject. Because `0.160` has already shipped to the box/TestFlight, preserve its frozen compatibility fixture and publish this as the next contract rather than silently redefining `0.160`.
2. **Do not “fix” this with timestamp tolerance or truncation.** Either permits distinct writes inside the tolerance window to share a token and weakens optimistic concurrency. An encoder-only Swift change is also insufficient because fractional precision is lost during decode. A global custom date transcoder would affect every API date and still couples correctness to database precision; raw-body interception or hand-edited generated code is brittle.
3. **Make explicit activation survive or visibly supersede autosave failure.** In `BackupViewModel.swift:389-435`, do not unconditionally erase a queued activation when an earlier operational save fails. Preserve the draft, reconcile against a freshly loaded revision, and require an explicit retry when true concurrent edits exist. Test autosave-in-flight → activation queued → autosave conflict/failure.
4. **Surface configuration failures at the control that initiated them.** Route schedule-save failures beside Schedule and activation failures beside the activation button or into the existing top-level error alert; announce them for accessibility and keep them until acknowledged/retried. A successful save/activation should also provide visible acknowledgement. Reconcile the “save as you go” footer with the fact that text/password fields currently save only on submit.
5. **Change the iOS picker label to “Backup.”** Update `BackupSettingsView.swift:216` and the string catalog/localizations; cover the rendered label.
6. **Keep recovery semantics but make the action-required state clearer.** When the only reason is `retention_review_required` (plus derivative `coverage_unknown`), consider rendering “Review required” prominently while retaining the API's `degraded` status. State explicitly that inventory, reads, and capacity are working and that a successful activation will recalculate coverage without pruning in the request.
7. **Verify the fix on the deployed path without bypassing authentication.** From an administrator session, change schedule and Synology policy, activate, then confirm: the response succeeds; the opaque revision advances; one `backup.config_updated` audit appears; `retention_review_required=false`; activation is non-null; persisted off-box policy matches the draft; and recovery transitions to the evidence-appropriate post-activation state. Keep automatic retention paused until the reviewed policy is intentional.

### Required regression coverage

- Generated Swift GET→PUT transport test using a six-digit server timestamp (or, preferably, the new opaque token) against the actual generated serializer.
- Cross-version API tests for old `expected_updated_at` and new `expected_revision`, including contradictory-token rejection and two concurrent writers.
- ViewModel/SwiftUI tests for autosave/activation ordering, draft preservation, error placement/accessibility, success acknowledgement, and “Backup” copy.
- Repository/API tests for exact CAS, activation state, audit-on-success, and no audit on rejection.
- Recovery tests for migrated pre-activation state, immediate post-activation `building/not_applicable`, mature `met`, and genuine incomplete/constrained cases.
- Frozen OpenAPI fixture plus regenerated Swift/web client drift and compatibility checks.

## Preventive Measures

- Treat optimistic-concurrency values as opaque lexical tokens across every client boundary; never depend on a UI date type preserving database precision.
- Add one cross-language contract test that fetches a server-generated revision and sends it back through each generated client before accepting any new CAS-protected endpoint.
- Test behavioral state transitions, not only in-memory DTO equality: successful persistence, audit creation, review-gate clearing, and recovery-state recalculation.
- Give destructive-policy activation its own explicit success/failure UX and queue priority rather than relying on generic background autosave behavior.
- Include an upgraded, non-empty installation in release smoke testing so the one-time retention-review workflow is exercised before TestFlight distribution.
