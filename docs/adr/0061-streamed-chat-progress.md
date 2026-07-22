# 0061 — Streamed chat progress; the answer stays whole and verified

Date: 2026-07-22
Status: Accepted

## Context

Advisor turns legitimately take 15–90 s (3–5 grounded model rounds). Two
resulting problems, both user-reported: the wait feels dead (a frozen
"thinking…" bubble), and while the model works the HTTP socket carries zero
bytes — weak WiFi drops exactly such idle connections (nginx 499), losing
answers the box then saves anyway.

The grounding guardrail (ADR 0009) validates the COMPLETE answer before
anyone sees it. Token-by-token streaming would put unverified numbers on the
user's screen and require retracting text after a failed check.

## Decision

`POST /chat/messages/stream` (SSE), sharing `_chat_turn` with the plain
endpoint, which remains for compatibility:

- `progress` events narrate the loop in real time: reading an attached
  photo, thinking, each tool call (friendly labels — "Solving for your
  retirement age"), and the guardrail's corrective retry ("Double-checking
  the figures").
- Exactly one `answer` event carries the full `ChatResponse` — sent only
  after the guardrail passed. **The answer is never streamed token-by-token**
  (decided with the user, 2026-07-22): what streams is what the advisor is
  *doing*, never unverified content.
- SSE comment keepalives (`: ping`) flow every 5 s, so the socket is never
  byte-idle — the 499 class of loss disappears on streaming clients.
- A disconnected client does not cancel the turn: the worker finishes and
  persists, so the clients' SavedAnswerRecovery still finds the answer.

Both clients (iOS + Angular, ADR 0025) consume the stream: live progress in
the thinking bubble / voice status line, answer handling unchanged.

## Rejected options

- **Raw token streaming** — puts ungrounded numbers on screen before
  validation; retraction after a failed guardrail is worse than waiting.
  Explicitly declined by the user when offered.
- **WebSockets** — bidirectional machinery for a one-way stream; SSE rides
  plain HTTP through the existing nginx proxy untouched.
- **Client polling for progress** — more requests, more latency, and does
  nothing for the idle-socket drops that motivated this.

## Invariant

Nothing user-visible from the advisor is ever sent before the grounding
guardrail validated it — streaming narrates activity, not draft content.
