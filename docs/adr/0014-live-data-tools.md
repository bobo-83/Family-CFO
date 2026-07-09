# ADR 0014: Live-data chat tools — exchange rates and web search (M24)

## Status

Accepted.

## Context

The advisor should answer questions needing live external facts — "how much is
$1,000 in VND?", "what does an iPhone 16 cost?" — which the local model must
never guess (ADR 0003/0009). The privacy stance (ADR 0008) forbids household
data leaving the box; it does not forbid fetching *public* data, provided the
outbound request carries nothing about the household.

## Decisions

### 1. Live data enters chat as M16 tools, never as model knowledge

Two new read-only tools join the agentic registry:

- **`get_exchange_rate(base, quote)`** — fetches the current rate from
  `open.er-api.com` (keyless, ~160 currencies incl. VND, ECB-style daily
  rates). The outbound request contains exactly two ISO currency codes.
- **`web_search(query)`** — top result titles/snippets/URLs from a
  **self-hosted SearXNG** metasearch instance (compose profile `search`, off by
  default). Registered only when `FAMILY_CFO_SEARXNG_URL` is configured, so the
  default deployment makes no third-party search calls. The model is prompted
  to search for *item/price facts*, not household information.

Numbers in tool results are grounded automatically by the M16 guardrail (the
tool trace feeds `grounded_values`), so a fetched rate or price can be quoted
without tripping the fabricated-number check — and anything the model invents
beyond the trace still fails closed.

### 2. What leaves the box, exactly

Currency codes (rate tool) and the model-composed search query (search tool —
and then only to the operator's own SearXNG). The chat prompt already forbids
the model from inserting household identity into tool arguments; tool argument
validation enforces shape (ISO codes; bounded query length). This mirrors the
M23 precedent (HF search query).

### 3. Failures degrade, never fabricate

Provider errors return structured `{"error": "lookup_failed", ...}` payloads —
the loop's existing correct-or-ask behaviour — and the deterministic fallback
path is unchanged. A disabled tool is simply absent from the registry, so the
model cannot call it.

## Consequences

- `FAMILY_CFO_LIVE_DATA_ENABLED` (default on; rates only) and
  `FAMILY_CFO_SEARXNG_URL` (default empty) knobs; a `searxng` compose profile.
- Rates come from one free provider; if it disappears, the tool degrades to
  `lookup_failed` until the adapter is pointed elsewhere (single function).
- Web-search snippet quality bounds price-answer quality; a shopping-API
  integration (keyed) remains future work.
