# ADR 0036: Devices are managed per member — each member has their own pairing QR

## Status

Accepted.

## Context

The web "Users & Devices" page listed all paired devices in one flat list and had
a single global "Pair a new device" button. That doesn't match how a household
thinks about devices: a device belongs to a *person*. It also buried the common
task — "pair my wife's phone" — behind an undifferentiated button, and the intro
copy still carried a stale internal milestone label ("M6, macOS only").

The backend already supported pairing on behalf of a member
(`createPairingSession(user_id)`, gated by `devices.manage`; owners can only be
targeted by themselves — ADR 0034 / pairing rules), but the UI never exposed it,
and the `PairedDevice` response didn't say *which* member a device belonged to.

## Decision

**Devices are presented and paired per member. Each member row on the Users page
expands in place to show that member's devices and a "Pair a device" action that
mints a QR for that member — so every member gets their own code.**

- `PairedDevice` gains a nullable `user_id` (the member it's paired to). The web
  groups devices under their owner; devices with no/unknown owner (legacy
  pairings, removed members) fall into an "Other devices" section so nothing is
  hidden.
- **Self vs on-behalf** mirrors the server rule: pairing your *own* device sends
  no target (membership only); pairing for *another* member sends their id and
  requires `devices.manage`. The current user's own row auto-expands so a
  regular member can pair their phone from the dashboard without extra rights.
- Owners can only ever pair their own device (the row hides the on-behalf action
  for owner-role members; the server enforces it regardless).
- Role assignment uses `mat-select`, which reflects the member's current role
  correctly — the previous native `<select>` showed the first option ("Admin")
  for everyone (fixed by ADR 0035's Material migration).

Web-only: iOS has no member-management screen — a member pairs their own device
from the app's PairingView — so this is a dashboard surface, consistent with the
existing platform-bound exception (ADR 0025).

## Invariant

> A paired device is attributed to exactly one member (or none, for legacy
> devices). The UI may show a pairing action only where the server would allow
> it: your own device always, another member's only with `devices.manage`, and
> never an owner's on someone else's behalf.

## Rejected

- **Keep one flat device list.** Doesn't answer "whose phone is this?" and makes
  per-member pairing awkward.
- **Expose `user_id` only in the UI state, not the API.** The client can't group
  devices it isn't told the owner of; the attribution has to come from the server.
- **A separate "manage devices" page.** Devices and members are the same mental
  model here; splitting them adds navigation for no gain.
