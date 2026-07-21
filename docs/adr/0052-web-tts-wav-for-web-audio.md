# ADR 0052: The web reads answers aloud with WAV via Web Audio; iOS keeps MP3

## Status

Accepted. Extends ADR 0018 (advisor voice) and the M87a read-aloud feature.

## Context

Read-aloud used the on-box Kokoro voice on both clients, proxied through
`POST /voice/tts` as MP3. On the web it never actually spoke in the Kokoro voice
— it always fell back to the browser's synthetic voice, and (separately) couldn't
be stopped.

Two root causes, found by probing the box:

- **Synthetic voice, not Kokoro**: Kokoro's MP3 begins with an **ID3v2.4 tag**
  (`ID3\x04…`). Safari's Web Audio `decodeAudioData` is unreliable decoding
  ID3-tagged MP3 and throws, so the code fell through to `speechSynthesis`. (The
  earlier `<audio>`-element attempt was blocked by mobile-Safari autoplay; moving
  to Web Audio was correct, but MP3 decode was the remaining wall.) iOS's
  `AVAudioPlayer` plays the same MP3 fine, which is why native worked.
- **Can't stop**: the web speak button was `[disabled]` while speaking, so a
  second tap (which the handler already treats as stop) never fired.

## Decision

- Add an optional **`format` (`mp3` | `wav`, default `mp3`)** to `VoiceRequest`.
  The proxy passes it to Kokoro as `response_format` and sets the response
  `media_type` accordingly.
- **The web requests `wav`.** Web Audio decodes PCM WAV reliably across browsers,
  so the fetched Kokoro audio plays in the natural voice; the synthetic voice
  stays only as the last-resort fallback when no voice service is configured.
- **iOS keeps `mp3`** (the default) — `AVAudioPlayer` handles it and MP3 is ~10×
  smaller than WAV.
- The web read-aloud button is no longer disabled while speaking; it toggles to a
  **Stop** control.

## Invariant

> Web read-aloud fetches WAV and plays it through a Web Audio `AudioContext`
> resumed inside the tap; iOS fetches MP3. The synthetic browser voice is only a
> fallback for when the box has no voice service. The read-aloud control can
> always stop playback.

## Rejected

- **Strip the ID3 tag from the MP3 in the proxy**: fragile — Safari's MP3 Web
  Audio decoding has other edge cases; PCM WAV sidesteps the codec entirely.
- **Serve WAV to everyone**: needless ~10× bandwidth for iOS, which decodes MP3
  fine; the per-client `format` keeps each on its best container.
- **Transcode server-side to a "clean" MP3**: added CPU and dependencies for no
  gain over WAV.
