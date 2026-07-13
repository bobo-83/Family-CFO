# ADR 0018: iOS Voice Architecture

## Status

Accepted (planned — M86/M87; implementation blocked on macOS/Xcode hardware).

## Context

The iOS app's flagship interaction is voice: the user asks a question out
loud, the advisor answers out loud, and it should feel as natural as
possible. Constraints, in order: privacy (ADR 0008 — audio is the most
intimate data the app will ever touch), the grounding architecture (ADR
0003/0009 — the answer must come from the tool-calling chat model, never
from a model that free-associates), no GPU contention with the serving
models (the box runs an 80B chat + 8B vision model), and every component
replaceable (ADR 0007).

Options evaluated (2026-07):

- **Speech-to-text.** Apple's on-device `SpeechAnalyzer` (iOS 26) matches
  Whisper-class accuracy at roughly twice the speed, entirely on the phone.
  Server-side open-source alternatives (Whisper large-v3-turbo, NVIDIA
  Parakeet) are marginally better on some benchmarks but add latency, ship
  raw audio off the phone, and consume box resources.
- **Text-to-speech.** Apple's `AVSpeechSynthesizer` is reliable but
  noticeably synthetic — the weak link for "natural." Open-source leaders:
  Kokoro-82M (Apache 2.0, 82M params, ~2–3 GB, faster than real time even
  on CPU), Chatterbox (MIT, 0.5B, preferred over ElevenLabs by 65.3% of
  blind-test listeners, voice cloning + emotion), Orpheus 3B (Apache 2.0,
  most expressive, wants 8–12 GB GPU).
- **Speech-to-speech models** (Qwen3-Omni, Moshi) take audio in and produce
  audio out natively — and would replace the grounded tool-calling brain
  with a model that cannot call our tools reliably. That breaks the
  system's core guarantee.

## Decision

1. **Listen on the phone.** STT is Apple's on-device Speech framework —
   `SpeechAnalyzer` on iOS 26+, `SFSpeechRecognizer` fallback below. Raw
   audio NEVER leaves the phone; only the transcribed text is sent to the
   existing `POST /chat/messages`. Best latency, best privacy, zero server
   load, no new dependency.

2. **Think in text, exactly as today.** The transcript flows through the
   unchanged grounded chat pipeline (tools, guardrails, memory, retrieval).
   Voice is an input/output skin, never a second brain. Speech-to-speech
   models are explicitly rejected.

3. **Speak from the box, behind a seam.** A new `tts` compose service runs
   Kokoro-82M (Apache 2.0) — small enough for CPU/minimal GPU so it cannot
   contend with the chat model — exposed to clients only through the API
   (`POST /voice/tts`, streamed audio). The engine is a replaceable seam:
   Chatterbox (MIT) is the designated upgrade when voice cloning or richer
   emotion is wanted and the compute budget allows.

4. **Degrade gracefully.** When the `tts` service is absent or unreachable,
   iOS falls back to `AVSpeechSynthesizer` — voice v1 (M86) ships entirely
   on-device before the box-side voice exists, and the box-side voice
   (M87) is additive polish.

5. **Naturalness mechanics.** Responses are sentence-chunked and streamed
   to playback as they synthesize (time-to-first-audio over total time),
   and the mic interrupts playback (barge-in) so the user can talk over
   the assistant like a person.

## Consequences

- Voice quality is bounded by Kokoro until the Chatterbox upgrade; both are
  permissively licensed and self-hosted, so no audio or text touches a
  cloud API.
- The `tts` container adds ~2–3 GB of memory budget to the compose stack;
  it is optional and off the GPU path.
- On-device STT accuracy on heavy accents/noise is Apple's ceiling; if it
  disappoints, a server-side Whisper endpoint can be added behind the same
  seam without changing the client contract.
- The chat pipeline stays synchronous text; no protocol change is required
  for M86. Sentence-streaming TTS (M87) needs a chunked/streaming response
  on the new endpoint only.
- On-device Kokoro (running the model on the iPhone itself, not the box) is
  feasible on an A17 Pro / iPhone 15 Pro Max (82M params, community MLX/Core
  ML ports exist) and would give zero-latency, offline, away-from-home voice.
  It is deliberately NOT the first target: the mature tooling is server-side
  Python, the iOS path needs model conversion plus an on-device
  text-to-phoneme frontend and ~350 MB of app payload, and the box round-trip
  on the home tailnet is inaudible with sentence-streaming. The M87 seam (the
  app already falls back when the service is absent) accommodates a
  client-side engine later with no redesign, so this stays a post-v1 option
  rather than a rejected one.

## Implementation notes (M87a, server + web)

The box-side voice shipped ahead of the iOS client (Mac-independent work):
the `tts` compose service is `ghcr.io/remsky/kokoro-fastapi-cpu` (multi-arch,
model baked in, CPU-only), and `POST /voice/tts` proxies its
OpenAI-compatible `/v1/audio/speech`, streaming MP3 and returning 503 (→
client fallback) when unset or down. Verified on the aarch64 box: a real
sentence synthesized to 54 KB of valid MP3 in ~1.0 s. The web dashboard's
chat gained a Read-aloud button so the voice is auditionable before any Swift
exists.
