# 0067 — Apple Watch app: glance + advisor chat, credential via the phone

Date: 2026-07-24
Status: Accepted

## Context

The user asked for the Overview and advisor chat on their Apple Watch. The
watch must obey the same rules as every client: pinned TLS to the box, a
revocable credential, grounded answers only.

## Decision

- A `FamilyCFOWatch` watchOS target embedded in the iPhone app. Shared code
  (generated client, networking with certificate pinning, AdvisorAPI with the
  streamed chat + SavedAnswerRecovery, money formatting) moved to a
  `FamilyCFOShared` folder compiled into both targets — one implementation,
  two screens (the ADR 0025 parity spirit applied to a third client).
- **Credential relay, not a second pairing**: the phone pushes
  `{apiBaseURL, certificateSHA256, token, householdName}` over
  WatchConnectivity application context on unlock/pairing, clears the token
  on sign-out/unpair, and the watch persists its copy so it works standalone
  (same WiFi/VPN reachability as the phone). Revoking the device on the
  dashboard kills the shared token server-side — the watch dies with the
  phone credential, one revocation surface.
- Watch UI v1: a vertical pager — the glance (safe-to-spend, net worth,
  monthly in/bills, emergency-fund months from the same `GET /household`
  context every client renders) and the advisor chat (dictation input,
  streamed progress narration per ADR 0061, validated answers, saved-answer
  recovery).
- Deploys ride the existing path: the watch app ships inside the iPhone
  bundle; `deploy-ios.sh` verifies the embedding and iOS pushes it to the
  paired watch automatically.

## Rejected options

- **Independent watch pairing (QR/login on the watch)** — no camera, painful
  input, a second credential lifecycle to manage; the phone relay gives the
  same result with zero new auth surface.
- **A watch-specific slim API** — the watch renders the same household
  context and chat pipeline as everyone else; a parallel API would drift
  (ADR 0025's reasoning).

## Invariant

The watch holds no credential the phone didn't give it, and every number it
shows comes from the same deterministic context or guardrail-validated
answers as the other clients.
