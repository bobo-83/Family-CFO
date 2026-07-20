# ADR 0044: Members rate advisor answers; the study job learns from it

## Status

Accepted.

## Context

A member wanted to rate the advisor's answers and have it "learn from my
feedback so next time it could be smarter." The app already learns without
touching model weights (ADR 0040): the idle-time study job distills knowledge
into household memories that are injected into every chat. Feedback should ride
the same rails — a rating is another signal for that job to learn from — rather
than fine-tuning or a separate ML loop.

Every advisor answer already carries a `recommendation.id` end-to-end (live
responses and history rows), so a rating has a stable key with no new plumbing.

## Decision

**A member gives an advisor answer a 👍 or 👎 (with an optional note). The idle
study job later reviews the flagged answers and distills a durable lesson into
household knowledge, then marks the feedback reviewed.**

- `POST /chat/feedback {recommendation_id, rating, note?}` → `advisor_feedback`
  (one row per recommendation × member; re-rating updates in place and resets
  `reviewed`). The endpoint scopes the recommendation to the caller's household
  (a member can't rate — or probe — another household's answers).
- The study job's existing idle tick (`run_study_tick`) now also drains the
  feedback queue: for each unreviewed rating it hands the runtime the answer,
  the up/down verdict, and the note, and asks for a stable-key **steering
  lesson** ("Always include RSU vests when estimating income."). Lessons are
  stored as `source="study"` memories, so they steer every future answer AND
  appear on the Advisor-knowledge screen beside the studied insights — feedback
  becomes part of what the advisor knows. Bounded to a few per tick.
- Both clients render 👍/👎 on every assistant answer (live and history —
  history rows now thread `recommendation_id` through), optimistic with revert
  on failure, gated on the recommendation id (ADR 0025 parity).

## Invariant

> Feedback is a learning signal for the study job, not a model-training input:
> a rating produces a reviewable, deletable steering note in household memory —
> never a weight change. Rating is scoped per household and per recommendation.
> The idle study tick is the single place feedback is distilled.

## Rejected

- **Rating → immediate steering note the user writes** (the recommended option
  in the design question): rejected in favor of the study job inferring the
  lesson, so a bare 👎 with no note still teaches something and the family
  doesn't have to author rules. The user chose this.
- **Fine-tuning on 👍/👎 (RLHF-style)**: violates ADR 0040 — stale, unauditable,
  GPU-contending. Feedback distilled into memory stays current and deletable.
- **A separate feedback-review worker job**: folded into the existing idle study
  tick instead, so one runtime selection and one idle gate cover both, and
  feedback can't compete with a human using the advisor.
- **Rating without household scoping**: a recommendation id is a UUID, but an
  endpoint that didn't check ownership would let one household probe another's
  answer existence. Scoped server-side.
