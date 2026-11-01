# Issue #162: account-only sign-in — source validation

Date: 2026-10-31. Branch: `fix/issue-162-account-only-signin`.
Base: `51231208b059178db3b759e9cab9bfebd5e9c3f7`.
This record describes uncommitted source work and local synthetic validation,
not a published artifact, CI run, deployment or installed-device acceptance.

## Implemented decision

ADR 0079 supersedes QR bootstrap in ADRs 0036/0056. The change removes both web
QR producers, iOS scan/paste authentication, both QR-only routes and schemas,
QR-only repository/secret/certificate-distribution code, and the QR library.
Migration `0097_retire_qr_pairing` drops outstanding codes. No compatibility
endpoint, flag, shim or deferred QR-API cleanup remains. Historical migrations
and immutable old-contract fixtures are history, not active support.

`POST /pairing/login`, shared device registration/list/revoke, existing device
credentials/private keys, encryption wraps and phone/watch relay remain.
Contract is `0.162`, all component BUILD numbers are `0`; both clients were
regenerated and a new oldest-compatible fixture added without editing old ones.

The unauthenticated iOS root is account sign-in. Saved server/trust settings
survive signed-out relaunch, with explicit certificate-change setup and full,
selectable fingerprint display. Login diagnostics unwrap generated ClientError,
NSError and nested CFNetwork/SSL causes without displaying raw error details.
Account switching clears session-owned advisor/language/freshness state; sync
freshness is scoped by non-secret household/device ids. Delayed rights refresh
cannot resurrect an outgoing token. A one-line `WKExtension` → `WKApplication`
rename was needed to build the existing watch dictation code under Xcode 27's
warnings-as-errors policy.

Account establishment, loss/recovery limitations and coordinated rollout are in
`docs/guides/account-sign-in.md`. No forgotten-password reset or new key-recovery
bypass is implied. New/changed onboarding/recovery strings include vi/lt
translations; obsolete QR instructions are removed.

## Local checks

Environment: macOS, Xcode 27.2, iOS/watchOS simulator SDKs, Python 3.12.13,
Node 22.23.2/npm 10.9.8. No new system-tool dependency introduced.

| Gate | Result |
|---|---|
| `make -C apps/api lint check-openapi` | Pass; implemented routes match authoritative OpenAPI |
| Full API `PYTHONPATH=src:. .venv/bin/python -m pytest -q` from `apps/api` | **1,246 passed, 3 skipped** |
| Web `npm test -- --watch=false` | **381 passed**, 35 files |
| iOS/watch `xcodebuild test`, simulator, signing disabled, parallel testing disabled | **538 passed**, 81 suites |
| `scripts/check-client-compatibility.sh web` | Pass; localized production build, no missing-translation warnings |
| `scripts/check-client-compatibility.sh ios` | Pass; iOS/watch simulator build against 0.162 baseline |
| Regenerate each client and compare byte-for-byte with working outputs | Pass for TypeScript and Swift |
| `check-versions.sh`, `check-compatibility-fixtures.sh` | Pass |
| `git diff --check`, `check-repo-hygiene.sh`, localization JSON/XML parsing | Pass |

Regressions cover retired routes with/without authentication, an outstanding
pre-upgrade code, migration preservation of device/auth/member/device-wrap
records, schema-only rollback without resurrected codes, shared web/device login
throttling, device rights/list/revocation/last-seen, account login into a sealed
household, old credential decoding, signed-out server restoration, account/cache
isolation, wrong password and safe wrapped TLS/decoding/cancellation errors.
Existing watch shared-state and session-isolation suites also pass.

The three API skips are PostgreSQL-required migration (2) and advisory-lock (1)
tests: `FAMILY_CFO_TEST_DATABASE_URL` is not configured locally. CI's existing
`FAMILY_CFO_REQUIRE_POSTGRESQL=1` policy must make missing PostgreSQL loud. The
new QR data-preservation migration regression ran against SQLite; a real
PostgreSQL run remains an unpassed gate, not inferred from SQLite success.

One parallel simulator run stalled in simulator/framework services and was
terminated; the subsequent complete non-parallel run above passed. An existing
`ControlledBillsAPI` test-only Sendable warning remains; app/watch build gates
pass without relaxing their warnings-as-errors settings.

## Review and remaining acceptance

Manual source/diff and stale-reference checks completed. Context Builder was
unavailable. Automated Oracle review returned **0/4 completed lanes**: every
lane failed before producing findings because its packaged input exceeded the
provider size limit. No independent automated-review approval is claimed.

