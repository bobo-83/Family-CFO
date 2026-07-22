# 0059 — End-of-utterance keys on microphone energy, not transcript stalls

Date: 2026-07-21
Status: Accepted

## Context

Voice mode decides the user is done when the transcript sits unchanged for a
punctuation-aware silence threshold (M87a, `EndOfUtterance`). But on-device
recognizers (both `SFSpeechRecognizer` and the iOS 26 `SpeechAnalyzer`)
routinely stall their partial results mid-word on long utterances while
revising a large hypothesis. A user asking a long question was cut off
mid-sentence — "…can I rely on that when I ret" was auto-sent while they were
still speaking, because the transcript hadn't moved for 3 s even though the
microphone was still hearing them.

## Decision

Transcript movement proves *what* was said; audio energy proves the user is
*still saying it*. Each speech engine now runs a `VoiceActivityMeter` on its
existing audio tap (RMS per buffer against `max(absolute floor, adaptive
noise floor × 2.5)`) and exposes `lastVoiceActivity`. The silence watcher
takes `quietFor = min(time since transcript change, time since voice
activity)` — the turn only ends when BOTH have been quiet for the required
duration. After the user truly stops, the recognizer catches up and its final
transcript update resets the clock, so the full question is sent.

Steady non-speech noise (fan, traffic) must not hold the turn open forever: if
the transcript has sat unchanged for the threshold plus 10 s, the utterance is
sent regardless.

## Rejected options

- **Longer flat silence thresholds** — punishes every normal exchange with
  dead air, and no fixed value survives arbitrarily long recognizer stalls.
- **Recognizer-reported speech events** (`speechStartDetected` etc.) — not
  available on both engine paths, and the recognizer is exactly the component
  whose stalls caused the bug; the raw microphone is the ground truth.
- **Manual push-to-talk** — abandons the hands-free premise of voice mode.

## Invariant

Voice mode never ends a turn while the microphone is registering voice-level
audio (bounded by the noise escape hatch), regardless of what the transcript
is doing. Raw audio still never leaves the device (ADR 0018) — the meter
reads buffer energy only.
