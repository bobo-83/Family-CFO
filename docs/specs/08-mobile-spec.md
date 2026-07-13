# Mobile Spec

Updated 2026-07-13 (roadmap = M83–M92 in `12-implementation-tasks.md`).
M83–M88 are implemented under `apps/ios/FamilyCFO` — foundation, advisor chat
with image/PDF/data-file attachments, on-device voice with the on-box natural
voice, and the Overview dashboard; see `apps/ios/README.md`. M89–M92 (camera
flows, review queues, quick categorization, widget/Siri) remain spec-gated and
ready to build.

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
9. **Quick categorization (M91)** — swipe-to-categorize transactions.
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
  administration, category management) — these stay on the web dashboard

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

## Backlog: On-device photo description (from M21 / ADR 0011)

The web dashboard's chat photo attachments are described server-side by a small
vision model (`vllm-vision`), because Safari cannot reach Apple's on-device
models from a web page. The native iOS app should prefer describing the photo
**on the device** (Vision framework / Foundation Models where available) and
sending only the text description to `POST /chat/messages` — less data leaves
the phone and the server needs no vision model for iOS users.
