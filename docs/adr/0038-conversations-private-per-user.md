# ADR 0038: Advisor conversations are private to the member who created them

## Status

Accepted.

## Context

Advisor chat threads were scoped by household only: `list_conversations`,
`get_conversation`, and `delete_conversation` filtered on `household_id`, so any
member saw — and could open, continue, or delete — every other member's chats.
A spouse could read the other's conversation and vice versa. Chats are personal
(what you ask the advisor, the photos you attach), and must not be shared across
the household even though everyone belongs to the same household.

Conversations already record `created_by_user_id`; nothing but the queries was
missing.

## Decision

**A conversation is private to the member who created it. Every conversation
read/write is scoped by `created_by_user_id == session.user_id`, in addition to
the household.**

- `list_conversations(household_id, user_id)`, `get_conversation(…, user_id)`,
  and `delete_conversation(…, user_id)` all require the caller's user id and
  filter on it. The API passes `session.user_id`; there is no code path that
  lists another member's threads.
- The chat endpoint resolves `conversation_id` through the same scoped
  `get_conversation`; a member posting with someone else's conversation id gets
  a fresh thread of their own rather than appending to another member's.
- This is enforced **server-side**, so both clients (iOS and web) inherit it
  from the shared contract — no client change needed.

### Memory is deliberately different

Extracted advisor **memories** are shared household *financial* context (e.g.
"the household has a 3% mortgage"), not personal chat content, so the memory
backfill still spans the whole household via a dedicated
`list_all_conversation_ids` (internal only). The private thing is the
conversation; the shared thing is the financial fact.

## Invariant

> No endpoint returns, opens, or deletes a conversation the caller didn't
> create. A new member sees only their own advisor threads. Any new conversation
> query must take a user id and filter on it — never household-only.

## Rejected

- **Household-shared conversations with a per-user filter only in the UI.** The
  server would still hand a member another's thread by id; privacy has to be a
  server rule, not a client courtesy.
- **Scoping extracted memories per user too.** Memories are shared household
  financial facts, not personal content; per-user memory would fracture the
  advisor's household picture for no privacy gain. Revisit only if memories ever
  capture genuinely personal (non-financial) content.
