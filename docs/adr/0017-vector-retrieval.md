# ADR 0017: Vector Retrieval

## Status

Accepted (M69).

## Context

The advisor's knowledge is injected wholesale: all household memories join
every prompt, and transactions are reachable only through aggregate tools
(sums, top merchants). That leaves two gaps: free-text recall over history
("when did we last pay the plumber, and how much?") is impossible, and
wholesale injection stops scaling once memories/documents grow past what a
prompt comfortably holds. The Docker spec has carried an unused Qdrant
scaffold since M12; ADR 0007 lists the vector store as a replaceable seam.

Constraints: local-only (ADR 0008 — no cloud embedding APIs), grounded
numbers (ADR 0003/0009), no GPU contention with the serving models, and
every component replaceable (ADR 0007).

## Decision

1. **Local CPU embeddings behind a seam.** An `EmbeddingAdapter` protocol
   with a fastembed implementation (BAAI/bge-small-en-v1.5, ~130 MB ONNX,
   CPU-only, lazily loaded, cache in a named volume). Embedding text never
   leaves the box and never touches the GPU the chat model needs.

2. **Qdrant behind a seam, on by default.** A `VectorStoreAdapter` protocol
   with a Qdrant implementation speaking plain REST via httpx (no client
   dependency). Qdrant loses its opt-in profile now that it has a consumer;
   `FAMILY_CFO_QDRANT_URL` empty disables the whole feature gracefully.

3. **What gets indexed.** Household memories and transactions (the trailing
   ~13 months), one point per row keyed by the row's uuid with the household
   id in the payload (every search filters on it). The worker upserts at
   startup (fast, additive) and does a wipe-and-rebuild daily (self-healing;
   prunes vectors of deleted rows). Indexing failures log and skip — never
   block the worker.

4. **Retrieval is a grounded tool, not prompt stuffing.** Chat gains
   `search_records(query)` (registered only when a vector store is
   configured): the query is embedded, both collections are searched
   household-filtered, and the top matches return with date, description,
   and amount display — so every recalled figure is grounded through the
   normal tool trace (ADR 0009). The model decides when to search; memory
   injection (M57) stays as-is until its size warrants retrieval too.

## Consequences

- "When did we last pay X" questions become answerable from real history,
  with amounts grounded; the same seam later serves documents and, when the
  memory store grows large, selective memory injection.
- Two new moving parts (Qdrant, an ONNX runtime in the api/worker image),
  both replaceable behind protocols and both optional at runtime — an empty
  `FAMILY_CFO_QDRANT_URL` returns the stack to its pre-M69 behavior.
- Recall is best-effort: an unindexed or ambiguous item may not surface
  (`lookup_failed`/empty results are honest tool outcomes); deterministic
  aggregates remain the authority for totals.
- Qdrant data joins the backup story the M8 spec deferred "until vector data
  exists" — it now exists and is REBUILDABLE from PostgreSQL, so backups
  deliberately exclude it (a restore re-indexes).
