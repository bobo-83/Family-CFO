# ADR 0056: Copy-link invites and credentialed device login

## Status

Accepted.

## Context

Adding a family member had a real security wart: an admin created the member on
the Users page **and typed the member's password for them** — the member never
chose their own credentials — and a previously-removed member could not be
re-added at all (`createMember` 409s on any existing email). The iOS app had no
login screen: QR pairing was the only way in.

The household wanted email-based invitations with visible status, self-chosen
credentials, and an iOS login screen — without breaking the QR pair/unpair
system (ADR 0036) or the box's privacy posture (ADR 0008/0014: household data
never leaves the box; the repo deliberately has no email/SMTP capability).

## Decision

### 1. Invites are copy-link; the box sends no email

`POST /household/invites` (right: `members.manage`) creates an invitation and
returns a **one-time link token** exactly once. The admin shares the link
themselves (their own mail app, iMessage, …). The token is CSPRNG
(`token_urlsafe(32)`) and stored **SHA-256-hashed**; a "new link" action
(`regenerateInviteToken`) mints a fresh secret since the old one can never be
re-shown. Creating a new invite for the same email revokes the prior pending
one — one valid link per invitee, mirroring the one-valid-QR pairing rule.
Status (pending / accepted / expired / revoked) is computed from timestamps and
shown on the Users page. The join page keeps the token in the URL **fragment**
and the public `previewInvite`/`acceptInvite` endpoints take it in a POST body,
so the secret stays out of server access logs. Public endpoints are throttled
under a namespaced `invite-ip:` rate-limit key so join-page fumbles cannot lock
the family out of password login (ADR 0010 defense-in-depth).

### 2. Accepting an invite creates — or revives — the member

`acceptInvite` (public; the token is the bearer proof, same trust shape as the
QR confirm) atomically claims the invite (conditional update — a concurrent
double-accept loses with 410) and then:

- **email has an account with any membership** → 409, never overwriting a live
  account's password;
- **email has an account with NO membership** (a previously-removed member —
  the users row survives removal) → the account is **revived**: new password,
  new display name, fresh membership. Same human, same user id — audit history
  and private conversations (ADR 0038) stay attached. This is now the rejoin
  path for removed members.
- **new email** → a users row + membership, with the invite's role (ADR 0034
  role_id + legacy tier), then an auto-login `AuthSession`.

The single-tenant lockout on `POST /households` is untouched: joining an
existing household never bootstraps a new one.

### 3. iOS login is credentialed *pairing*, not a parallel auth path

`POST /pairing/login` (email + password + device name + device public key,
sharing the web login's brute-force counters) creates the **same**
`paired_devices` row + device-bound 30-day session as a QR confirm and returns
the same `DeviceCredential` — now carrying `household_id`/`household_name` so
the phone can build its ServerConfig without a QR payload. Therefore:

- phones signed in by password appear on the web **Devices page**;
- **revocation and unpair work identically** for both entry paths;
- QR pairing keeps its jobs (zero-typing path; pairing a device *for* another
  member with `devices.manage`).

Without a QR there is no cert fingerprint to pin, so the iOS login flow uses
**trust-on-first-use**: one explicit setup request captures the server's leaf
cert SHA-256, the user confirms it, and every later request is pinned to it —
the same trust act as scanning the admin's QR, rotated by re-login like re-pair.

### 4. Audit + undo (ADR 0023)

`invite.created` UNDOABLE (undo deletes the invite — the link dies);
`invite.revoked` UNDOABLE (the hash survives revocation, so undo revives the
original link); `invite.token_regenerated` IRREVERSIBLE (the old secret is not
stored); `invite.accepted` IRREVERSIBLE (the invitee set a password we refuse
to replay — remove the member instead); `pairing.login` IRREVERSIBLE (revoke
the device from Devices).

### 5. Platform scope (ADR 0025)

Invite *management* is an admin surface — dashboard-only, like members/roles
management (exception 3). The *join* page is inherently a browser artifact (a
link the invitee opens). The iOS login screen ships as its own change; QR
pairing covers invitees meanwhile, so no capability gap opens.

## Invariant

> A member's password is chosen only by that member (bootstrap owner excepted).
> An invite link's secret exists in exactly one place — the link — and is
> hashed at rest; it can be revoked, regenerated, or accepted exactly once.
> However a phone signs in (QR or password), it is a paired device: visible on
> the Devices page and revocable there. The box never sends email.

## Rejected

- **SMTP sending from the box** (e.g. the admin's own mailbox credentials in
  `.env`): the box would hold personal mailbox credentials and invite email
  would cross the ADR 0008/0014 egress line — machinery disproportionate to a
  family's a-few-invites-ever volume. Copy-link keeps delivery in the admin's
  own hands; SMTP can be a later optional milestone.
- **Transactional email service** (Resend/SES): a third party would see every
  invite — against the self-hosted privacy posture.
- **Reusing the web login session for iOS**: a `device_id=NULL` session is
  invisible on the Devices page and not revocable per-device — it would fork
  device management (the seam ADR 0036 exists to prevent).
- **Deep-linking the invite into the iOS app** (custom scheme/Universal Link):
  Universal Links need a public domain the LAN box doesn't have; the invite
  link is a browser signup artifact. TOFU on explicit user action matches the
  home-LAN threat model.
- **Overwriting any existing account's password on accept**: allowed only when
  the account holds no membership (a removed member being re-invited by an
  admin who personally delivered the link) — never for a live account.
