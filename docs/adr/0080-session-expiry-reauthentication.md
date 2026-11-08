# ADR 0080 — Session expiry and reauthentication

## Status

Accepted for implementation in [#164](https://github.com/bobo-83/Family-CFO/issues/164#issuecomment-6674058190). Amends ADR 0079 for existing-device reauthentication after an involuntary session end. Direct Apple Watch sign-in is tracked in #166.

## Lifecycle

An expired or rejected current session immediately loses protected UI, rights, and session-owned work and data. Expiry shows “Your session has expired. Please sign in again.” A current protected 401 with unknown expiry cause shows “Please sign in again to continue.” Keep the reason through failed attempts during the same sign-in visit. A fresh process may lose the reason. Successful sign-in clears it and opens Overview without resuming interrupted actions. Voluntary sign-out and transport failures create no expiry notice.

Use the issued UTC expiry at startup, during active use, on resume and before a protected request or biometric entry. Recheck after biometric completion. Cap remaining time with a process-local monotonic deadline and periodically recheck wall time while active. A missing or malformed expiry requires fresh sign-in with generic copy. The iPhone keeps trusted server and device identity separately from the bearer. The server remains authoritative for access.

Each install has a new non-secret session generation, including same-token relogin. Rights refresh retains its generation. Capture the actual dispatched credential and owner together. Only current-owned completions can update shared state, persist rights, invalidate or redirect. Web persistence keeps a pointer to the newest generation and an owner-specific credential record. Removing one owner's record cannot delete the next owner's credentials. Reconcile storage events against the current pointer. Existing browser records without expiry require one sign-in. Public operations and transport errors remain local.

A sealed household key lock is distinct from both auth and the biometric lock. Keep the new-member 423 body available to its form. On web, the first ordinary 423 requests password sign-in with no expiry message. If the same household remains locked after that sign-in, keep the new authenticated session for the existing key recovery flow. A successful read of protected household data ends that bounded recovery window. On iPhone, keep the single device-wrap unlock/replay attempt within the current generation. A 401 from either unlock subrequest invalidates that session.

## Existing-device proof

The challenge operation accepts the device ID without an old bearer. Issue an opaque 32-byte random challenge encoded as unpadded base64url. Give it a five-minute lifetime. Return the same response shape for unknown IDs. Apply separate IP throttling and bound live challenge storage. Issuance counts against its own burst-tolerant policy (30 per 300 seconds, answered with a 60-second pause), never the failed-password lockout: every device's renewal pays one issuance, and a shared home IP must not lock a household out of its own phones. Persist a hash of the challenge with its device ID and expiry, and prune expired entries. The completion request carries the device ID, challenge, email, password and base64url-encoded 64-byte P-256 signature (raw big-endian r followed by s). Sign the UTF-8 bytes of the domain string `family-cfo/device-reauth/v1`, a newline, the device ID, a newline and the exact encoded challenge, with no trailing newline. Use ECDSA with SHA-256. The API and iPhone must pass a shared verification vector before release.

Verify the password through the shared login IP/account throttle and verify the signature against the registered device public key. Validate that the device belongs to the account, is active and still belongs to a household the account can access. Within one transaction, serialize with device revocation and other renewals, consume the challenge, retire existing device bearers and issue the next device-bound bearer using the existing device TTL. Expired, replayed and unauthorized completions share a generic rejection. Never log request credentials or proof. Keep the device row, trust, private key and household wraps. Audit the renewal inside the transaction with a fixed public summary so a sealed household does not need its data key to complete sign-in. Failure to install a new local bearer compensates by revoking only that bearer. A member with unavailable pairing can explicitly choose device setup.

## Companion

Publish expiry and explicit invalidation using an ordered, versioned phone context that survives relaunch. Persist the Watch's last accepted ordering and reject stale or rejected generations, including after restart. A deliberate companion reset establishes a new ordering scope. An owned Watch 401 clears cached content and can request newer phone state once. Missing replies or temporarily inaccessible Keychain mean unavailable. On-device expiry clears app and complication data without contact. Phone invalidation arrives on contact. Legacy Watch contexts require a fresh context with expiry. The phone retains its private key and share-inbox attachments.

## Invariants and tradeoffs

A stale request cannot revive or displace a newer session. A valid existing iPhone device can recover even after the old session row is pruned. Using the existing refresh endpoint would require an unexpired bearer and lose device binding. New-device enrollment would replace the key and create another device row. A one-time web sign-in avoids inventing an expiry for an existing opaque token. Authentication and credentials are outside the advisor registry.

## Interoperability test vector

The following public values are synthetic. The signature verifies under the
registered public key for the exact challenge and device ID shown.

| Field | Value |
| --- | --- |
| Device ID | `00000000-0000-4000-8000-000000000164` |
| Challenge | `AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8` |
| Public key (base64 of raw x and y) | `axfR8uEsQkf4vOblY6RA8ncDfYEt6zOg9KE5RdiYwpZP40Li/hp/m47n60p8D54WK84zV2sxXs7LtkBoN79R9Q==` |
| Signature (base64url of raw r and s) | `k0W1YxjHtcknC23YxrA1h7_Vz6sSSud8QYL2O7M87g1XZERRTUVkwsVPezlVnR5QNFIFF_11z6LaW7F_8ws3mw` |
