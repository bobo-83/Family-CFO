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
