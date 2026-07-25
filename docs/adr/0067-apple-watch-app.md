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

## Amendment (2026-07-25): v2 — spoken conversation and drill-downs

User feedback after first wear: talking beats typing, answers should be
audible, and the glance numbers need depth. v2 adds: a one-tap mic button
(watchOS dictation IS the voice input path — the Speech framework does not
exist on watchOS, so the phone's transcript-plus-audio-energy voice mode
cannot be ported; dictation gives the same speak-to-ask result), spoken
answers through the box's Kokoro voice with the on-device system voice as
fallback (never silent, ADR 0058 spirit), drill-down screens for
safe-to-spend and net worth (with the snapshot trend charted), and a third
page charting the year's monthly in/out from `GET /overview/yearly`.

## Amendment (2026-07-25, v3): the conversation loop

Single dictated questions weren't a conversation (user feedback). watchOS
still has no Speech framework, but the system input controller can be
presented PROGRAMMATICALLY (`presentTextInputController` via the visible
interface controller) — so the chat page now has a Talk button that loops:
dictate → grounded answer spoken aloud (always, in conversation) → the mic
re-opens for the follow-up, until the user cancels or taps End. Typing stays
a first-class path beside it (a keyboard-labelled input sheet). Learned the
hard way and verified in the simulator rig: bottom-bar toolbar items are
safe; top-bar items assert in this paging-TabView navigation context.

## Amendment (2026-07-25, v4): conversation management on the wrist

The chat page always resumed its in-memory thread; past conversations were
invisible and unmanageable from the watch (user request 2026-07-25). The
chat page now carries three in-content controls (top-bar toolbar items still
crash here — v3): **Chats** pushes the conversation list (same
`ConversationListViewModel` as the phone, moved to Shared along with the
transport-error describer so list rules and failure wording cannot drift —
a deleted row only leaves the list once the box confirms, and comes back if
it refuses), **New** clears to a fresh thread, and the speaker toggle.
Tapping a past thread reloads its turns from the box and continues in it.
Rejected: a fourth TabView page for history (the list is a chat affordance,
not a peer of Glance/Trend); a watch-local thread cache (the box is the
record; a stale local list would lie).

## Amendment (2026-07-25, v5): watch-face complication

"How do I add Family CFO to my watch face?" — faces need a complication,
which on modern watchOS is a WidgetKit extension; the watch app had none.
New `FamilyCFOWatchWidgets` extension (embedded in the watch app) offers a
"Money glance" complication in circular/corner/inline/rectangular slots. It
follows the phone widget's M92a contract exactly: NO network from the
widget — the watch app caches a `WatchFaceSnapshot` (left to spend, safe to
spend, 30-day low, net worth) to the shared App Group every Glance load and
nudges the timeline; the face shows the last-known number (left to spend
first, the Glance page's own priority) and the system can redact it
(`privacySensitive`) on a locked wrist. The snapshot lives in a third tiny
sync group (`FamilyCFOWatchShared`) compiled into both the watch app and the
widget — not `FamilyCFOShared`, whose whole API client the widget doesn't
need. Discovered en route: the phone's M92a
home-screen widget target was silently LOST when the pbxproj was hand-rebuilt
for the watch — `FamilyCFOWidget/` code existed with no target. Restored the
same day (user request): `FamilyCFOWidget` appex target re-wired, embedded in
the phone app, `OverviewSnapshot` moved to a `FamilyCFOWidgetShared` sync
group compiled into both, bundle id `com.familycfo.ios.widget`.

## Amendment (2026-07-25, v6): self-healing credential relay

The watch showed "the box answered unexpectedly" (a 401) right after an app
update: the phone only pushed the pairing on unlock, so the watch's relayed
token could lag a session rotation until the user happened to open the
phone app. Three fixes, all latest-wins: (1) on any non-OK glance/trend
response the watch asks the phone for its CURRENT pairing over the live
WCSession message channel (waking the phone app in the background; the
bridge answers from persisted state since nobody unlocked it) and retries
once; (2) the phone re-pushes the pairing every time it comes to the
foreground, not only on unlock; (3) the bridge activates at launch, not
first unlock, so requests can always be answered. Invariant: the watch
never keeps showing an auth error while the phone, in reach, holds a newer
credential.

## Amendment (2026-07-25, v7): graphical complications

Numbers-only face slots wasted the medium (user request 2026-07-25). The
small slots (circular, corner) now draw a SPEND RING — the fraction of the
month's expected income still free to spend, amount in the center, amber
under 20% — and the rectangular slot shows the month: In and Out bars
scaled against each other plus the left-to-spend line. The snapshot gained
optional month fields (income received, spent, expected income) so an older
cache still decodes and falls back to the text layout; the no-network and
privacy-redaction rules are unchanged.

## Amendment (2026-07-25, v8): the cash meter

The ring asked the wearer to read a proportion; the user wanted something
glanceable-er: the small slots now draw a PILE OF CASH — one bill (barely
covering the month) up to five (way more than needed), and a torn red bill
when left-to-spend is negative. Thresholds are the month margin as a share
of expected income (<5% -> 1, <15% -> 2, <25% -> 3, <40% -> 4, else 5),
drawn with pure vector shapes (no assets); the compact amount rides the
widget label. The rectangular month bars are unchanged.

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
