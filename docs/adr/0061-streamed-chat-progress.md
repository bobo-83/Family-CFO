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

## Amendment (2026-09-03): bounded recovery tells the truth when it expires

Saved-answer recovery on Apple clients is deliberately bounded to about two
minutes beyond the failed request. Only a timeout or lost stream enters that
poll; authentication, server, and offline failures surface immediately. If the
answer appears during the poll, the client shows it and no error. If the poll
expires, a lost or timed-out stream is described as a connection that dropped
(or a request that timed out) while the advisor was working, with a prompt to
reopen the conversation later — never as proof that Local Network access or
Wi-Fi is misconfigured, and never as an invitation to immediately resend a
turn the box may have saved (issue #124).

## Amendment (2026-09-04): recovery follows a bounded whole turn

Issue #125 replaces the arbitrary two-minute recovery window with one server/client contract. An advisor turn has a 600-second default monotonic deadline (`FAMILY_CFO_CHAT_TURN_TIMEOUT_SECONDS`): attachment analysis, every model/tool round, the grounding retry, fallback selection, and persistence share it. The healthy per-model token-budget timeout remains independent but is capped by the turn time remaining. Expiry is terminal (HTTP 504 before a plain response, or `advisor_turn_deadline_exceeded` on an open stream); no new fallback or persistence stage starts after it.

A successful streamed response advertises its remaining `X-Advisor-Recovery-Horizon-Seconds`. Apple clients capture the header from the raw response fields through a client middleware — the frozen oldest compatible contract (ADR 0074) generates no accessor for it — and convert that duration—not a cross-device timestamp—to a local monotonic deadline, check immediately, then back off to a 15-second polling cap until one final lookup at the deadline. The same 600-second horizon is the compatibility fallback for an older server or a failure before response headers arrive. The detached worker remains detached, but it is no longer unbounded; post-response memory extraction remains outside this contract.

Exhaustion is described truthfully. When recovery polled past a server-advertised horizon, the bounded turn provably persisted nothing — the copy says the turn didn't complete and that resending is safe, which no longer conflicts with the issue-#124 rule because nothing can have been saved. Without an advertised horizon the server may be an unbounded pre-M95 box still working, so the copy keeps the "check this conversation before resending" caution. Server-side, the deadline gates the *start* of persistence: once the recommendation write begins, the conversation turn is committed with it even if the deadline lapses mid-write, so a 504 or terminal stream error always means nothing was saved.

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
