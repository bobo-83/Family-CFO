# Backup Settings Revision and Recovery UX: Implementation Plan

## 1. Summary

Replace the lossy `updated_at` optimistic-concurrency contract with a required, opaque string `revision` on `BackupConfig` and an optional `expected_revision` on updates. Persist the revision as a UUID string and advance it atomically on every settings mutation. Keep `updated_at` as display/audit chronology and accept exact `expected_updated_at` requests for the single `0.161` compatibility window so shipped `0.160` web and other lossless clients continue working; the already-shipped iOS timestamp truncation remains safely rejected until the regenerated `0.161` client adopts the string revision. Preserve the administrator-review and no-pruning-during-activation invariants. Update both clients to use only the new revision, harden their serialized save lanes so explicit activation cannot be discarded behind an autosave failure, place save feedback beside the initiating controls, change the picker noun to “Backup,” and render review-only degraded recovery states as an actionable “Review required” presentation without weakening the API’s machine-readable degraded status.

This is a targeted vertical change rather than a generalized HTTP-concurrency or settings-framework refactor. Only backup configuration currently has the demonstrated cross-language precision failure, and the existing repository lock/CAS, generated-client, client ownership, and recovery reason-code seams are suitable extension points.

### Planning basis

- Scope is PR #161 at `8871a45917a35f9452a2ef81c5a5308c0781608e` on `fix/issue-116-tiered-backup-retention`.
- The source-of-truth diagnosis, including live sanitized database and API reproduction, is `docs/investigations/backup-retention-activation-recovery-warnings-2026-09-11.md:24-156,213-269`.
- The existing retention design and activation safety contract remain authoritative in `docs/adr/0077-box-global-backup-retention.md:190-204,249-302` and `docs/plans/backup-retention-recovery-window-2026-09-10.md`.
- The compatibility/version boundary is governed by `docs/adr/0074-per-component-build-numbers.md:78-114` and `docs/specs/04-openapi.md:116-215`.
- Standards evidence supports opaque validators and strict comparison, but not a second header authority for this transition: [RFC 9110 §13.1.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.1), [RFC 6585 §3](https://www.rfc-editor.org/rfc/rfc6585.html#section-3), and the [OpenAPI 3.1.1 Header Object example](https://spec.openapis.org/oas/v3.1.1.html#header-object-example).
- The project pins swift-openapi-runtime 1.12.0 (`apps/ios/FamilyCFO/FamilyCFO.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved:49-55`); its date transcoder behavior confirms why timestamp formatting is not an adequate CAS repair: [swift-openapi-runtime 1.12.0 `Configuration`](https://github.com/apple/swift-openapi-runtime/blob/1.12.0/Sources/OpenAPIRuntime/Conversion/Configuration.swift).

### Scope boundaries and non-goals

- Do not weaken or bypass administrator review, perform synchronous pruning during activation, or change the recovery engine’s machine-readable statuses/reason codes.
- Do not change authentication/authorization, SMB credential handling, backup encryption, retention selection, or restore semantics except to invalidate stale configuration revisions after restore.
- Do not introduce a generic settings-save framework, global date transcoder change, `ETag`/`If-Match` authority, or persisted client-side draft queue.
- No Advisor tool change is required: this fixes mutation/concurrency and presentation for an existing data domain and adds no new household-readable domain.
- Production code begins only after the ADR and affected Spec Kit documents are accepted in the order defined by `docs/specs/README.md:5-23`.

---

## 2. Current-state analysis

### 2.1 Ownership and persistence

- `database/migrations/versions/0093_box_global_backup_settings.py` creates one `backup_settings` row keyed by `"global"`.
- `apps/api/src/family_cfo_api/models.py` maps the singleton.
- `repository.get_backup_settings()` lazily bootstraps it:
  - fresh installations begin activated;
  - upgraded/non-empty installations begin with:
    - `retention_review_required=true`;
    - `retention_activated_at=NULL`;
    - automatic pruning paused.
- `BackupSettingsRecord.updated_at` is currently both:
  - a user-facing modification time;
  - the exact repository concurrency token.
- Every successful settings mutation derives a strictly newer microsecond timestamp. Ordinary updates and activation compare that timestamp exactly.

The review gate, destination generations, retention policy, and backup-operation lock are already correctly box-global and must be retained.

Evidence: `database/migrations/versions/0093_box_global_backup_settings.py:37-78`, `apps/api/src/family_cfo_api/models.py:732-763`, and `apps/api/src/family_cfo_api/repository.py:4403-4460,4901-5120`.

### 2.2 Current GET-to-PUT path

The failing path is:

```text
repository BackupSettingsRecord.updated_at
→ BackupConfig.updated_at
→ OpenAPI string/date-time
→ generated client representation
→ BackupConfigUpdateRequest.expected_updated_at
→ API endpoint
→ repository exact updated_at predicate
```

Transformation boundaries:

1. The repository returns a timezone-aware Python `datetime`.
2. FastAPI/OpenAPI serializes it with database microseconds.
3. Web retains it as a TypeScript `string` and echoes it unchanged.
4. Generated Swift decodes it into `Foundation.Date`.
5. `LenientDateTranscoder` accepts fractional input, but Foundation loses precision and the default encoder emits whole seconds.
6. The repository compares the altered value with the exact stored timestamp.
7. Both schedule and activation requests receive HTTP 409 before mutation or audit.

No policy field is missing or misrouted; `BackupAPI.swift` correctly sends independent local and off-box policies.

Evidence: `apps/api/src/family_cfo_api/schemas.py:1877-1969`, `shared/openapi/family-cfo.v1.yaml:7544-7554,7692-7704`, `apps/ios/FamilyCFO/FamilyCFOShared/APIClient/Generated/Types.swift:9256-9262,9410-9417`, `apps/ios/FamilyCFO/FamilyCFOShared/Networking/APIClientFactory.swift:46-61,104-108`, and `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupAPI.swift:98-127`.

### 2.3 API mutation and activation behavior

`update_backup_config()` currently:

1. Uses `model_fields_set` to build a partial patch.
2. Acquires `BackupOperationLock`, preventing an accepted write from being overwritten by an in-flight restore.
3. Loads the prior singleton.
4. Calls:
   - `update_backup_settings()` for ordinary changes; or
   - `update_and_activate_backup_settings()` for confirmed activation.
5. Maps lock contention and stale CAS to HTTP 409.
6. Writes `backup.config_updated` only after successful persistence.
7. Discards the committed `after` record, then reloads and returns the latest configuration after releasing the operation lock.

That final reload is a second concurrency defect independent of timestamp precision. Another administrator can commit between the first write and the response reload; the first client then receives the second writer’s token, attaches it to already-queued work, and can overwrite the intervening edit without seeing a conflict. The update response must instead be tied to the record committed by that request (`apps/api/src/family_cfo_api/api/backups.py:710-760`).

Confirmed activation atomically:

- applies both policies/caps/reserves;
- clears `retention_review_required`;
- clears `legacy_conflict_detected`;
- sets `retention_activated_at`;
- advances the revision timestamp.

It does not prune during the request. That invariant remains unchanged.

### 2.4 Client save ownership

#### Web

`Backups` in `apps/web/src/app/pages/backups/backups.ts` already:

- serializes automatic and retention saves;
- stores the returned `BackupConfig`;
- echoes `config.updated_at` unchanged;
- uses session/request generations to reject stale completions;
- preserves drafts during a 409 reconciliation flow.

It must switch tokens but does not need a new networking abstraction.

#### iOS

`@MainActor BackupViewModel`:

- owns editable operational and retention drafts;
- serializes all configuration updates through `pendingSave`;
- coalesces later automatic drafts;
- preserves visible retention drafts on conflicts;
- rejects stale session, lifetime, generation, token, and cancelled completions.

The blocking queue behavior is that `drainSaveQueue()` clears all queued work when `performSave()` returns failure. An explicit activation queued behind an operational request can therefore disappear.

`configError` is also shared by all configuration failures but appears only in the Retention section. A schedule failure is therefore below the initiating control and may appear silent.

Current completion guards do not fully own unsent intent. `replaceSessionIfNeeded()` invalidates household-sensitive state but does not clear or re-owner the box-global save queue; only `endViewLifetime()` advances `saveQueueGeneration` and clears `pendingSave`. A request captures the active session when it starts, not when its snapshot was enqueued (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupViewModel.swift:136-213,439-460`). The replacement queue must prevent a pending activation or password snapshot created under session A from being retried under session B.

The current iOS and web activation buttons are disabled while an operational save is active (`apps/ios/FamilyCFO/FamilyCFO/Backups/BackupViewModel.swift:233-236`, `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupSettingsView.swift:291-303`, `apps/web/src/app/pages/backups/backups.html:484-486`). The new behavior must deliberately decide whether activation is queueable through the actual UI rather than testing only an unreachable method-call sequence.

The complete client seams are `apps/web/src/app/pages/backups/backups.ts:430-583`, `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupViewModel.swift:389-539,917-999`, and `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupSettingsView.swift:55-72,214-303`.

### 2.5 Recovery derivation

`backup_recovery.py` deliberately preserves machine truth:

- tiered coverage is `unknown` while review is required;
- `keep_all` coverage remains `not_applicable`;
- `retention_review_required` is a degradation reason;
- `coverage_unknown` is additionally present where applicable;
- a destination with any degradation reason becomes `degraded`.

Thus the migrated state correctly produces:

| Destination | Coverage | Reasons | API status |
|---|---|---|---|
| Local tiered | `unknown` | `retention_review_required`, `coverage_unknown` | `degraded` |
| Off-box keep-all | `not_applicable` | `retention_review_required` | `degraded` |
| Overall | — | derived from both destinations | `degraded` |

The API must continue reporting these states. The presentation problem is that clients do not distinguish an administrator action gate from storage, inventory, capacity, or readability faults.

Evidence: `apps/api/src/family_cfo_api/backup_recovery.py:347-420,443-599,828-935` and `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupSettingsView.swift:379-480`.

### 2.6 Hard constraints

- OpenAPI is authoritative; generated Swift and TypeScript files cannot be hand-edited.
- `shared/openapi/compatibility/0.160.yaml` is immutable.
- A client that requires the new field cannot ship under contract `0.160`.
- `BACKUPS_MANAGE` remains required for every affected route.
- SMB credentials remain write-only/encrypted and cannot appear in revisions, errors, audits, logs, or tests.
- API work executes in the existing synchronous worker boundary and database transaction while the async route waits.
- iOS UI state remains `@MainActor`; web mutations remain serialized through one JavaScript promise lane.
- Restore, settings updates, and destination-generation rotation must all invalidate stale configuration tokens.

---

## 3. Design

### 3.1 Resolved concurrency contract

#### Chosen contract

Use additive JSON fields with one new authority:

```text
BackupConfig
  revision: string             required, opaque
  updated_at: date-time        required, display chronology only

BackupConfigUpdateRequest
  expected_revision: string?   new preferred precondition
  expected_updated_at: date-time? deprecated compatibility precondition
```

No `ETag` response header and no `If-Match` request header will be introduced.

Rationale: JSON fields remain ergonomic and consistent in both generators, avoid maintaining header and body authorities, and allow an additive server compatibility window for already-generated `0.160` clients.

#### Representation

- Database type: `VARCHAR(36) NOT NULL`.
- Values: application-generated UUID strings using the existing `new_id()` facility.
- API type: opaque `string`, with nonempty/bounded schema constraints but no OpenAPI `uuid` format requirement.
- Clients must not parse, sort, increment, display, or derive meaning from the token.
- `updated_at` remains a timestamp for display, audit chronology, journal context, and operator diagnostics only.

UUIDs are preferable to exposed integers because they remain language-neutral, cannot exceed JavaScript safe-integer precision, and make accidental client arithmetic visibly invalid.

#### One-release compatibility window

Contract `0.161` is the only server contract that accepts both token forms.

| Request form | Ordinary legacy fields | Retention/capacity update | Confirmed activation |
|---|---|---|---|
| `expected_revision` only | Exact revision CAS | Exact revision CAS | Exact revision CAS |
| Exact `expected_updated_at` only | Accepted for `0.160` compatibility | Accepted | Accepted |
| Both tokens | Both must match the same current row | Both must match | Both must match |
| Neither token | Existing tokenless legacy allowlist only | Rejected with 428 | Rejected with 428 |
| Stale new token | 409, no mutation | 409 | 409 |
| Stale legacy token | 409, no mutation | 409 | 409 |
| One current and one stale token | 409, no mutation | 409 | 409 |
| Truncated/rounded timestamp | 409, no tolerance | 409 | 409 |

Additional rules:

- A supplied compatibility timestamp is never ignored merely because a revision is also present.
- Tokenless writes retain the existing `_BACKUP_SETTINGS_TOKENLESS_FIELDS` restriction and equal-cap alias rule.
- Every successful tokenless mutation still advances the new revision, invalidating any revision-aware draft.
- “No-op” means the normalized patch is empty: validate every supplied token, then return the current row without advancing either field. A nonempty patch whose values equal the current row retains today’s reviewed-write behavior: it advances `revision`/`updated_at` and emits the existing no-value-change audit summary.
- Confirmed activation always advances both `revision` and `updated_at`, even if policy values are unchanged, because it changes or reaffirms the activation state and produces an audit.
- New web and iOS clients send only `expected_revision`.
- `expected_updated_at` is marked deprecated in `0.161`.
- The earliest removal of `expected_updated_at` and tokenless compatibility is contract `0.162`, in a separate coordinated change after `0.160` clients are no longer supported.
- The `0.160` fixture remains permanently unchanged.

#### HTTP results

| Condition | Status | Mutation/audit |
|---|---:|---|
| Successful update or activation | 200 | Commit, then one audit |
| Stale or contradictory supplied token | 409 | None |
| Backup/restore operation owns global lock | 409 | None |
| Required precondition absent | 428 | None |
| Invalid policy, malformed field, policy fields without confirmation | 422 | None |
| Unauthorized/forbidden | 401/403 | None |
| Unexpected persistence failure | 500 | Transaction rolls back |
| Audit/response-construction failure after persistence | 500/transport ambiguity | Settings may be committed; clients preserve drafts and reload before retry |

Because this is an application-body CAS rather than `If-Match`, stale predicates retain the existing 409 behavior. This preserves shipped client conflict handling and avoids partially adopting HTTP conditional-request semantics.

### 3.2 Database migration and model

At planning time, `0094_backup_delete_intents.py` is the sole migration head, so add:

`database/migrations/versions/0095_backup_settings_revision.py`

Reconfirm the head immediately before implementation; if another migration lands first, use the next available revision and correct parent without changing the design.

Migration behavior:

1. Add nullable `revision VARCHAR(36)` to `backup_settings`.
2. Generate one UUID per existing row in Python/Alembic and populate it. There can currently be at most the global singleton, but the migration must update by primary key rather than assuming exactly one row.
3. Alter the column to non-null.
4. Add a portable length check requiring 36 characters.
5. Do not assign a permanent server default; all future application inserts must supply a revision.
6. Downgrade removes the check and column.

No revision column is added to `backup_retention_events` in this change. Its existing `policy_updated_at` remains historical timestamp metadata, not a client concurrency token.

`models.backup_settings` gains the matching non-null string column and check constraint. `BackupSettingsRecord` gains immutable `revision: str`.

Bootstrap rules:

- A new or lazily materialized singleton receives `revision=new_id()`.
- Existing migrated settings receive one backfilled revision without changing `updated_at`, review flags, policies, activation time, or destination generations.

### 3.3 Repository mutation semantics

#### Interfaces

Conceptual signature changes:

```text
update_backup_settings(
  engine,
  patch,
  *,
  expected_revision: str | None,
  expected_updated_at: datetime | None
) -> BackupSettingsRecord

update_and_activate_backup_settings(
  engine,
  patch,
  *,
  expected_revision: str | None,
  expected_updated_at: datetime | None
) -> BackupSettingsRecord
```

At least one token is required by the activation function. The ordinary function retains tokenless compatibility.

Internal-only mutation helpers, including `activate_backup_retention()` and `rotate_backup_destination_generation()`, move to `expected_revision` rather than accepting timestamps. Update all internal call sites rather than preserving parallel timestamp APIs.

#### Shared CAS algorithm

Both ordinary and activation paths must use one private token-validation routine:

1. Normalize the supplied legacy timestamp to UTC.
2. Load the row under the existing transaction/row-lock discipline.
3. If `expected_revision` exists, compare it exactly with `current.revision`.
4. If `expected_updated_at` exists, compare it exactly with `current.updated_at`.
5. If either supplied comparison fails, raise `BackupSettingsConflictError`.
6. Validate the merged settings.
7. Generate:
   - `next_revision = new_id()`;
   - `next_updated_at = max(utcnow(), current.updated_at + 1 microsecond)`.
8. Repeat all supplied predicates in the SQL `UPDATE`:
   - key;
   - revision if supplied;
   - updated timestamp if supplied.
9. Set both new values in the same statement.
10. Treat any row count other than one as a conflict.

The repeated SQL predicate is required even after `SELECT ... FOR UPDATE`, because SQLite’s lock behavior differs and it protects against future adapter changes.

#### Mutation coverage

Every code path that changes `backup_settings` must assign a fresh revision:

- ordinary configuration update;
- tokenless compatibility update;
- confirmed update-and-activation;
- direct retention activation helper;
- local/off-box destination-generation rotation;
- restore re-upsert;
- restore compensation/recovery re-upsert;
- any test/helper that directly persists singleton changes.

Destination-generation rotation and configuration revision advancement occur in the same transaction. A destination identity change therefore invalidates both stale status causality and stale editor drafts.

Restore must never reuse the captured pre-restore revision. After bringing the restored database to the current schema, the re-upsert must:

- generate a new configuration revision;
- advance `updated_at`;
- rotate both destination generations;
- set review required and activation null;
- preserve the captured operational configuration and encrypted password.

The same rule applies to compensating restore state. Any client token observed before restore must fail afterward.

### 3.4 Schema and API layer

#### Pydantic schemas

`BackupConfig` gains required `revision: str`.

`BackupConfigUpdateRequest` gains `expected_revision: str | None`.

Validation changes:

- Retention/capacity fields still require `confirm_retention_policy=true`.
- Confirmation or retention/capacity changes require at least one of `expected_revision` or `expected_updated_at`.
- Omitted and explicit-null tokens both count as absent. After schema/policy validation succeeds, a request that requires a precondition but supplies neither token is mapped to 428 at the endpoint rather than reduced to a generic Pydantic 422.
- A supplied `expected_updated_at` remains timezone-aware and normalized to UTC.
- Request revisions use `min_length=1` and `max_length=128`; empty or overlong strings fail request validation with 422, while the response value remains opaque despite the current UUID-backed 36-character implementation.
- An unknown but syntactically valid revision is a stale token and returns 409.
- Structurally invalid bodies, invalid retention policies, or policy fields without confirmation remain 422 even when a token is also absent. The existing tokenless equal-cap `max_bytes` compatibility exception is unchanged.

#### API adapter

Extract a single response adapter that builds `BackupConfig` from a supplied `BackupSettingsRecord`; GET obtains the current record and calls it, while PUT calls it with the exact committed `after` record. Derived job/pending-prune metadata may be evaluated at response time, but configuration values and `revision` must remain pinned to that record.

`update_backup_config()`:

1. Builds the existing partial patch unchanged.
2. Detects whether policy/confirmation requires a precondition.
3. Returns 428 before lock acquisition if both token fields are missing.
4. Passes both possible tokens to the repository.
5. Retains:
   - `BACKUPS_MANAGE`;
   - `BackupOperationLock`;
   - 409 busy behavior;
   - 409 stale behavior;
   - 422 validation behavior;
   - audit contents and redaction;
   - post-commit audit behavior.
6. Returns the committed `after` configuration even if another writer advances the singleton before response construction. A post-commit audit/response failure remains transport-ambiguous and forces client reconciliation; it must never launder a later writer’s revision into this response.
7. Documents 428 in the route and OpenAPI.

The API will not emit `ETag`, and it will not read `If-Match`.

### 3.5 OpenAPI-first release and generated clients

#### Authoritative contract

After the ADR and `docs/specs/04-openapi.md` are accepted, update `shared/openapi/family-cfo.v1.yaml` before adapting Python or client code:

- `BackupConfig.revision`
  - required;
  - string;
  - `minLength: 1`, `maxLength: 128`;
  - described as opaque and the sole preferred concurrency token.
- `BackupConfig.updated_at`
  - unchanged type and requiredness;
  - description changed to display/modification time, not a token.
- `BackupConfigUpdateRequest.expected_revision`
  - optional string;
  - `minLength: 1`, `maxLength: 128` when present;
  - preferred precondition.
- `expected_updated_at`
  - retained;
  - deprecated;
  - documented as exact `0.160` compatibility only.
- PUT responses add 428.
- PUT description documents the dual-token AND behavior and 409 semantics.

Then regenerate:

- all committed web client artifacts under `apps/web/src/app/api-client`;
- Swift generated `Types.swift` and `Client.swift`.

No generated artifact may be hand-edited.

This is an implementation order, not permission to publish a half-updated contract. The required response field, matching API implementation, `0.161` fixture, generated clients, `VERSION`, and build resets form one coherent PR/release boundary; intermediate commits may be locally red but must not be released as contract `0.160`.

#### Contract release

- Change `VERSION` from `0.160` to `0.161`.
- Reset `apps/api/BUILD`, `apps/web/BUILD`, and `apps/ios/BUILD` to `0`.
- Add immutable `shared/openapi/compatibility/0.161.yaml` representing the first released `0.161` API.
- Never modify `shared/openapi/compatibility/0.160.yaml`.

A `0.161` client is not required to run against a `0.160` server; the contract mismatch must remain visible. A `0.161` server must accept valid `0.160` request shapes during the compatibility window.

### 3.6 Web adoption and save durability

#### Token adoption

In `backups.ts`:

- Change `RequestOwner.configUpdatedAt` to `configRevision`.
- Read `BackupConfig.revision`.
- Send `expected_revision: config.revision`.
- Do not send `expected_updated_at`.
- Update `serverConfig` only from an owned successful response whose revision belongs to that request’s committed record; adopt it before draining the next queued save so an intervening writer still produces a conflict.

#### Queue state

Replace the two booleans as the authoritative queue with explicit owned intents and immutable in-flight snapshots:

```text
pendingAutomatic:
  intent ID
  session/presentation epoch
  payload
  initiating controls: schedule and/or destination

pendingActivation:
  intent ID
  session/presentation epoch
  confirmed retention/capacity snapshot
  latest operational portion while not yet sent

inFlight:
  intent ID and owner
  immutable request snapshot

blockedSave:
  intent ID and owner
  immutable failed snapshot plus any newer pending intent
  phase: refreshing | refreshFailed | ready
  failure class
  message
  owned current server config only in ready phase
```

Rules:

- Stamp intent ownership when work is enqueued, not when transport starts. Session/presentation replacement, disappearance, restore initiation, or an explicit full reload invalidates old queued/blocked intent, conflict fetches, password snapshots, and feedback; an old completion cannot clear a newer owner’s busy state.
- Deliberately allow exactly one activation to be queued through the real UI while an operational save is in flight. The button becomes a visible pending state and rejects duplicate taps; this makes focus-loss/autosave followed by activation a supported interaction rather than only a method-level race.
- Before an activation is sent, its confirmed retention/capacity values are immutable, while later operational edits may update its operational portion so activation cannot revert newer schedule/destination input.
- Once sent, the full in-flight snapshot is immutable. Later operational edits become a separate pending automatic intent. Unconfirmed retention edits never mutate a confirmed intent.
- A write-only SMB password stays in memory, carries its edit generation in the snapshot, and is cleared only when that exact generation is acknowledged. A newer password edit must survive an older success; every owner/lifetime invalidation clears all password snapshots.
- A newer explicit activation confirmation supersedes an older unsent/blocked activation for the same owner. An older in-flight completion may acknowledge only its own intent ID and must not clear or claim success for the newer one.
- If an automatic save succeeds, activation proceeds using the returned revision.
- If an automatic save fails while activation is pending:
  - stop the loop;
  - preserve the activation;
  - fetch current configuration without overwriting the draft;
  - require explicit reconciliation before sending it.
- If activation fails, retain its exact sent snapshot and intent ID. If current visible retention differs, generic Retry is disabled: the user must explicitly confirm “Save and activate current settings,” creating a superseding intent. If values still match, Retry clearly identifies the preserved policy it will submit.
- Operational edits made after that failure remain a separate pending draft. When retention/capacity still match, an explicit Retry creates a new intent from the preserved confirmed policy plus the latest operational draft; it never mutates or resends an old full snapshot that would revert newer schedule/destination values.
- Blocked reconciliation has three explicit phases:
  - `refreshing`: owned GET in progress; Retry and Use current are disabled;
  - `refreshFailed`: drafts remain; expose “Reload current settings”; mutation retry remains disabled;
  - `ready`: current config/revision is owned by the same session/presentation; Retry and Use current are available.
- Every reload/retry travels through the same serialized lane. Duplicate retry taps coalesce or disable; edits while blocked update drafts but never auto-restart mutation.
- Classify failures instead of treating all errors alike:
  - stale/busy 409 or transport ambiguity: preserve intent, reload, then require explicit retry;
  - 401/403: preserve non-secret visible drafts but require authentication/permission recovery; never repeat mutation automatically, and discard destructive intent if session ownership changes;
  - 422: keep editable drafts, invalidate the failed confirmation, and require correction plus new explicit activation;
  - 428 from a revision-aware client: treat as a client/contract fault, reload and report it; never fall back to tokenless/timestamp writes;
  - owner/lifetime cancellation: discard old pending/blocked consent rather than converting it into a retryable failure.
- Retry adopts the owned fetched revision without overwriting visible drafts, then sends the still-valid preserved or superseding snapshot.
- “Use current settings” discards all pending snapshots and applies the fetched server values.
- Never automatically replay after a 409.
- Payload equality after a lost response means only “the box now matches this non-secret draft”; it does not prove which writer committed or that this request’s audit succeeded. The default path remains explicit reconciliation, not inferred success.

#### Web feedback

Separate configuration feedback by initiating control:

- Schedule: inline error/success immediately below the schedule field.
- Destination: inline error/success within the Synology settings card.
- Retention: validation, save failure, reconciliation actions, and activation success immediately adjacent to the activation button.
- Failures use `role="alert"`.
- Success and informational text use `role="status"` with polite announcement.
- New edits clear stale success feedback for that control but do not clear a pending failure/reconciliation state until it is resolved or superseded.
- Review/conflict warnings remain driven by the authoritative server response and clear only after an owned successful activation, never optimistically on click.
- Add English source messages plus Lithuanian and Vietnamese translations in `apps/web/src/locale/messages.xlf`, `messages.lt.xlf`, and `messages.vi.xlf`; `scripts/check-web-i18n.sh` remains the build gate.

### 3.7 Swift API and ViewModel adoption

#### DTO adapter

`BackupConfigDraft` changes:

- remove `expectedUpdatedAt: Date?`;
- add `expectedRevision: String?`.

`LiveBackupAPI.updateConfig()` sends generated `expectedRevision` and leaves deprecated `expectedUpdatedAt` absent.

Extend the existing `BackupError` taxonomy rather than string-matching localized messages:

- retain `configurationConflict(String)` for documented 409 responses (busy and stale both enter reconciliation);
- add `configurationInvalid(String)` for 422 so the ViewModel preserves editable values but requires correction/reconfirmation;
- add `configurationPreconditionRequired(String)` for the regenerated 428 response so a new client reloads/reports a contract fault and never falls back;
- keep `APIError.unauthorized` and `APIError.server(403)` as authentication/permission boundaries; raw transport cancellation/error remains distinguishable from documented HTTP failures.

Confirm the generated 428 enum-case spelling after regeneration, then map that case explicitly in `LiveBackupAPI`.

Generated response `updatedAt: Date` remains available for display. The new `revision` remains a lossless Swift `String`.

`APIClientFactory.swift` and `Dates.swift` retain their existing date behavior. This change deliberately removes dates from CAS rather than changing global date encoding.

#### ViewModel ownership

`RequestOwner.configToken` changes from `Date?` to `String?`.

`BackupViewModel` adds:

- `configRevision: String?` as the sole save/status ownership token;
- optional `configUpdatedAt: Date?` only if needed for visible chronology;
- explicit pending operational and activation queue state;
- enqueue-time session/presentation ownership and intent identities;
- an immutable in-flight snapshot separate from later drafts;
- a blocked/reconciliation state;
- source-specific configuration feedback.

Required state responsibilities:

```text
OperationalSaveOrigin = schedule | destination

SaveOwner
  session identity
  presentation/queue epoch

ConfigSaveFeedback
  owner and intent ID
  level = success | error
  message

PendingOperationalSave
  intent ID and owner
  draft
  origins: set<OperationalSaveOrigin>

PendingActivationSave
  intent ID and owner
  immutable confirmed retention/capacity draft
  latest unsent operational draft

InFlightSave
  intent ID and owner
  immutable request snapshot

BlockedSave
  intent ID and owner
  immutable failed snapshot
  phase and failure class
  message
  owned current server config only when ready
```

#### Queue algorithm

1. `save(origin:)` snapshots the operational fields with the current `SaveOwner` and coalesces origins only within that owner.
2. If an unsent activation is already pending, update only its operational portion; never mutate its confirmed retention/capacity values.
3. `saveAndActivateRetention()` captures both policies, capacity values, and the current operational draft as a new explicit intent. A newer confirmation supersedes an older unsent/blocked activation for the same owner.
4. Remove `!isSaving` as a blanket eligibility rule: allow one activation intent to queue behind an operational request, show it as pending, and disable duplicate confirmation while activation is pending/in flight/blocked.
5. Before starting any transport, the drain loop verifies enqueue-time session and presentation epochs. Activation includes all operational values and supersedes any older unsent operational snapshot already folded into it.
6. On success:
   - adopt the returned revision before another request starts;
   - acknowledge and remove only the matching owner/intent ID;
   - update only baselines corresponding to the sent snapshot;
   - retain edits made during the request as unsaved;
   - clear a sent password only when its edit generation still matches; retain any newer password edit;
   - publish success only for controls whose current value still matches the acknowledged snapshot;
   - refresh recovery status.
7. On recoverable failure:
   - retain the failed activation snapshot and its identity in blocked state;
   - retain any activation already queued behind a failed operational save;
   - stop draining;
   - preserve all visible drafts;
   - fetch current configuration through an owner-checked reconciliation request;
   - publish an error beside each affected control.
8. Reconciliation and Retry follow the `refreshing`/`refreshFailed`/`ready` states and failure classes defined in §3.6. Retry is unavailable until an owned fetch succeeds and always re-enters the serialized lane; duplicate taps cannot create a second request.
9. Retry:
   - requires an explicit user action;
   - adopts the separately fetched current revision without replacing drafts;
   - when visible confirmed retention still matches, creates a new retry intent from that confirmed policy plus the latest operational draft rather than mutating the immutable failed snapshot;
   - otherwise requires a new activation confirmation and superseding intent.
10. “Use current box settings”:
   - clears blocked and pending saves;
   - applies the fetched config completely;
   - refreshes recovery status.
11. Session replacement, view disappearance, restore initiation/completion, and explicit full reload increment the queue/presentation epoch and discard old pending/blocked activation consent, password snapshots, and feedback. A same-owner reconciliation GET is not a full reload and may update only the comparison base/blocked state.
12. No disk persistence is added. If the server accepted work before an ownership boundary, the next authoritative load recovers its result; the old intent is never resubmitted under the new owner.

A recoverable failure within the same owner must never clear a still-valid `pendingActivation`; ownership invalidation and an explicit Use-current action intentionally do.

#### Ambiguous completion

If a request may have committed but its response was lost:

- fetch current configuration;
- do not blindly retry;
- if an activation’s complete non-secret payload matches, review is cleared, and no write-only password participated, stop retrying and show an acknowledgement state: “The box now matches these settings, but this request’s response was not confirmed.” Do not claim which writer committed or that this request’s audit succeeded;
- if the state does not match or a write-only password prevents full comparison, require explicit retry after the owned reload;
- a retry uses the newly fetched revision but can create another accepted activation/audit, so duplicate intent/tap protection is still required;
- cancellation caused by session/lifetime invalidation discards the old intent; cancellation or timeout while the same owner remains active follows the transport-ambiguous reconciliation path.

### 3.8 iOS control-adjacent feedback and copy

`BackupSettingsView` changes:

- `Picker("Back up", ...)` becomes the noun label `Picker("Backup", ...)`.
- Keep the action label “Back up now”; that is correctly a verb.
- Schedule changes call `save(origin: .schedule)`.
- Destination text/password completion calls `save(origin: .destination)`.
- Use focus tracking so leaving a destination field triggers the same coalesced destination save; `.onSubmit` remains supported.
- Update footer copy to: schedule saves when selected; destination values save when editing finishes; retention requires explicit activation.
- While only an operational save is active, a valid activation button remains available for one tap, changes to an explicit queued/pending presentation, and rejects duplicates. It remains disabled for invalid retention, an activation already pending/in flight, or unresolved blocked reconciliation.

Feedback placement:

- `scheduleSaveFeedback` appears in `scheduleSection`.
- `destinationSaveFeedback` appears in `connectionSection`.
- `retentionSaveFeedback` and retry/reconciliation controls appear immediately above or below “Save and activate retention.”
- Initial configuration-load failure appears near the top of the form and includes Retry; it is not mislabeled as a retention error.
- Success uses a checkmark and non-danger styling.
- Failure uses visible text, an icon, and red/error styling.
- Success is announced politely; errors assertively.
- Accessibility labels include the initiating control, for example “Schedule save error.”
- Feedback survives until acknowledged, retried, superseded by a new edit, or replaced by a later owned completion.
- Keep the upgrade warnings visible while the authoritative response says `retention_review_required` or `legacy_conflict_detected`. Clear them only from an owned successful activation response; never optimistically hide them after a tap or after a failed save.
- Existing top-level alerts remain for backup/create/restore/delete and other non-configuration operations.

Add Lithuanian and Vietnamese values for every new or changed catalog entry in `Localizable.xcstrings`.

### 3.9 Recovery-card presentation

Do not change `backup_recovery.py`, API enums, reason codes, or overall aggregation.

Each client derives a presentation-only review state for a destination when:

```text
status == degraded
AND reason_codes contains retention_review_required
AND every reason code is one of:
  retention_review_required
  coverage_unknown
```

Presentation rules:

| API condition | Primary client badge | Additional treatment |
|---|---|---|
| Review-only degraded | `Review required` | Explain pruning is paused and direct the administrator to activation |
| Review plus any other degradation reason | Actual API status (`Degraded`, `Constrained`, etc.) | Show review as a secondary required action |
| No review reason | Existing API-derived state | No review action copy |
| Off-box keep-all under review | `Review required`; coverage remains “Not applicable” | Do not invent a coverage warning |
| Post-activation building/met | `Healthy`; coverage “Building” or “Met” | Preserve existing coverage explanation |

For review-only cards, clients may say that the server reports no additional inventory, read-probe, capacity, or anomaly reason. They must not claim archive integrity or key correctness.

Accessibility must retain the underlying state, for example:

> “Review required. API recovery status is degraded because retention policy review is pending.”

Overall status may display “Review required” only when the overall API status is actually `degraded`, at least one configured destination is review-only, every configured non-healthy destination is review-only, and none is constrained or unavailable. This avoids vacuous review labeling for all-healthy or unconfigured states. The underlying API value remains `degraded` in diagnostic/accessibility detail.

### 3.10 Concurrency and failure matrices

#### Server mutation concurrency

| Sequence | Result |
|---|---|
| Two revision-aware writers use the same revision | Exactly one commits; the other gets 409 |
| Legacy timestamp writer commits first | Revision advances; pending revision-aware writer gets 409 |
| Revision-aware writer commits first | `updated_at` advances; legacy writer gets 409 |
| Request supplies current revision and stale timestamp | 409 |
| Duplicate activation with the original revision | First may succeed; duplicate gets 409 |
| Restore races with settings update | Global operation lock serializes them; loser gets/observes busy state |
| Destination generation rotates | Revision advances in the same transaction |
| Client disconnects after server accepts work | Server lock/transaction completes independently; client reload reconciles |
| Writer B commits after writer A but before A constructs its response | A receives its own committed record/revision; A’s queued write conflicts with B rather than inheriting B’s token |

#### Client save lane

| State/event | Required behavior |
|---|---|
| Idle → automatic edit | Send one operational snapshot |
| More automatic edits while in flight | Coalesce to newest pending snapshot |
| Activation queued behind successful automatic save | Send activation with returned revision |
| Activation queued behind failed automatic save | Preserve activation, stop, reload current config, require retry |
| Activation itself fails | Preserve exact activation snapshot and draft |
| True 409 conflict | Never auto-replay |
| Busy 409 | Preserve draft; show server message; explicit retry |
| Transport ambiguity | Preserve draft and reload before retry |
| Reconciliation GET fails | Enter refresh-failed state; preserve drafts; disable Retry/Use current until owned reload succeeds |
| Newer confirmed activation while older intent is pending/blocked | Newer intent supersedes the older one; an older completion cannot clear it |
| Session/lifetime/restore/full-reload replacement | Discard old unsent consent/password snapshots; old completion publishes nothing or clears no newer busy state |
| Duplicate user retry | UI/queue coalesces or disables the second tap; do not rely on CAS after rebasing |
| Status refresh fails after save | Keep committed configuration; clear stale recovery dates only |

---

## 4. Verification plan

### 4.1 Automated backend tests

#### Migration and restore lifecycle

Extend `apps/api/tests/test_migrations.py` with real 0094→0095 upgrade and 0095→0094 downgrade coverage: backfill a pre-migration singleton, retain every policy/review/generation/timestamp value, assign a non-null revision, and remove only the revision on downgrade. Run the case against SQLite and the CI-required PostgreSQL lane.

Extend `apps/api/tests/test_backup_lifecycle.py` through the existing restore/compensation seams: a successful restore and a verified compensation path each produce a revision distinct from both the pre-restore live row and captured archive, rotate destination generations, re-enable review, retain the intended operational/encrypted state, and preserve existing preimage/rollback-marker guarantees.

#### Repository

Extend `test_backup_settings_repository.py` to cover:

- fresh bootstrap produces a valid revision;
- revision-only update success;
- stale revision rejection;
- exact legacy timestamp success;
- truncated timestamp rejection;
- both tokens current succeeds;
- either token stale rejects the entire write;
- tokenless compatibility update advances revision;
- two concurrent writers using one revision yield exactly one commit;
- activation advances revision and timestamp together;
- rejected activation changes neither row nor review flags;
- destination-generation rotation advances revision;
- empty normalized patch validates current/stale/dual tokens without advancing; a nonempty equal-value patch advances revision and retains the existing reviewed/no-value-change audit contract;
- direct restore/re-upsert helpers produce a fresh revision and re-enable review, complementing the lifecycle tests above.

#### API

Extend `test_backups_api.py` to cover:

- GET includes `revision`;
- revision-only schedule persistence;
- revision-only two-destination activation;
- timestamp-only `0.160` compatibility;
- contradictory dual-token request returns 409;
- missing activation precondition returns 428;
- table-driven omitted/null/both-token cases and exact 422-versus-428 precedence;
- invalid policy, malformed revision, and policy-without-confirmation remain 422;
- the tokenless equal-cap `max_bytes` alias remains accepted while disallowed tokenless fields remain 422;
- busy operation remains 409;
- no audit on 409/422/428;
- exactly one `backup.config_updated` audit on normal success;
- activation clears review/conflict state, sets activation time, and does not prune;
- deterministically pause writer A after persistence, commit writer B, then resume A: A’s response contains A’s committed revision and its queued follow-up conflicts with B rather than inheriting B’s revision.

#### Recovery transition

Add a focused pre/post activation test to `test_backup_recovery.py`:

1. Start with migrated review-required settings.
2. Configure local tiered and off-box keep-all.
3. Supply healthy inventory/probe/capacity evidence.
4. Before activation assert:
   - local `degraded/unknown`;
   - local review and coverage-unknown reasons;
   - off-box `degraded/not_applicable`;
   - off-box review reason;
   - overall degraded.
5. Activate through the same repository/API path.
6. Assert no archive was pruned.
7. After activation assert:
   - off-box `healthy/not_applicable`;
   - local `healthy/building` for young insufficient-history evidence, or `healthy/met` in a separate mature fixture;
   - overall healthy absent other anomalies.

### 4.2 Generated-client and web tests

- Regenerate both clients and run drift checks.
- Add a Swift transport-level regression using an actual generated GET decode and generated PUT encode:
  - GET JSON contains six-digit `updated_at` and a revision string;
  - decoded config retains the revision exactly;
  - PUT JSON contains identical `expected_revision`;
  - PUT omits `expected_updated_at`;
  - the lossy `Date` value is irrelevant to CAS.
- Web tests assert `expected_revision` is copied unchanged and the deprecated timestamp field is absent.
- Web queue tests cover automatic-in-flight → one UI-reachable activation queued → automatic failure → activation retained → failed reconciliation GET → owned reload → explicit retry.
- Cover duplicate Retry taps, Retry racing autosave, edits while blocked, immutable in-flight snapshots, newer confirmed activation superseding older blocked intent, and acknowledgement by exact owner/intent ID.
- Cover write-only password generations: an acknowledged value clears, a newer edit survives an older success, and owner invalidation clears all snapshots.
- Cover 401/403/409/422/428/transport/cancellation transitions and prove a revision-aware client never falls back to tokenless or timestamp writes.
- Test reverse-order/session replacement completions and session A→B→A so old consent cannot be submitted or clear newer busy state.
- Render the actual component to test the activation button’s queueable/disabled states, adjacent `role=alert`/`role=status`, and review-only versus review-plus-real-fault presentation.
- Assert upgrade warnings remain through failed/conflicted activation and disappear only after the owned response clears both server flags.
- Update and validate `messages.xlf`, `messages.lt.xlf`, and `messages.vi.xlf` for every new dashboard string.

### 4.3 iOS tests

Extend `BackupViewModelRetentionTests.swift` for:

- config load maps the string revision;
- operational and activation requests send it;
- successful first save advances the token used by the second;
- queued activation survives:
  - stale conflict;
  - lock/busy 409;
  - transport failure or same-owner timeout;
- 422 preserves editable values but invalidates automatic retry/old confirmation; correction requires a new activation intent;
- 401/403 requires auth/permission recovery and 428 never falls back to a legacy token path;
- explicit retry uses the freshly fetched revision;
- failed reconciliation GET disables Retry/Use current until an owned reload succeeds;
- duplicate retry taps, Retry racing autosave, and a newer confirmed activation cannot create or clear the wrong request;
- “Use current box settings” discards pending work;
- draft edits during an in-flight activation remain unsaved after success;
- an acknowledged password generation clears while a newer password edit survives an older completion;
- session A→B and A→B→A, view disappearance/reappearance, full reload, and restore boundaries discard old consent/password snapshots and reject late feedback without clearing newer busy state;
- schedule errors appear only in schedule feedback;
- destination errors appear only in destination feedback;
- activation errors and reconciliation appear by the activation button;
- success feedback is retained and superseded correctly;
- upgrade review/legacy warnings survive failure and clear only from the owned activation response;
- review-only recovery presentation differs from actual storage degradation;
- the review-only overall helper requires actual overall degradation plus at least one review-only destination and preserves all-healthy/unconfigured/mixed-fault states;
- the schedule field title is exactly “Backup” while “Back up now” remains unchanged;
- accessibility labels/live announcements and Lithuanian/Vietnamese catalog entries exist.

No SwiftUI rendering-test target exists at this head. Keep state/presentation logic in testable helpers and ViewModel tests, but treat the actual picker text, focus-loss submission, queued-button state, layout, and VoiceOver announcements as mandatory manual Xcode checks in §4.5 rather than claiming ViewModel tests prove rendered behavior.

### 4.4 Contract and build gates

Bootstrap dependencies before compatibility gates on a clean machine. The API lane requires Python 3.12, Tesseract, and an already-configured disposable PostgreSQL test database; the web lane requires Node 22:

```bash
uv venv --python 3.12 --seed apps/api/.venv
(cd apps/api && make install)
(cd apps/web && npm ci)
```

Run the non-Apple gates from the worktree root:

```bash
# Version and immutable contract fixtures.
scripts/check-versions.sh
scripts/check-compatibility-fixtures.sh

# API lint, focused SQLite regression lane, required PostgreSQL full suite, and OpenAPI parity.
cd apps/api
make lint
env -u FAMILY_CFO_REQUIRE_POSTGRESQL -u FAMILY_CFO_TEST_DATABASE_URL \
  .venv/bin/python -m pytest \
  tests/test_migrations.py \
  tests/test_backup_settings_repository.py \
  tests/test_backups_api.py \
  tests/test_backup_recovery.py \
  tests/test_backup_lifecycle.py
test -n "${FAMILY_CFO_TEST_DATABASE_URL:-}"
FAMILY_CFO_REQUIRE_POSTGRESQL=1 make coverage
make check-openapi
cd ../..

# Generated web client, dashboard build/i18n, and unit tests.
cd apps/web
npm run generate:client
git diff --exit-code -- src/app/api-client
CI=true npm test
cd ../..
scripts/check-web-i18n.sh
scripts/check-client-compatibility.sh web
```

Do not generate, mutate, or compile Swift/iOS code from Linux. On macOS with Xcode and the iOS/watchOS platforms installed, run the generator, oldest-contract compilation, and the same simulator test shape as CI:

```bash
scripts/generate-swift-client.sh --check
scripts/check-client-compatibility.sh ios
xcodebuild -downloadPlatform watchOS
cd apps/ios/FamilyCFO
sim="$(xcrun simctl list devices available \
  | awk -F '[()]' '/iPhone/ { udid = $2 } END { print udid }')"
test -n "$sim"
xcodebuild test \
  -project FamilyCFO.xcodeproj \
  -scheme FamilyCFO \
  -destination "id=$sim" \
  CODE_SIGNING_ALLOWED=NO
```

The PostgreSQL lane must fail, not skip, when its database/tool prerequisites are absent. Add explicit SQLite and PostgreSQL markers/fixtures for the new migration/CAS cases so both adapters execute. The compatibility scripts run against new `0.161.yaml`; separate API tests prove acceptance of frozen `0.160` request shapes.

### 4.5 Manual verification

Use a synthetic or sanitized upgraded database; do not request or expose credentials.

1. Upgrade a database containing an existing `0093` singleton with a fractional `updated_at`.
2. Confirm the new revision column is populated and review remains required.
3. GET `/api/v1/backups/config` and confirm:
   - `revision` is present;
   - `updated_at` is unchanged in meaning;
   - no `ETag` is emitted.
4. Submit an exact timestamp-only `0.160` schedule update; confirm success and revision advancement.
5. Submit a rounded timestamp; confirm 409, unchanged row, and no audit.
6. From the new web client, change schedule twice rapidly; confirm serialized persistence and the second request uses the first response revision.
7. From the new iOS client, change schedule and confirm adjacent “saved” feedback.
8. Queue activation behind a deliberately failed operational save; confirm the activation remains visibly pending and is not sent until Retry.
9. Activate reviewed local/off-box policies and confirm:
   - HTTP 200;
   - persisted policies match;
   - revision changes;
   - review/conflict flags clear;
   - activation time is non-null;
   - exactly one audit is present;
   - no pruning occurs in the request.
10. Refresh recovery:
    - review-only cards no longer remain;
    - off-box keep-all becomes healthy/not-applicable;
    - local becomes healthy/building or healthy/met according to evidence.
11. Introduce a synthetic probe or capacity fault and confirm clients show the real degraded/constrained state rather than “Review required.”
12. Verify VoiceOver announcements, Dynamic Type, narrow layouts, and Lithuanian/Vietnamese copy.
13. Restore a backup and verify both destination generations and the configuration revision change and review becomes required again.

### 4.6 Pre-merge box and TestFlight validation

After all automated gates pass and the implementation is committed on the PR branch:

1. Record `git rev-parse HEAD`, require a clean tracked worktree, and use source-build mode for the pre-merge candidate. Ensure no `API_IMAGE_TAG`/`WEB_IMAGE_TAG` override silently selects a different published image; record the resulting image IDs/digests as well as the application versions.
2. Use the existing credential stores and `.deploy.env` destination only; never place SSH or App Store secrets in commands, logs, the plan, or the repository.
3. Before applying migration 0095, take and verify a recoverable database backup, record the current Alembic revision and prior API/web image IDs, and quiesce API/worker writers. Rehearse both `upgrade head` and `alembic downgrade 0094_backup_delete_intents` against a disposable copy before touching the target box.
4. Patch the box’s contract-owning services first with `scripts/patch.sh api worker`. Confirm migrations complete, `/health` reports contract `0.161`, and prior clients receive an explicit version mismatch rather than silently assuming compatibility.
5. Patch the dashboard with `scripts/patch.sh web`, run `scripts/doctor.sh`, and execute the non-destructive web checks against that box.
6. Upload the same committed iOS candidate with `SKIP_OTA=1 scripts/release-testflight.sh`. This prevents the script’s default secondary OTA publication. Record the marketing version/build and App Store Connect processing state beside the tested commit, then deliberately assign only the intended internal tester group; upload, processing, and tester distribution are separate gates.
7. Install the processed TestFlight build and perform real schedule persistence, Synology policy activation, recovery transition, relaunch/reload, and blocked-save reconciliation. Before live activation, inspect the pending-prune preview and agree on the maintenance window because pruning may run on a later scheduler pass even though activation itself does not prune.
8. Run destructive restore, rollback, fault-injection, and synthetic archive cases only on an isolated migrated test box/database. A live box may receive narrowly bounded persistence/recovery checks only with explicit operator approval and a verified recovery point; the acceptance checklist never implies a routine production restore.
9. Do not merge based solely on green CI or a successful upload. The acceptance record must tie together the PR commit, source/image provenance, running API/web versions, schema revision, TestFlight build/audience, persisted settings/audit evidence, and recovery transition.
10. Before any `0.161` client is distributed, rollback means: stop writers, use migration-aware 0.161 tooling to downgrade the database to 0094, verify the prior data/state, then restore the prior API/web images as a unit. After a revision-dependent client is installed, a server-only rollback is unsupported; stopping further TestFlight distribution does not remove installed clients, so keep a compatible 0.161 server or ship a coordinated forward fix.

---

## 5. File-by-file impact

### Documentation and Spec Kit

#### `docs/plans/backup-settings-revision-and-recovery-ux-2026-09-12.md`

- Replace the current open-question draft with this resolved plan.
- Record the compatibility and rollout decisions before production changes.

#### `docs/adr/0078-backup-settings-opaque-revision.md` — new

- Supersede ADR 0077 only for concurrency-token representation.
- Record JSON revision selection, UUID storage, legacy timestamp window, status codes, and rejected ETag/tolerance alternatives.
- ADR 0077 is currently the latest; revalidate immediately before creation if implementation starts from a newer base.

#### `docs/specs/02-adrs.md`

- Index ADR 0078 and its accepted concurrency/compatibility decision before downstream specification edits.

#### `docs/specs/03-domain-model.md`

- Add the opaque configuration revision to the global backup-settings aggregate.
- State that `updated_at` is chronology rather than entity identity.

#### `docs/specs/04-openapi.md`

- Amend backup configuration with `revision`/`expected_revision`, 428, deprecation, and dual-token AND semantics.
- Change the expected contract to `0.161`.

#### `docs/specs/05-database-schema.md`

- Add `backup_settings.revision VARCHAR(36) NOT NULL`.
- Document backfill, advancement, restore, and downgrade behavior.
- Clarify `policy_updated_at` is not a client CAS token.

#### `docs/specs/06-security-model.md`

- State that revisions are opaque non-secret validators but must not expose credentials or configuration values.
- Confirm no authorization change.

#### `docs/specs/08-mobile-spec.md`

- Replace `updatedAt` CAS language with revision strings.
- Add durable activation queue, source-adjacent feedback, accessibility, “Backup” copy, and review-only presentation rules.

#### `docs/specs/09-angular-dashboard-spec.md`

- Mirror the revision, queue, feedback, accessibility, and review presentation requirements.

#### `docs/specs/12-implementation-tasks.md`

- Add the execution work items below after the preceding Spec Kit documents are accepted.

#### `docs/guides/backup-and-restore.md`

- Add migration 0095 and the opaque revision/one-release timestamp compatibility behavior.
- Explain that review-required warnings are intentional upgrade safety state, successful activation clears them, and “Review required” is presentation over an underlying degraded reason rather than proof of archive damage.
- Update rollout/rollback notes from contract 0.160 to 0.161 and document the pre-client schema downgrade boundary.

#### `docs/guides/deployment.md`

- Tighten “Ship a test build” to record exact commit/source-or-image/runtime/TestFlight identity, distinguish upload from tester distribution, and use `SKIP_OTA=1` when the candidate is intentionally TestFlight-only.

`docs/specs/README.md`, the PRD, AI orchestration, and Docker specification require no semantic amendment; their ordering and acceptance gates still apply.

### Database and backend

#### `database/migrations/versions/0095_backup_settings_revision.py` — new

- Add, backfill, constrain, and downgrade the revision column.
- Depends on the validated current Alembic head.

#### `apps/api/src/family_cfo_api/models.py`

- Add the revision column and check.

#### `apps/api/src/family_cfo_api/repository.py`

- Add `BackupSettingsRecord.revision`.
- Populate it during bootstrap.
- Add shared dual-token validation.
- Advance revision and timestamp atomically in every settings writer.
- Convert internal rotation/activation call sites to revision CAS.
- Ensure restore-related re-upserts never reuse a prior revision.

#### `apps/api/src/family_cfo_api/schemas.py`

- Add response/request revision fields.
- Update validation to accept either token while leaving missing-precondition status selection to the endpoint.

#### `apps/api/src/family_cfo_api/api/backups.py`

- Include revision in responses.
- Pass both precondition forms.
- Return 428 when required tokens are absent.
- Factor response construction so PUT serializes the exact committed `after` record while GET still loads current state.
- Preserve locking, audit, authorization, derived metadata, and no-pruning behavior without a post-commit revision reload.

#### `apps/api/src/family_cfo_api/backup_recovery.py`

- No production behavior change.
- It remains the authoritative machine-state derivation.

#### `apps/api/src/family_cfo_api/backup_processing.py`

- Keep `settings_updated_at` as journal chronology, not CAS.
- Continue routing restore finalization through the repository re-upsert so the new revision is fresh; preserve the existing preimage and compensation verification behavior.

### Backend tests

#### `apps/api/tests/test_migrations.py`

- Add real 0094→0095 backfill and 0095→0094 downgrade tests for SQLite and PostgreSQL.

#### `apps/api/tests/test_backup_lifecycle.py`

- Prove fresh revisions and review reactivation after successful restore and verified compensation/rollback paths.

#### `apps/api/tests/test_backup_settings_repository.py`

- Add bootstrap, dual-token, empty/equal patch, concurrency, rotation, and direct re-upsert revision tests.

#### `apps/api/tests/test_backups_api.py`

- Add contract compatibility, status-code precedence, response-revision race, audit, and persistence tests.

#### `apps/api/tests/test_backup_recovery.py`

- Add the review-required → activated recovery transition.

### OpenAPI and generated artifacts

#### `shared/openapi/family-cfo.v1.yaml`

- Add the new authoritative fields, deprecation text, and 428 response.

#### `shared/openapi/compatibility/0.161.yaml` — new

- Freeze the first `0.161` API.
- Depends on final authoritative OpenAPI.

#### `shared/openapi/compatibility/0.160.yaml`

- No changes permitted.

#### `apps/web/src/app/api-client/**`

- Regenerate all TypeScript client files.

#### `apps/ios/FamilyCFO/FamilyCFOShared/APIClient/Generated/Types.swift`
#### `apps/ios/FamilyCFO/FamilyCFOShared/APIClient/Generated/Client.swift`

- Regenerate from OpenAPI.
- Do not hand-edit.

### Web client

#### `apps/web/src/app/pages/backups/backups.ts`

- Adopt `revision`.
- Replace flag-only queuing with owner/intent-stamped pending and immutable in-flight snapshots plus explicit blocked reconciliation phases.
- Add source-specific feedback and review-only presentation helpers.

#### `apps/web/src/app/pages/backups/backups.html`

- Place feedback beside schedule, destination, and activation controls.
- Add retry/use-current actions and accessible recovery presentation.

#### `apps/web/src/app/pages/backups/backups.scss`

- Add non-color-only review, success, and error presentation styling.

#### `apps/web/src/app/pages/backups/backups.spec.ts`

- Add token, queue/intent ownership, failure transitions, rendered-button, feedback, accessibility, and recovery tests.

#### `apps/web/src/locale/messages.xlf`
#### `apps/web/src/locale/messages.lt.xlf`
#### `apps/web/src/locale/messages.vi.xlf`

- Add every new queue, reconciliation, save-feedback, and review-required string in English, Lithuanian, and Vietnamese.

### iOS client

#### `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupAPI.swift`

- Change draft token to `expectedRevision`.
- Send only the generated revision field.
- Extend `BackupError` with typed 422 and 428 configuration cases; map the generated responses explicitly and retain distinct auth/permission/transport errors for the queue state machine.

#### `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupViewModel.swift`

- Store and own the string revision.
- Implement enqueue-time session/presentation ownership, immutable intent identities/snapshots, and durable pending operational/activation states.
- Add blocked reconciliation phases, failure classification, superseding activation semantics, and serialized explicit retry.
- Add source-specific feedback and review-only presentation helpers.

#### `apps/ios/FamilyCFO/FamilyCFO/Backups/BackupSettingsView.swift`

- Change the picker label to “Backup.”
- Add focus-completion saves for destination fields.
- Allow one visible activation intent to queue behind an operational save and prevent duplicate confirmation.
- Render feedback adjacent to controls.
- Render review-only cards without hiding the API status.
- Add accessibility live announcements.

#### `apps/ios/FamilyCFO/FamilyCFO/Localizable.xcstrings`

- Add/update English, Lithuanian, and Vietnamese strings.

#### `apps/ios/FamilyCFO/FamilyCFOTests/BackupViewModelRetentionTests.swift`

- Update fixtures for required revisions.
- Add queue ownership/intent, failure-transition, retry, feedback, copy-helper, and recovery-presentation tests.

#### `apps/ios/FamilyCFO/FamilyCFOTests/BackupGeneratedTransportTests.swift` — new

- Exercise the actual generated GET decode and PUT encode with a fractional `updated_at`; assert lossless string-revision echo and omission of deprecated timestamp CAS.
- The synchronized Xcode test group includes the new file without hand-editing `project.pbxproj`.

#### `apps/ios/FamilyCFO/FamilyCFOShared/Networking/APIClientFactory.swift`
#### `apps/ios/FamilyCFO/FamilyCFOShared/Networking/Dates.swift`

- No production changes. Tests must demonstrate that their remaining date precision behavior no longer participates in CAS.

### Release metadata

#### `VERSION`

- Change to `0.161`.

#### `apps/api/BUILD`
#### `apps/web/BUILD`
#### `apps/ios/BUILD`

- Reset all to `0`.

---

## 6. Rejected alternatives

- **Timestamp tolerance or truncation:** rejected because two valid writes inside the tolerated interval could share a token.
- **Swift encoder-only repair:** rejected because precision has already been lost during decoding.
- **Global custom date transcoding:** rejected because it changes every endpoint and still couples correctness to database precision.
- **ETag/If-Match plus legacy JSON:** rejected because it creates simultaneous header and body precondition authorities during compatibility and is less ergonomic in the current generators.
- **ETag/If-Match only:** rejected because shipped clients already send body tokens and need an additive server transition.
- **Expose a numeric revision:** rejected because JavaScript precision and client arithmetic would become contract concerns.
- **Reuse `updated_at` text as an opaque string:** rejected because persistence correctness would remain coupled to timestamp precision and formatting.
- **Remove review from recovery degradation:** rejected because coverage is genuinely unknown while destructive policy is paused.
- **Add a new API recovery status solely for UI:** rejected because existing reason codes already distinguish review from faults; presentation can be improved without parallel machine truths.
- **Automatically retry stale drafts:** rejected because it can overwrite another administrator’s accepted edit.
- **Persist the iOS save queue across screen/app lifetimes:** rejected as unnecessary scope; server CAS plus authoritative reload handles accepted-but-unobserved requests, while unsent drafts remain presentation-scoped.
- **Generalize all settings screens onto a new save coordinator:** rejected because it adds indirection without solving another demonstrated defect.

---

## 7. Risks and migration

- **Already-shipped iOS remains unable to save fractional-token settings.** The server must continue rejecting altered timestamps; remediation requires the `0.161` iOS release.
- **Mixed-version rollout shows contract mismatch warnings.** This is intentional under ADR 0074. Deploy API first, then web, then iOS.
- **A post-commit reload can launder another writer’s revision.** PUT must serialize its own committed record, and the deterministic A-write/B-write/A-response test is a release blocker.
- **Box-global state does not make destructive consent session-global.** Every pending/in-flight/blocked intent and password snapshot needs enqueue-time session/presentation ownership; replacement or restore discards old consent.
- **Rollback to an old API after `0.161` clients ship is unsupported.** Old code neither supplies nor advances the new revision, and installed TestFlight clients cannot be recalled. Before client distribution, rehearse a writer-quiesced schema downgrade plus prior-image restore; afterward keep a compatible server or forward-fix.
- **Missed settings writer could leave revisions unchanged.** Search every insert/update/upsert of `models.backup_settings`, including restore and test helpers, and enforce mutation tests.
- **Transport ambiguity can create duplicate audits after explicit retry.** Clients reload first; a complete non-secret state match prevents blind retry but is described only as a match, not proof of this request or audit. Password-bearing or mismatched state requires explicit reconciliation.
- **Review-only UI could mask real faults.** The special presentation is allowed only for the exact reason-code subset; any additional reason restores the actual degraded/constrained/unavailable badge.
- **TestFlight upload normally also refreshes OTA.** The bounded pre-merge path sets `SKIP_OTA=1`; any later OTA publication requires its own artifact identity, audience, and verification.
- **Generated-client naming may differ for HTTP 428.** Validate the generated Swift response case rather than predicting its spelling before adapting `LiveBackupAPI`.
- **Migration/ADR numbering may advance before implementation.** They are confirmed as 0095/0078 at this plan’s exact head; revalidate after any rebase and adjust only identifiers/parents.

---

## 8. Implementation order

1. Accept the new ADR and amend Spec Kit documents in required order.
2. Define the accepted additive shape in authoritative `shared/openapi/family-cfo.v1.yaml`; do not publish the required response field as an intermediate `0.160` release.
3. Add the revision migration, model, repository state, migration/lifecycle coverage, and repository tests as one backend persistence change.
4. Add schema/API support—including committed-record response construction—and backend compatibility/race tests against the already-defined YAML.
5. Finalize contract `0.161`, its immutable fixture, all generated clients, `VERSION`, and component build resets as one coherent publication boundary.
6. Adopt revision tokens and durable save/reconciliation behavior in the web client.
7. Adopt revision tokens and durable save/reconciliation behavior in iOS.
8. Add control-adjacent feedback, localization, copy, queueable activation controls, and recovery presentation on both clients.
9. Run automated gates and synthetic migrated-install/rollback verification.
10. From one committed PR head, patch the test box API/worker first and web second, upload the matching iOS build to the bounded TestFlight audience, complete the real upgraded-box checklist, and merge only after the acceptance record is complete.

---

## 9. Execution index

### WI-0 — Accept the revised design

- **Goal:** Record the opaque-revision and compatibility decision before implementation.
- **Done when:** ADR and Spec Kit amendments are accepted in required order, the authoritative OpenAPI change is defined before Python/client implementation, and no token, response-ownership, intent-ownership, recovery, or rollback decision remains unresolved.
- **Key files:** New ADR; `docs/specs/02-adrs.md`, `03-domain-model.md`, `04-openapi.md`, `05-database-schema.md`, `06-security-model.md`, `08-mobile-spec.md`, `09-angular-dashboard-spec.md`, `12-implementation-tasks.md`; `docs/guides/backup-and-restore.md`; `docs/guides/deployment.md`; `shared/openapi/family-cfo.v1.yaml`.
- **Dependencies:** None.
- **Size:** M.

### WI-1 — Persist opaque revisions

- **Goal:** Give the singleton a durable UUID revision advanced by every writer.
- **Done when:** Migration upgrades/backfills/downgrades on SQLite and PostgreSQL; bootstrap and all mutation/restore/compensation/rotation paths produce fresh revisions; repository and lifecycle concurrency tests pass.
- **Key files:** New migration, `models.py`, `repository.py`, `test_migrations.py`, `test_backup_settings_repository.py`, `test_backup_lifecycle.py`.
- **Dependencies:** WI-0.
- **Size:** L.

### WI-2 — Add dual-token API compatibility

- **Goal:** Make revision-aware requests authoritative while accepting exact `0.160` timestamps for one window.
- **Done when:** GET exposes revision; PUT implements revision-only, timestamp-only, dual-token AND, 409, 422, and 428 rules; PUT returns its own committed record; the A/B response race conflicts rather than laundering B’s revision; audit/no-mutation assertions pass.
- **Key files:** `schemas.py`, `api/backups.py`, `test_backups_api.py`.
- **Dependencies:** WI-1.
- **Size:** L.

### WI-3 — Release contract `0.161` and regenerate clients

- **Goal:** Publish the additive contract and lossless generated string mappings.
- **Done when:** YAML is authoritative, `0.160` remains unchanged, `0.161` fixture exists, clients regenerate cleanly, version/build files are correct, and compatibility/drift gates pass.
- **Key files:** `family-cfo.v1.yaml`, `compatibility/0.161.yaml`, generated web/Swift clients, `VERSION`, component `BUILD` files.
- **Dependencies:** WI-0 through WI-2; the authoritative YAML is defined in WI-0, while fixture/version/generated-artifact publication waits for the matching server.
- **Size:** M.
- **Atomicity:** Contract, fixture, generated artifacts, version, and build resets must land together.

### WI-4 — Adopt revision and durable saves on web

- **Goal:** Remove timestamps from web CAS and preserve explicit activation across failed autosaves.
- **Done when:** Requests send only `expected_revision`; only the response’s own committed revision feeds the next request; blocked saves retain same-owner activation; failure/reload/retry states, intent supersession, rendered queue controls, and session ownership tests pass.
- **Key files:** `backups.ts`, template/styles, `backups.spec.ts`, and web locale catalogs.
- **Dependencies:** WI-3.
- **Size:** L.

### WI-5 — Adopt revision and durable saves on iOS

- **Goal:** Remove `Date` from iOS CAS and make activation queueing failure-safe.
- **Done when:** Generated transport test proves lossless revision echo; recoverable same-owner failures retain activation, 422 requires corrected reconfirmation, auth/owner boundaries never replay consent, and retry/use-current/session/lifetime/intent-ID tests pass.
- **Key files:** `BackupAPI.swift`, `BackupViewModel.swift`, `BackupViewModelRetentionTests.swift`, new `BackupGeneratedTransportTests.swift`.
- **Dependencies:** WI-3.
- **Size:** XL.

### WI-6 — Finish configuration UX and copy

- **Goal:** Make save outcomes visible beside their controls and correct the schedule label.
- **Done when:** Schedule, destination, and retention each show owned success/error feedback; one real UI activation can queue behind an operational save without duplicate consent; focus completion matches footer copy; picker says “Backup”; accessibility and both clients’ Lithuanian/Vietnamese translations pass.
- **Key files:** `BackupSettingsView.swift`, `Localizable.xcstrings`, web template/styles/component tests, web XLIFF catalogs.
- **Dependencies:** WI-4, WI-5.
- **Size:** L.

### WI-7 — Present review-required recovery truthfully

- **Goal:** Distinguish the administrator gate from real storage/recovery faults without changing API truth.
- **Done when:** Exact review-only reasons render “Review required”; mixed reasons retain real fault status; pre/post activation backend and client tests pass.
- **Key files:** `BackupViewModel.swift`, `BackupSettingsView.swift`, `backups.ts`, web template, `test_backup_recovery.py`, client tests.
- **Dependencies:** WI-4, WI-5.
- **Size:** M.

### WI-8 — Verify and roll out

- **Goal:** Prove migration, compatibility, persistence, activation safety, recovery transition, and accessibility on the deployed path.
- **Done when:** All automated gates and isolated upgrade/downgrade/manual checklists pass; one exact PR commit is traceable to source/image provenance, schema revision, running API/web versions, and the processed/distributed TestFlight build; `SKIP_OTA=1` bounds publication; API/worker precedes clients; one successful reviewed activation clears the gate without synchronous pruning; no merge occurs before this evidence is recorded.
- **Key files:** Test suites, compatibility scripts, release metadata, `scripts/patch.sh`, `scripts/doctor.sh`, `scripts/release-testflight.sh` (execution only; no script changes expected).
- **Dependencies:** WI-1 through WI-7.
- **Size:** M.

---

## 10. Open questions and implementation-time revalidation

No material design question remains. Before implementation or deployment, revalidate only environment-derived facts that can legitimately drift:

- migration 0095 and ADR 0078 are still the next identifiers after rebasing;
- the regenerated Swift name for the documented 428 response before adapting `LiveBackupAPI`;
- the disposable test-box target, live-box maintenance approval, TestFlight internal tester group, and pre-upgrade recovery point;
- the candidate commit, source/image provenance, schema revision, and deployed component versions immediately before acceptance testing.

If any of those checks changes the contract or safety boundary rather than only an identifier/environment value, stop and amend the accepted ADR/spec before implementation continues.
