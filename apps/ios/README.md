# iOS App

SwiftUI iPhone app (iOS 18+). Implements M83 (foundation) and M84 (advisor
chat); the remaining roadmap is M85–M92 in
`docs/specs/12-implementation-tasks.md`. Spec: `docs/specs/08-mobile-spec.md`.

## Layout

```
apps/ios/
  FamilyCFO/                  Xcode project (app + unit tests)
    FamilyCFO/
      App/                    entry point, app state machine, role-aware shell
      Pairing/                QR scan/paste → confirm → POST /pairing/confirm
      Networking/             generated-client factory, cert pinning, auth middleware
      Security/               Keychain store, Face ID gate
      Chat/                   advisor chat (M84): conversations, attachments
      APIClient/Generated/    committed generated client — DO NOT EDIT
    FamilyCFOTests/           unit tests (Swift Testing)
  openapi-generator/          SPM tool package that runs swift-openapi-generator
```

The app icon's master art is `shared/brand/icon.svg` (also the web
dashboard's favicon). To regenerate the rasters after editing it:
`qlmanage -t -s 1024 -o . shared/brand/icon.svg` for the asset-catalog
1024px PNG, `sips -z <n> <n>` for the web PNG sizes.

## Build and run

Open `apps/ios/FamilyCFO/FamilyCFO.xcodeproj` in Xcode 16+ and run the
`FamilyCFO` scheme, or from the command line:

```sh
cd apps/ios/FamilyCFO
xcodebuild build -project FamilyCFO.xcodeproj -scheme FamilyCFO \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
xcodebuild test -project FamilyCFO.xcodeproj -scheme FamilyCFO \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

CI runs the same tests plus the client drift check (`.github/workflows/ios.yml`).

## Generated API client

The Swift client is generated from `shared/openapi/family-cfo.v1.yaml`
(contract-first, ADR 0005 — same discipline as the Angular client) and
committed under `FamilyCFO/APIClient/Generated/`. After any contract change:

```sh
scripts/generate-swift-client.sh          # regenerate
scripts/generate-swift-client.sh --check  # what CI runs
```

Never edit the generated files by hand; no hand-maintained DTOs.

## Pairing and trust (M83)

1. On the dashboard, an owner/adult opens **Admin → Devices** and generates a
   one-time (10-minute) pairing QR.
2. The app scans it (or accepts the pasted payload when no camera is
   available, e.g. in the simulator) and shows the server identity —
   household, URL, certificate fingerprint — for confirmation.
3. On confirm, the app calls `POST /pairing/confirm` and receives a
   revocable 30-day device credential, stored in the Keychain
   (device-only, non-migrating). The QR's `certificate_sha256` is pinned:
   every TLS connection must present exactly that certificate, which is how
   the home server's self-signed cert becomes trustworthy with no CA
   install. Re-pairing rotates the pin.
4. Face ID (or passcode) gates the UI on every launch where local
   authentication is available.
5. The credential carries the household `role`; the shell adapts
   (owner/adult/viewer) and operator features stay on the web dashboard.

Revoke a device any time from the dashboard's Devices page (owner-only);
the app's Settings page can also unpair locally.

## Remote access

The app talks to whatever base URL was in the pairing QR. The supported
off-LAN path is the household's own tailnet/VPN (Tailscale/WireGuard) so the
box is never exposed to the internet (ADR 0008): install the VPN on the
phone, make the server reachable under the same name/IP as in the QR, and
nothing else changes. If the dashboard is reached under a different hostname
than the phone will use, generate the QR from the address the phone can
reach, since the QR embeds the dashboard's own origin.

## Advisor chat (M84)

- Conversations and history come from the existing `GET /conversations`
  endpoints; answers from `POST /chat/messages` — the same grounded
  pipeline, memory, and retrieval as the web dashboard. No iOS-only data
  paths.
- Attachments: photos (camera or library; HEIC transcoded to JPEG, large
  images downscaled) and PDFs, base64-inlined per the contract with the
  M18 upload cap enforced client-side too.
- Grounded metadata (confidence, warnings) renders under each answer.

## Voice (M86)

Two ways to talk to the advisor, both fully on-device (ADR 0018):

- **Push-to-talk**: the mic button in the chat bar dictates into the text
  field — the transcript stays editable before you send it.
- **Hands-free**: the waveform button opens a voice conversation. Speak;
  after ~1.6s of silence the transcript goes through the same grounded
  chat pipeline as typed messages; the answer is read aloud (markdown
  stripped); listening resumes. Tap the orb to interrupt an answer.

STT is the iOS 26 `SpeechAnalyzer` (the system downloads its language
model on first use), falling back to `SFSpeechRecognizer` pinned to
on-device recognition — if a device/language can't transcribe locally the
feature fails rather than send audio to Apple's servers. Raw audio never
leaves the phone; only the finished transcript hits `POST /chat/messages`.
Replies speak through `AVSpeechSynthesizer`. M87 upgrades the voice to the
on-box Kokoro TTS stream with acoustic barge-in.
