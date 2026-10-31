# Account sign-in on iPhone

Issue #162 / ADR 0079 retires QR sign-in completely. There are no QR creation,
scan, paste or redemption paths, including for older apps.

## First sign-in

1. The first owner creates the household/account in the dashboard. For another
   member, use Users → invite and share the one-time join link yourself. The
   member accepts it in their browser and chooses their own password. Do not
   share passwords or sign everyone's phone in as the owner.
2. Install/update the iPhone app. Enter the HTTPS dashboard address (including
   its port; 8443 is the default). For remote access, connect the household VPN.
3. Verify the server identity. A publicly trusted certificate must validate for
   that hostname. For a self-signed certificate, compare the **full** fingerprint
   shown by the app with the operator over a separate trusted channel before
   confirming. Never send a password to an HTTP address.
4. Sign in with your own account. The private key stays on the phone and the
   revocable device token is saved in Keychain. The device appears under your
   membership in Devices; the usual device-management right controls revocation.
   Face ID/passcode remains a local unlock, not a substitute for server access.

## Sign out, lost access and certificate rotation

Sign out removes the token/private key and watch access but keeps the server
address and trust settings, including after relaunch. Sign back in with your
account; a saved self-signed pin is not silently re-captured. Choose **Change
server** on the sign-in screen to explicitly set up a different server or
confirm a certificate the operator has deliberately replaced. **Forget this
server** in Settings also clears server settings locally; revoke a lost device
from another signed-in device or the dashboard.

An expired/revoked session needs a new account sign-in. Save your password in
your own password manager before signing out of the last working session.
There is **no forgotten-password reset/email recovery flow**. Password change
requires the current password. Ask the operator if you lose it; do not give a
password or recovery key to an AI agent. Removing/re-inviting a member revokes
sessions and changes membership; it is not a non-destructive password reset.

## Encryption recovery is separate

Existing device/member wraps and the Restore keys recovery-key flow are
unchanged. Existing signed-in devices can still unlock a sealed household.
A new account/invite alone cannot recover lost encryption keys. See
[disaster recovery](disaster-recovery.md) and ADR 0072: losing every usable
password/device, recovery key and master key loses access to the data.

## Coordinated upgrade (contract 0.162)

Publish the API/worker, then the dashboard, then the iOS app (with watch).
Update all components as one coordinated release. Older QR-dependent apps must
update, not use a legacy endpoint. Already-issued QR/account device sessions
remain valid until normal expiry/revocation; this update does not force logout.
All outstanding QR codes are discarded by migration `0097_retire_qr_pairing` and
cannot be redeemed. A schema rollback recreates an empty table, not old secrets;
roll back matching component contracts only.

The historical QR incident is retired/superseded, **root cause unresolved**.
Account-login diagnostics classify wrapped TLS/network/serialization errors
without displaying raw exception/request/response content. Retiring QR is not
proof that its operational trigger was diagnosed.
