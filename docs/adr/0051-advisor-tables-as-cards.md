# ADR 0051: Advisor tables render as cards, not a wide grid

## Status

Accepted. Extends ADR 0050 (advisor answers are markdown, rendered natively).

## Context

ADR 0050 made both clients render the advisor's GitHub-flavored markdown. Tables
then rendered as real `<table>`/grid — but a typical plan table has **five
columns** (Category, Current, Trim Suggestion, Monthly Savings, Tied to Goal),
and that does not fit a phone-width chat bubble. Every cell wrapped mid-word
("Shoppin g", "Trim Suggestio n"), on **both** iOS and the web app viewed in a
phone browser. Horizontal scroll was considered but hides columns and still reads
like a spreadsheet crammed into a phone.

## Decision

**Render each table row as a stacked, labeled card** — the first column is the
card title, each remaining column a "Header: value" field. No wide grid, no
sideways scroll, no mid-word wrapping; it reads cleanly at any width.

- **iOS**: `MarkdownMessageView` splits the answer into prose and table segments.
  Prose renders through MarkdownUI; each table becomes native SwiftUI cards
  (`TableCards`). Inline markdown inside cells (e.g. **$488.81**) is preserved.
- **Web**: `MarkdownPipe` rewrites each GFM table into card HTML before markdown
  rendering (cell content via `marked.parseInline`); cards are styled with
  `.md-card` / `.md-card__field`.

Both parse the same GFM table shape (a header row, a `|---|` separator, then body
rows), so a stray `|` in prose is never mistaken for a table.

## Invariant

> A GitHub-flavored table in an advisor answer renders as one card per row (first
> column the title, the rest labeled fields) on every client — never as a wide
> multi-column grid that overflows or wraps a phone-width bubble.

## Rejected

- **Horizontal scroll**: keeps the grid but hides columns off-screen behind a
  swipe and still feels like a spreadsheet — not what a polished finance app does.
- **Wrap cells (the default)**: what we had — mid-word wrapping, unreadable.
- **Change the advisor to emit lists instead of tables**: loses the tabular
  intent and the side-by-side comparison; better to keep the model's markdown and
  present it well per platform (same spirit as ADR 0050).
- **Keep real tables on wide web, cards only on mobile**: the dashboard is
  routinely opened in a phone browser, so one consistent card layout is simpler
  and always readable.
