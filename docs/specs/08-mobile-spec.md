# Mobile Spec

Updated 2026-07-13 (roadmap = M83–M92 in `12-implementation-tasks.md`).
M83–M89 and M91 are implemented under `apps/ios/FamilyCFO` — foundation, advisor
chat with image/PDF/data-file attachments, on-device voice with the on-box
natural voice, the Overview dashboard (incl. M93 safe-to-spend), the receipt/W-2
camera flows, swipe-to-categorize transactions, and the review queues; see
`apps/ios/README.md`. M92 is implemented in the app (Siri intent, bill notifications, and the
widget's code + data layer); the widget's Xcode target + App Group capability
is a one-time UI step (see `apps/ios/README.md`). The iOS roadmap is complete.

The on-device photo-description backlog note below is **delivered for receipts**
(M89): Vision reads the receipt on the phone and only the text is sent. Chat's
own image attachments still use the server's vision model.

## Platform

iPhone app built with SwiftUI. Deployment target iOS 18+; iOS 26 APIs
(`SpeechAnalyzer`) used conditionally with fallbacks. Viewport reference
device: iPhone 15 Pro Max (matches the dashboard's 393pt discipline).

## Product scope (v1 → v1.x)

Ordered by milestone:

1. **Foundation (M83)** — generated Swift client from the OpenAPI contract,
   QR pairing, Keychain credential, Face ID unlock, TLS trust, remote
   access via the household's own VPN (Tailscale/WireGuard documented).
2. **Advisor chat (M84)** — the flagship screen. Conversations, grounded
   answers, image attachments (camera/library), PDF attachments.
3. **Data-file attachments (M85)** — CSV / spreadsheet / plain-text files
   attached in chat become bounded, grounded context for the answer.
4. **Voice v1 (M86)** — on-device speech both ways (Apple STT + system
   TTS); push-to-talk and hands-free modes.
5. **Natural voice (M87)** — on-box open-source TTS (Kokoro-82M; ADR 0018)
   streamed to the phone with barge-in.
6. **Overview (M88)** — the daily-glance dashboard.
7. **Camera flows (M89)** — receipt capture and W2 scan as first-class
   camera buttons.
8. **Review queues (M90)** — one-tap bill-suggestion and income
   confirm/reject.
9. **Quick categorization (M91)** — swipe-to-categorize transactions; one swipe files every same-merchant transaction at once (M91b); inline + starter-set category creation (M91a).
10. **System integration (M92)** — net-worth widget, Siri/App Intents into
    chat, local notifications for upcoming bills.

## Voice interaction (ADR 0018)

- **STT on the phone**: `SpeechAnalyzer` (iOS 26+) / `SFSpeechRecognizer`
  fallback. Raw audio never leaves the device; only the transcript is sent
  to the existing chat endpoint. Apple's on-device transcription is
  Whisper-class in accuracy at ~2x speed, which is good enough — no
  open-source STT is warranted.
- **The brain stays the grounded chat pipeline** — voice is a skin over
  `POST /chat/messages`; speech-to-speech models are rejected because they
  cannot drive the tool/guardrail architecture.
- **TTS**: v1 is `AVSpeechSynthesizer` (works offline, zero infra). The
  natural voice is Kokoro-82M (Apache 2.0) running as a small `tts`
  service on the box, streamed sentence-by-sentence, with mic barge-in;
  Chatterbox (MIT) is the designated upgrade engine. The app degrades to
  system TTS whenever the service is absent.

## Attachments in chat

- **Images**: JPEG/PNG/HEIC (transcoded) through the existing vision
  describe-then-ground path. On-device summarization (Vision framework /
  Foundation Models) remains the preferred long-term path — see backlog
  note below.
- **PDF**: server generalizes the W2 rasterize-pages approach (M77/M78) to
  chat attachments — pages become images for the vision describer.
- **CSV / spreadsheets / text**: the server extracts a bounded structured
  preview (headers, row/amount summaries) that joins the prompt as grounded
  context; nothing is written to the household's records unless the user
  explicitly runs an import. Size caps apply (M18 upload limits).
- **Video: explicit NON-GOAL.** The on-box models cannot process video and
  no plausible self-hosted path exists on this hardware; photos and PDFs
  cover the real use cases (receipts, statements, tax forms).

## Responsibilities

- Chat (text, attachments, voice)
- Camera capture (receipts, W2s, documents)
- Face ID local unlock
- Local notifications (bill due dates — computed on device)
- QR pairing with home server
- Secure authentication (Keychain, revocable device credential)

## Non-Responsibilities

- Financial reasoning (server-side, grounded)
- Long-term storage of household financial data
- Acting as the system of record
- Operator features (AI runtime management, monitoring, backups, imports
  administration, category *management* — rename/delete/reorganize) — these stay
  on the web dashboard. **Exception (M91a):** *creating* a category inline while
  categorizing is allowed on the phone (the `createCategory` endpoint), because
  a categorize screen with no categories and no way to add one is a dead end;
  full management still lives on the dashboard.

## Networking and trust

- **Client generation**: Swift client generated from
  `shared/openapi/family-cfo.v1.yaml` (contract-first, same as Angular);
  CI check added alongside the Angular drift check.
- **TLS**: the pairing QR carries the server certificate fingerprint; the
  app pins it (no CA installation dance). Re-pairing rotates the pin.
- **Remote access**: the app takes a configurable base URL; the documented
  path for off-LAN use is the household's own tailnet/VPN so the box is
  never exposed to the internet (ADR 0008).

## Apple AI and Vision

Use Apple's long-term supported frameworks where available:

- Vision Framework
- Foundation Models when appropriate and available
- Speech framework (`SpeechAnalyzer` / `SFSpeechRecognizer`) for STT

The iPhone may summarize images into structured JSON before sending to the server.

Example:

```json
{
  "merchant": "Costco",
  "item": "MacBook Air",
  "price": {
    "amount_minor": 149900,
    "currency": "USD"
  },
  "confidence": 0.96
}
```

Photos should remain on device whenever structured extraction is sufficient.

## Pairing Flow

1. User opens dashboard onboarding.
2. Server displays QR code (URL + certificate fingerprint + pairing secret).
3. iPhone scans QR code.
4. App confirms server identity (pins the fingerprint) and household.
5. Server creates device credential.
6. App stores credential in the Keychain behind Face ID.

## Acceptance Criteria

- Mobile API client is generated from OpenAPI (CI-checked).
- Face ID protects local app access where available.
- Pairing credentials are revocable from the dashboard.
- Raw microphone audio never leaves the device.
- Voice answers keep the grounded-numbers guarantee (same pipeline, same
  guardrails).
- Image capture sends structured JSON when possible.
- Every feature exposed on iOS reads through existing contract endpoints —
  no iOS-only data paths.

## Loans: entering the end of a loan or lease (M115)

Some statements state a maturity date; others only "N payments remaining." The
loan form accepts **either**, as a segmented choice ("End date" / "Payments
left"), and both store the same single fact — the maturity date (`payments
left` derives it as N months from today; switching modes carries the value
over, and `monthsLeft`/`dateAfter` are exact inverses, test-guarded). No new
schema field: one source of truth, two entries. A scanned statement that reads
a concrete date switches the form to date mode to show it.

The dashboard has the matching editor (M116): the "Debts & Loans" page mirrors
this capability set — list/add/edit/delete, statement scan via file picker or
paste (ADR 0028), and the same date-or-payments-left end entry with identical
derivation, test-guarded on both clients (ADR 0025 parity closed).

## Budgets & Goals on iOS (M118/M119)

Budgets (monthly per-category envelopes) and Goals (targets, progress, planned
monthly contribution) are full iOS screens with create/edit/delete, reachable
from the Overview's Budgets and Top-goal cards and the More tab's "Money"
section — ADR 0025 parity with the dashboard's pages. Goal deletion is a new,
undoable endpoint (`deleteGoal`, ADR 0023) available to both clients. The goal
form's planned-contribution field feeds the spending plan's savings term
(ADR 0027).

## Backlog: On-device photo description (from M21 / ADR 0011)

The web dashboard's chat photo attachments are described server-side by a small
vision model (`vllm-vision`), because Safari cannot reach Apple's on-device
models from a web page. The native iOS app should prefer describing the photo
**on the device** (Vision framework / Foundation Models where available) and
sending only the text description to `POST /chat/messages` — less data leaves
the phone and the server needs no vision model for iOS users.

## Foreign-currency accounts on iOS (M123 follow-up, #156, ADR 0075)

Parity with the dashboard (ADR 0025): the same rule, the same three places.

- **Overview.** `netWorthCard` shows "Not counted in {base} totals: {name}
  ({balance}) · …" below the value when `accountsOutsideBaseCurrency` is a
  non-empty list, each balance formatted in its own currency; nothing for `[]`
  or `nil`. The watch glance is untouched — it shows the base-currency figure.
- **Base currency, loaded and never guessed.** `AppModel.householdCurrency`
  (`HouseholdCurrencyProvider`) is seeded by every live context fetch through
  `LiveHouseholdAPI.onContext` — the callback that already seeds the household
  language — and resolved once by a screen opened first. Keyed by household id,
  device id and access token, invalidated whenever the credential's token
  changes (sign-in, pairing, sign-out; a rights refresh keeps the token and the
  value), single-flight, successes only cached, a late completion for an old
  session discarded. The live API's context callback carries the session and
  household it was built for and is dropped for any other, so a response that
  lands after a sign-out and a pairing as another household seeds nothing.
  A failed fetch shows a banner with the reason and a Retry on both screens;
  pull-to-refresh retries it too.
- **Accounts.** The Add button is disabled until the currency is known; the Add
  Account sheet enters the balance in that currency (no literal `$`). A manual
  account is created in the base currency; with it unknown nothing is saved and
  the error says so. A row held in another currency carries "Held in {currency}
  · not counted in {base} totals". The Emergency fund section totals
  base-currency reservations only and lists a foreign reservation beneath it,
  unadded, with a footer that says so; the rows are keyed by account id, since
  names are not unique.
- **Goals.** The Add button waits for the base currency; a new goal is declared
  in it. The shared form sheet formats both money fields in the goal's currency —
  the base for a new goal, the declared currency for an edit — and an edit is
  sent in the declared currency.
- Strings live in `Localizable.xcstrings` with `vi` and `lt` values.

## Box-Global Backup Retention on iOS (issue #116, ADR 0077)

The existing system-administrator Backups screen is an exception to the older
operator-features non-responsibility above. It manages the same box-global
configuration and recovery-status contract as the dashboard; server
`BACKUPS_MANAGE` authorization remains authoritative regardless of active
household role.

`BackupAPI` adds `recoveryStatus()` from generated OpenAPI and
`BackupConfigDraft` gains independent local/off-box policy, maximum, reserve,
and optimistic `updatedAt` fields. `@MainActor BackupViewModel` owns editable
policy drafts, validation, pending-prune preview, explicit save/activation,
recovery status, configuration conflict, and a distinct status error.

The screen adds matching **Retention and capacity** and **Recovery window**
sections:

- Each destination chooses Tiered or Keep every backup. Tiered fields are “Keep
  every backup for,” “Keep one per day through,” and “Keep one per week through”
  with `1 <= all <= daily <= weekly <= 3650` validation. Maximum size and
  minimum free-space reserve are independent.
- Destructive retention/cap/reserve edits remain a draft until **Save and
  activate retention** sends the current optimistic token and explicit
  confirmation. Field blur never activates or prunes. Pending prune count/bytes
  and migrated/restore review warnings are visible before confirmation.
- A 409 preserves the unsaved draft, loads current server configuration
  separately, and requires deliberate reconciliation; it never blindly retries
  stale values. Valid auto-saves are serialized/coalesced and cannot clear a
  pending review.
- Recovery renders configured target separately from observed candidates,
  qualified oldest/newest dates, visible and nullable exact-readable counts,
  number probed/probe completeness, coverage, capacity, anomalies, and disclosed
  remote-mtime fallback. Empty, building, met, incomplete, shortened, unknown,
  constrained, degraded, unavailable, and not-configured states retain distinct
  text.
- Every destination says that archive integrity and key correctness are checked
  only during restore. Copy says “oldest readable backup currently visible” or
  “recovery candidate,” never guaranteed restore point. The old “last 7” wording
  is removed.
- `LabeledContent`, `Picker`, numeric `TextField`, and text-bearing `Label`
  controls provide visible plus VoiceOver-readable destination/state/date
  semantics. Primary strings have Lithuanian and Vietnamese catalog values.

Configuration/status requests carry authenticated-session revision, request
generation, and the config token observed at start. Only a still-owned
completion may update state. Status refreshes after backup create, config save,
destination check, local/remote delete, and remote-list refresh. Only a current
failure clears stale recovery dates and shows unavailable copy; create, restore,
and delete remain usable when status is unavailable.

All generated Swift, app Swift, Xcode-project, and Apple tests are implemented
and verified only on macOS with the Swift toolchain, Xcode, and installed
iOS/watchOS platforms. Acceptance includes every state, validation and conflict,
reverse-order/session-replacement completions, refresh/clearing, VoiceOver and
localized copy, protocol-mock parity, client drift, and removal of “last 7.”

## Qualified and Unavailable Aggregates on Apple Clients (M124, ADR 0076)

The generated Swift client consumes the coordinated qualified-aggregate
OpenAPI contract; generated files are never hand-edited. Implementation and
validation require macOS, the Swift toolchain, Xcode, and installed iOS/watchOS
platforms.

Phone, iPad, Watch, widgets, Bills, Budgets, Income/Tax, and every Overview
current/month/year detail follow the same server-owned semantics as the web
client:

- Render each qualified leaf from `value`, including partial zero, with adjacent
  visible and VoiceOver-readable singular/plural omission copy.
- Render unavailable decisions as “Unavailable”/an em dash without success or
  danger coloring, percentages, progress, checkmarks, decision navigation, or a
  reconstructed running-balance view.
- Never sum qualified values or counts, derive net/remaining/coverage/rates,
  rebuild rankings, or substitute zero on device. Spending consumes the server
  `SpendingByCategory.total`; phone and Watch budgets consume the server
  `BudgetListResponse.summary`.
- Continue showing unaffected sibling cards. A qualified HTTP 200 is not a
  transport failure. A strict operation's documented 409 maps to incomplete
  stored data and remains distinct from 423/sign-in handling.
- Map generated DTOs into existing primitive Watch/widget snapshots. New
  completeness fields are optional for old-cache decoding; a successful 200
  with a null safety decision clears any stale cached decision rather than
  retaining it or coercing it to zero.

`OverviewViewModel` owns each load by household/session identity, requested
month, and monotonic generation. It cancels/replaces old optional tasks, commits
context independently of optional outlook/plan failures, and rechecks ownership
after every suspension and before state, loading, goal-name, notification,
snapshot, or widget side effects. Only an owner-valid current-context success
refreshes notifications/widgets; a late, cancelled, same-month, or prior-session
response commits nothing.

Acceptance covers exact count 0, partial nonzero, partial zero, pluralization,
unavailable decisions, unaffected sibling survival, strict 409 versus locked
423, stale-cache clearing, reverse completion/cancellation/session replacement,
and parity across phone, Watch, and widgets. Localized catalog and accessibility
assertions are required.