Not performed: GitHub CI, PostgreSQL-required tests, physical iPhone/watch
onboarding/upgrade/server-trust interaction, production session/key recovery,
TestFlight/OTA publication, deployment or issue closure. These are distinct
release/acceptance gates, not a deferred legacy-API cleanup task.

The historical QR incident is **retired/superseded, root cause unresolved**.
Removing QR and correcting shared diagnostics does not diagnose its original
operational trigger.

## Edge-case regression follow-up

The subsequent source review identified ordinary retries re-entering trust
setup and cold restoration overwriting the cached request language. Both are
fixed and covered by regression tests; this section supersedes the earlier
review-availability status for these focused changes, not its historical record.

Coverage added/updated:

- Exact URL/port/proxy-prefix and saved-pin/system-trust continuity across two
  consecutive attempts for TLS, timeout, cancellation, decoding, 401 and
  unexpected-HTTP failures (12 pin/failure combinations).
- 401 password clearing and 429 retry-state preservation with/without a pin;
  explicit Change server clearing credentials/errors; unsafe destinations
  rejected before a client is constructed.
- A valid 201 fixture with independently injected private-key and credential
  write failures, each with/without a pin: inline error, unchanged trust, no
  completed local session and no watch activation.
- vi/lt cold restoration before any fetch, missing cached language, same-token
  rights refresh, replacement session in the same/different household, and
  missing/corrupt server or credential data.
- Real storage-backed bootstrap → sign-out → new-model bootstrap: server/pin
  survive; credential/private key do not; revoked-session and watch clearing
  callbacks are observed without contacting external services.

`AppSessionStorage` and `AppSessionEffects` preserve live platform defaults but
let tests use unique preferences suites, in-memory credential writes and fake
artifact/watch/shortcut/revocation collaborators. Private-key saving also has a
narrow failure-injection seam. The new restoration tests never clear host
widgets/notifications or leave random freshness keys in standard defaults.

Validation:

- RED before the two production fixes: focused run executed 19 tests and failed
  with 83 reported expectations/issues, including trust retry and language cases.
- Final isolated focused run: **34 tests passed**, four suites.
- Final full iOS/watch simulator run: **549 tests passed**, 82 suites, with
  parallel testing disabled; signing disabled. Result bundle:
  `/tmp/issue162-edges-final.xcresult`.
- iOS oldest-contract compatibility build, diff whitespace, repository hygiene,
  version and immutable-fixture checks passed. API/web were not rerun in this
  Swift-only follow-up; their earlier results remain separate evidence.

Focused Oracle review: four of four lanes completed on snapshot
`2026-10-31/0854`; the final dependency-seam refinements were then checked locally
and tested, not sent through a second review. Reconciliation:

| Reported item | Lanes | Disposition |
|---|---|---|
| Shared storage, freshness and external-cleanup test isolation | 1–4 | Accepted; isolated storage and fake effects now used |
| Sign-out test previously tested only supplied data, not persistence | 1 | Accepted; storage-backed lifecycle regression added |
| Missing private-key/credential persistence-failure coverage | 4 | Accepted; valid-201 failure-injection cases added |
| Require provenance for every legacy language cache, defaulting unknown caches to English | 1 | Rejected as a new P1 regression: unbound legacy language caching predates this patch; invalidating every legacy cache would recreate the cold-start regression being fixed. Existing cache provenance remains an inherited limitation, not claimed solved. New-session replacements and invalid restorations explicitly reset language. |

No commit/push, deployment, physical-device acceptance or issue closure was
performed. PostgreSQL/physical-device gates from the earlier record remain
unpassed; no legacy QR API cleanup is deferred.

## Backend CI migration assertion follow-up

PR #165's initial backend run executed all PostgreSQL tests and reported one
failure with 1,248 passes: the backup bootstrap integration test still expected
migration `0096_backup_repair_index` after successfully upgrading to
`0097_retire_qr_pairing`. The earlier local run skipped PostgreSQL tests and did
not expose that stale assertion. No production migration failure was observed.

Updated the expected revision and added an explicit check that the QR-only
`pairing_sessions` table is absent after the PostgreSQL upgrade. Reproduced the
original failure locally before editing, using an isolated PostgreSQL 17 test
container with `FAMILY_CFO_REQUIRE_POSTGRESQL=1`.

Validation after the fix:

- Migration suite: **8 passed**, no skips.
- Full backend suite: **1,249 passed**, no skips; enforced crypto coverage
  **100%**. Log: `/tmp/issue162-pg-full.log`.
- API lint, OpenAPI drift check and diff whitespace check passed.

This passes the previously unrun local PostgreSQL test gate; it does not claim
production migration, recovery or physical-device acceptance. No runtime code,
contract or migration was changed in this follow-up.

