# ADR 0028: Every statement input accepts paste (M114)

## Status

Accepted. An instance of the M96 "uniform experience" rule, applied to
statement/document capture, on both clients (ADR 0025).

## Context

The M100 check-photo attachment gained "Paste from clipboard", but the loan
statement scan, the iOS W-2 scan, and the dashboard's W-2 scan did not — each
capture input had its own ad-hoc list of sources. The user, unable to paste a
copied loan statement into "Add a loan": "Whenever there is an input for
statement make sure we can paste."

## Decision

**Any input that accepts a statement/document image offers the same sources —
camera and/or picker AND the clipboard — through shared machinery, not
per-screen re-implementations.**

- iOS: one shared reader, `ClipboardImage.read` (handles a copied `UIImage`, an
  item provider, typed image data, and PDF data). Used by the transaction
  check-photo input, the loan statement scan (image or PDF → the same scan
  path as camera/file), and the W-2 scan (which also gained a PDF scan path,
  closing an incidental gap — payroll PDFs previously worked only on the web).
- Web: Ctrl/⌘+V on a page with a statement input feeds the copied image/PDF
  into the same scan path as the file picker (`window:paste` listener, write
  roles only), and the hint text says so.
- A paste that isn't a usable image/PDF produces a clear message, never a
  silent no-op.

## Invariant

> A statement that can be photographed or uploaded can also be pasted, through
> the shared clipboard reader, feeding the identical scan/attach path. A new
> capture input ships with all sources or names the exception in its ADR.

## Rejected

- **Per-screen paste implementations** — that's how the gap happened; the
  transaction screen had paste for months while loans didn't.
- **Paste buttons on the web via `navigator.clipboard.read()`** — permission
  prompts vary by browser; the OS-native paste gesture needs no permission and
  is what users try first.
- ~~**Scoping paste to the chat attachment input too**~~ — added in M118 on
  both clients (iOS composer menu + Ctrl/⌘+V on the dashboard chat page); the
  exception no longer exists.
