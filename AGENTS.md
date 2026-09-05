# Agent Instructions

This repository is optimized for AI coding agents and human maintainers.

## Operating Model

- Follow the Spec Kit order in `docs/specs/README.md`.
- Do not implement product behavior before the relevant spec exists.
- Prefer small, reviewable changes tied to a milestone.
- Treat documentation as code.
- Update tests and docs with implementation changes.

## Architecture Rules

- Financial calculations must be deterministic and auditable.
- LLMs may explain, summarize, and recommend, but must not be the sole calculator.
- Do not add cloud service dependencies for sensitive financial data.
- Keep AI runtimes, vector stores, OCR engines, authentication, and financial modules replaceable.
- OpenAPI is the source of truth for backend clients.

## Platform Constraints

- Do not implement, scaffold, generate, or modify Swift/iOS app code from a Linux environment.
- On Linux, iOS work is limited to specifications, documentation, API contracts, CI planning, and review.
- Swift/iOS implementation requires a macOS environment with the Swift toolchain and Xcode available.

## Expected Change Shape

Each feature should include:

- Implementation
- Unit tests
- Integration tests where component boundaries are touched
- **Advisor tool access** — a feature that adds a data domain the family can
  see must also add or extend a read-only grounded tool in the M16 registry
  (`apps/api/src/family_cfo_api/ai_tools.py`, ADR 0009), reusing the same
  service code as the HTTP endpoint, so the chat advisor can answer questions
  about it. If chat access is genuinely out of scope, say so as an explicit
  non-goal in the spec gate.
- Documentation updates
- A clear commit message

## Tests Run Everywhere

Every test must be able to run — and pass — on a contributor's machine and in
CI. Operator directive (2026-09-04):

- **No test may assert a tool's absence.** A test of a fallback path selects
  the fallback explicitly (patch the seam); it must not assert whichever
  engine the host happens to have. Reference case: the OCR stub test asserted
  the "no tesseract" warning, so it passed on CI runners and failed inside the
  very Docker image that ships.
- **Skips must be loud where the tool is guaranteed.** An environment-dependent
  test may skip on a machine missing its tool, but CI installs the tool and
  sets the matching `FAMILY_CFO_REQUIRE_*` variable so the same test FAILS
  rather than silently skips if the tool ever disappears from a runner.
- **Declare every tool.** A test that needs a system binary or runtime must
  have it (a) installed in the CI workflow, (b) listed below, and (c) called
  out to the operator when introduced — never assumed onto a machine.

### System tools for the full test matrix

- Python ≥ 3.12 — `uv venv --python 3.12 --seed apps/api/.venv`, then
  `make install` in `apps/api` (installs the API plus every sibling service
  editable, so workspace changes are what run).
- Node 22 + npm — web unit tests, generated-client checks, Playwright e2e.
- tesseract (`brew install tesseract` / `apt-get install tesseract-ocr`) —
  real-OCR tests; also present in the production API image.
- Xcode with the iOS and watchOS platforms — Apple targets.

## One Input, Not Two — Minimize Duplicate User Entry

**When the user tells the system something, don't make them tell it again for a
related record the system could infer.** A person's time and patience are the
scarcest resource in a self-hosted app nobody is paid to keep using; every
redundant tap is a reason to stop.

Concretely: when an action establishes a fact that another record can reuse,
propagate it — apply or suggest, don't re-prompt. The reference case (M96):
filing a *bill* under a category also files that bill's still-uncategorized
*matching transactions* under the same category, so the user categorizes the
merchant once, not once per place it appears.

Rules for propagation:

- **Never overwrite an explicit user choice.** Propagate only to records the user
  hasn't already decided (e.g. uncategorized transactions), so a bulk action can't
  silently undo a deliberate one.
- **Say what you did.** Propagation must be visible — return and surface the count
  ("also filed 5 matching transactions"), never a silent mass mutation.
- **Match conservatively.** Use the same normalized key the system already trusts
  (e.g. `normalize_merchant`); a false match that mis-files data is worse than
  leaving a record for the user to handle.
- **Prefer auto-apply for the safe cases, suggestion for the ambiguous ones.**
  Reducing input is the goal, but not at the cost of wrong data the user must now
  hunt down and fix.

Before adding a second place where the user enters something the system already
knows, ask whether the first entry can flow to it instead.

## Credentials — Never Ask, Never Handle

**Never ask the user for a password, passphrase, private key, API token, or any
other secret — and never accept one if it is offered.** This binds AI agents and
any tool they drive. It is not a style preference; a secret pasted into a chat,
a prompt, a config file, or a shell command has been disclosed: it lands in
transcripts, scrollback, shell history, process listings and logs, and cannot be
un-disclosed. The correct response to "I need to authenticate" is never "what is
your password".

Instead, build the path where the secret never reaches you:

- **Delegate the authentication to a tool the user drives themselves**, so the
  secret goes straight from the user to that tool. `ssh-copy-id` typed in the
  user's own terminal; `gh auth login`; `docker login`. Point at the command and
  let them run it.
- **Use the credential store the platform already has**, rather than a value you
  hold: `~/.ssh/config` + `ssh-agent`, the macOS Keychain, an existing CLI's
  logged-in session. Read *through* it; never read *out of* it.
- **Never force past it.** `scripts/deploy.sh` and `scripts/patch.sh` leave
  `SSH_USER`/`SSH_PORT`/`SSH_KEY` unset by default precisely so `ssh` resolves
  them from `~/.ssh/config` — an earlier version prompted for them and built
  `user@host` itself, which overrode the very mechanism that made a password
  unnecessary. If your code needs a secret to work, the design is wrong; fix the
  design.
- **Config files hold destinations, not secrets.** `.deploy.env` names a host;
  it must never name a password. If a file would need a secret to be useful,
  that secret belongs in the platform's store instead.
- **A secret in the repo is an incident, not a mistake.** If one is disclosed
  anyway — pasted in chat, committed, printed to a log — say so plainly and tell
  the user to rotate it. Do not quietly use it.

The user should be able to hand an agent this repository and never be asked to
type a secret to it. If a task genuinely cannot proceed without one, stop and
explain what the user must run themselves.

## Sensitive Data

Never commit:

- Bank statements
- Receipts with personal details
- Credentials
- Tax documents
- Brokerage or payroll exports
- Production database dumps

Use synthetic fixtures only.
