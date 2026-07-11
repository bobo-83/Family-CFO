# ADR 0016: Household Memory and Conversation Summarization

## Status

Accepted (M57).

## Context

The advisor's only memory is the active conversation's last 8 messages (M30).
Facts the family states in chat — where they live, how many kids they have,
how often they eat out — are forgotten the moment the thread ends, and even
inside one long thread anything older than the window silently drops. The user
explicitly wants the opposite: durable recall of personal facts across **all**
conversations, and deleting a conversation should not lobotomize the advisor.

Constraints:

- Local-only (ADR 0008): extraction and summarization must run on the on-box
  runtime; no data leaves the server.
- Grounding (ADR 0003/0009): any numbers the model repeats from memory must
  join the grounded set, or the guardrail would reject honest recall.
- The model runtime is optional: with AI disabled, chat still works
  deterministically, so memory extraction must be a best-effort side effect,
  never a request dependency.

## Decision

1. **Derived-fact store.** A `household_memories` table holds short facts as
   `(key, value)` rows, unique per `(household_id, key)`. Keys are stable
   snake_case identifiers produced by the extractor (`home_city`,
   `kids_count`, `eating_out_frequency`), so a later statement ("we eat out
   5 times a week now") **updates** the fact instead of duplicating it.
   `source_conversation_id` is an informational column with **no foreign
   key**: memories deliberately survive conversation deletion.

2. **Extraction as a post-response side effect.** After each chat exchange the
   API schedules a FastAPI background task that asks the household's own
   runtime to extract durable facts from the user's message (strict-JSON
   prompt, temperature 0, parsed defensively — garbage output means no
   memories, never an error). Chat latency is unchanged; a failed extraction
   only logs. The photo description participates (it is already text by
   ADR 0011).

3. **Rolling conversation summary.** The same background task maintains
   `conversations.summary`: once a thread exceeds the 8-message window,
   everything older than the window is summarized (~150 words, concrete
   figures kept) by the runtime and stored on the conversation.

4. **Injection + grounding.** Chat prepends two context messages when
   available: "Known household facts" (all memories, bounded) and "Earlier in
   this conversation" (the stored summary). Numbers appearing in either join
   `known_values`, exactly like conversation history (M30), so recalling
   "you have 2 kids" or a summarized figure passes the guardrail.

5. **Transparency and control.** `GET /memories` lists every stored fact,
   `POST /memories` lets a family teach one directly, and
   `DELETE /memories/{id}` forgets it — surfaced on a dashboard "Advisor
   memory" page. Deleting a **conversation** keeps its extracted facts (the
   user's explicit requirement); deleting a **memory** is the forget
   operation, and both mutations are audited.

6. **Backfill.** On worker startup, households with no `_backfill_done`
   marker get their existing conversations' user messages run through the
   extractor once, so facts stated before this feature existed are recovered.
   Conversations deleted before M57 were hard-deleted and are honestly
   unrecoverable.

## Consequences

- The advisor accumulates a compact, inspectable profile of the household;
  answers can use it without re-asking, and the profile survives thread
  deletion by design — documented in the UI so nobody is surprised.
- Two small extra LLM calls happen after each exchange (extraction, and
  summary only past 8 messages), costing idle-GPU seconds, not user latency.
- Extraction quality depends on the chat model; a bad extraction is visible
  on the memory page and individually deletable, and never enters an answer
  unnoticed because memory numbers are grounded, not trusted blindly.
- The memory store is plain rows in PostgreSQL: included in M8 encrypted
  backups, no new infrastructure, replaceable later by retrieval (the
  vector-store backlog) without changing the contract.