## PR review fixes

Both accepted findings are addressed together in a separate follow-up commit:

- After a successful login response, private-key or credential persistence
  failure best-effort revokes the newly issued session with its returned token
  and the original URL/pin. A newly saved private key is removed. Revocation
  failure cannot complete local sign-in, activate watch access, replace trust,
  or hide the sanitized storage error. The dashboard device record is not
  deleted by session logout; cleanup does not bypass device-management rights.
- Device-owner labels distinguish member lookup loading, failure (including
  thrown transport errors or missing data), and successful unmatched lookup.
  Reload clears stale owner names. New labels are translated into vi/lt.

Regression tests failed before the production fixes. After the fixes:

- Focused iOS sign-in suite: **15 tests passed**, including both persistence
  failures with/without a pin and successful/failed cleanup. The generated
  DELETE request is checked for the issued bearer token and retained endpoint.
- Full iOS/watch simulator suite: **550 tests passed**, 82 suites, signing and
  parallel testing disabled. Result: `/tmp/issue162-review-full.xcresult`.
- Full web suite: **387 tests passed**, 35 files; localized production build
  passed. Member-loading, error/missing-data, thrown-error, successful-unmatched,
  and failed-reload cases are covered.
- Focused Oracle review of snapshot `2026-10-31/2104`: **4/4 lanes completed**,
  all reported no findings; accepted after checking against the source/tests.
- iOS/web oldest-contract compatibility builds, version/immutable-fixture checks,
  repository hygiene and diff-whitespace checks passed.

Backend code, contract and migrations are unchanged; the earlier PostgreSQL
validation remains separate evidence. Physical-device, production recovery and
release gates remain unrun. No merge, deployment or issue closure is implied.

## Latest-wins and retained-key coverage follow-up

The final PR review reproduced a member-enrichment race: revocation could start
another roster load before an older member lookup completed, letting stale
success/failure overwrite newer owner labels. A monotonically increasing load
generation now guards device and member results, including thrown failures.
Current device-list rejection ends the loading state with an error; a superseded
rejection has no effect and produces no unhandled promise rejection.

Coverage strengthened in the same separate follow-up commit:

- Six deferred member-result interleavings and three deferred device-result
  interleavings cover stale success, returned errors and thrown failures after
  newer loads finish. Current device-list rejection is also tested. Four race
  regressions failed before the generation guard.
- The populated migration-preservation test runs on both SQLite and isolated
  PostgreSQL 17 schemas. It compares pre/post device, session, household-key and
  wrap rows, invalidates outstanding QR codes and checks schema-only rollback.
- After migration and a key-cache reset, the existing device remains locked
  until its retained private key unwraps its fetched wrap and opens the real
  key-session endpoint; no password re-login or cached key masks the result.
- The account-login crypto test now creates independent device key pairs. Each
  private key opens only its own wrap and unlocks after a fresh cache reset;
  cross-device wrong-key attempts fail authentication. Keys are generated only
  at test runtime and are never logged or committed.

Review reconciliation for snapshot `2026-10-31/2142`: **4/4 lanes completed**.
Lanes 2–4 reported no findings. Lane 1's rejected-device-request handling gap
was accepted as robustness work (the missing catch was inherited, not a newly
introduced P1), then fixed and covered with current/superseded rejection tests.
Final refinements were checked locally rather than sent for repeated review.

Validation:

- Focused migration/crypto suite: **28 passed**, including both database engines.
- Full PostgreSQL-required backend suite: **1,250 passed**, no skips; enforced
  crypto coverage **100%**. API lint and OpenAPI drift checks passed. Log:
  `/tmp/issue162-review-final-api.log`.
- Full web suite: **397 passed**, 35 files; localized production build passed.
  Log: `/tmp/issue162-review-final-web.log`.
- Web oldest-contract compatibility, version/fixture/hygiene and diff-whitespace
  checks passed.
- Swift/iOS source is unchanged in this follow-up; its prior 550-test result is
  separate evidence, not a fresh execution claim.

No production migration, physical-device acceptance, merge or deployment was
performed. No legacy QR protocol support or deferred cleanup was introduced.

## Release preflight: OTA guidance

Before publishing, inspection found that the OTA script's generated landing page
still instructed users to generate/scan a QR code. Replaced that stale text with
account sign-in, preserved-session and explicit certificate-verification guidance.
A static regression reads the actual emitted HTML heredoc without executing any
build/signing/deployment tool; it failed before the text correction and passed
afterward. API lint, shell syntax and diff checks passed. Four focused review
lanes completed with no findings. Fresh PR CI must pass before merge/deployment.
