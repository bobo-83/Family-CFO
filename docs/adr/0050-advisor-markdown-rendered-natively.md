# ADR 0050: Advisor answers stay markdown, rendered natively on each client

## Status

Accepted.

## Context

The advisor emits GitHub-flavored markdown — headings, **tables**, lists — but
neither client rendered it:

- **iOS** used `AttributedString(markdown:, .inlineOnlyPreservingWhitespace)`,
  which is *inline-only*: bold/italic worked, but `####` headings and `|`-tables
  fell through as raw text (a plan's savings table rendered as pipe soup).
- **Web** interpolated `{{ turn.content }}` as **plain text** — the raw markdown
  source showed verbatim, tables and all.

The question was whether to change the wire format (serve HTML, or structured
JSON) or fix the rendering.

## Decision

**Keep markdown as the advisor's wire format; render it natively on each client
with a real GitHub-flavored-markdown renderer.**

- **iOS**: adopt `swift-markdown-ui` (MarkdownUI) — a block-level GFM renderer
  with tables, headings, and lists. Assistant rows render through `Markdown(...)`
  with a compact `chatBubble` theme (system body text, modestly-scaled headings,
  transparent so it sits on the bubble fill and adapts to light/dark). User rows
  stay plain text.
- **Web**: a pure `MarkdownPipe` runs `marked` (GFM) to HTML, bound via
  `[innerHTML]`, which Angular sanitizes — scripts/handlers are stripped, tables
  and formatting kept. Applied only to assistant text, never raw user input.
  Tables scroll horizontally inside the bubble rather than widening the page.

## Invariant

> The advisor speaks GitHub-flavored markdown. Each client renders it natively
> (MarkdownUI on iOS, `marked` + sanitized `[innerHTML]` on web) so headings,
> tables, and lists display as formatted content — never as raw markdown source.
> User-authored text is never rendered as HTML.

## Rejected

- **Serve HTML from the server**: renders *worse* on iOS specifically — SwiftUI
  has no good native HTML view (`NSAttributedString` HTML is slow, main-thread,
  and hard to style; `WKWebView` is far too heavy for chat bubbles). On web it
  adds an XSS surface for no gain over sanitized `[innerHTML]`. Markdown is also
  what's stored in history and what the model produces most reliably.
- **Structured JSON (sections/tables as data), rendered natively**: maximal
  control but a large, fragile change — the tool-calling loop returns free-form
  text, and constraining a conversational advisor to a rigid schema is brittle.
- **Hand-rolled iOS markdown renderer**: avoids a dependency but re-implements
  table/list parsing we'd have to maintain and test; MarkdownUI is battle-tested.
- **Keep inline-only on iOS**: the actual defect — it can't render the tables and
  headings the advisor already emits.
