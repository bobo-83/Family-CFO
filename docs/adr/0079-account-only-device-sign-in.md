# ADR 0079: Account-only device sign-in

## Status

Accepted (issue #162, 2026-10-31). Supersedes QR bootstrap and delegated
QR provisioning in ADRs 0036/0056. Implementation gate accepted by the operator's
decision in issue comment 6542964196.

## Scope and decision

Account sign-in is the only supported way to establish a new mobile device
session. Remove QR scanning/pasted-secret entry, dashboard QR creation on both
Devices and Users, and `POST /pairing/sessions` and `POST /pairing/confirm` in
ONE change. No compatibility endpoints, flags, shims or later cleanup phase.
Outstanding codes cease to be redeemable. Drop the QR-only `pairing_sessions`
table in a forward migration; retain historical migrations for upgrade history.

Keep `POST /pairing/login`, shared device registration, `paired_devices`,
`auth_sessions`, device listing/revocation, public/private keys and encryption
wraps. Existing credentials (including QR-issued credentials) remain valid
until ordinary expiry/revocation. No credential-origin migration or forced
logout. Phone/watch relay and local biometric unlock remain unchanged.

## Account establishment and recovery

- The bootstrap owner chooses their account in the dashboard. Other members
  accept a copy-link invite (ADR 0056) and choose their own password before
  signing in on iOS. Delegated QR setup is deliberately removed: never share
  passwords or sign everyone's device in as the owner.
- Sign-out deletes local credentials/private keys and clears session-owned
  data/watch access, but preserves the non-secret server address and trust
  configuration across relaunch. Re-entry uses that configuration, without
  silently replacing a self-signed pin. Changing server/trust is explicit.
- Password change requires the current password. There is NO forgotten-password
  reset endpoint or email recovery flow in this scope. A still-working device
  can retain access but does not reveal/reset its member's password. With a
  lost password, do not sign out of the last working session; consult the
  operator. Removing/re-inviting a member is destructive membership removal,
  not a password reset, and must not be presented as one.
- Account access and decryption recovery are distinct. Preserve member/device
  wraps and the existing Restore keys recovery-key flow (ADR 0072). A sealed
  household requires an available member/device wrap, recovery key or master
  key; a new invite is not proof of decryption-key recovery. No new reset,
  escrow, cloud dependency or key bypass is introduced.

## Trust and diagnostics

HTTPS is mandatory for account credentials. On explicit first setup, show the
server address and full certificate fingerprint for out-of-band comparison;
use host-valid system trust for CA-signed servers, explicit fingerprint trust
for self-signed servers. Never send credentials through the setup probe or
silently trust a changed self-signed certificate. Certificate rotation recovery
uses explicit server/trust setup, not a QR code.

Ordinary sign-in retries, including timeouts, TLS rejection, cancellation,
decoding failures and unexpected HTTP statuses, stay on the credentials screen
with the exact attempted URL and trust configuration. Only explicit Change
server restarts certificate discovery. Error text remains inline and sanitized.
If secure local persistence fails after the server issues a device session,
best-effort revoke that issued session using its token and the same URL/trust,
remove any newly saved private key, and report the storage failure even if
revocation is unavailable. Do not complete the local session or activate watch
access. Device-owner labels distinguish pending/unavailable member lookups from
a successful lookup that does not contain the member.

Cold restoration of valid existing credentials preserves the cached household
request language before the first request, even offline. A real session/account
replacement resets it; a same-token rights refresh does not. Missing/corrupt
server or credential data must not carry an old session's language into sign-in.

Account errors unwrap generated `ClientError` and underlying `NSError` chains.
Classify TLS, connectivity, cancellation and encoding/decoding failures without
printing raw errors, request/response bodies, URLs, credentials or userInfo.
Unknown failures must not be labelled connectivity failures.

## API, versioning and rollout

OpenAPI is authoritative; remove QR-only operations/schemas and regenerate
TypeScript/Swift clients. Bump the shared contract and reset all component BUILD
numbers (ADR 0074), with a new immutable compatibility baseline. Publish all
components together in a coordinated release: API/worker, then dashboard, then
iOS/watch. Older QR-only clients must update; they receive no legacy QR support.
Existing sessions keep working during the update. Rollback must use matching
component contracts; table downgrade cannot resurrect outstanding codes.
No deployment or release is implied by source implementation.

## Non-goals

No new financial data domain or advisor tool: authentication and secrets must
not be exposed to ordinary advisor chat. No redesign of invites, authentication
session lifetimes, watch relay or encryption. No diagnosis of the historical
QR incident's operational trigger. Closure must say retired/superseded, root
cause unresolved, rather than root cause fixed.

## Invariant and rejected alternatives

Every new mobile device session proves the member's own account credentials;
continuing an existing session does not depend on its bootstrap origin.

- Retaining QR behind a flag/compatibility shim or a later cleanup PR is rejected:
  it preserves the two-path confusion and violates this release's decision.
- Removing device registration with QR is rejected: account sign-in still needs
  device-bound revocation, encryption wraps and watch relay.
- Adding email/cloud password reset is rejected in this scope: it changes the
  recovery/security model rather than simplifying authentication. The deliberate
  tradeoff is losing delegated zero-password setup; members need usable accounts.

## Acceptance and documentation

Test removed routes (including an outstanding pre-upgrade code), migration
preservation of existing device/auth/key records, account login/rights,
wrong credentials/shared throttling, device list/revoke, sealed-key wraps,
fresh iOS setup, stored-server sign-out/relaunch, HTTPS and wrapper-aware safe
errors. Retain tests of account switching/cache isolation and watch continuity.
Generated-client drift, contract/version fixtures, API/web suites and iOS/watch
build/tests are gates; report unavailable/runtime/manual gates separately.
Update current mobile/security/dashboard specs, onboarding/recovery guides and
API README. Historical investigations/ADRs remain history with supersession
notices; they are not instructions to retain QR functionality.
