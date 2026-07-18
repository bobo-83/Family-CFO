# ADR 0030: No personal identifiers or environment specifics in the repo (M121)

## Status

Accepted.

## Context

Preparing to open-source the repo surfaced maintainer-specific values baked into
tracked files: the home box's private IP (`192.168.68.x`, in ~11 files including
source), the Apple Developer **Team id** (in the Xcode project and a deploy
script), and a Synology-address UI placeholder on the same subnet. None is an
exploitable secret — the IP is non-routable RFC1918 — but each ties the public
repo to one person and one network, and once published they persist in git
history forever. The user asked for a rule so this can't recur.

A later pass found a category the first sweep missed: the maintainer's **real
name** used as sample data in test fixtures, an env example, deploy-script
comments, and web/iOS specs (a real first name in device labels,
`owner_display_name`, and email fixtures). A name has no detectable pattern, so the pattern-based guard
cannot catch it — this is precisely what the gitignored literal deny-list is
for, and the lesson is that sample/test data must use a neutral placeholder
persona, never a real person.

## Decision

**The repository contains no personal identifiers and no environment-specific
values.** Anything that differs per person or per deployment is supplied at
build/run time (env var or a gitignored file), and the tree carries only neutral
placeholders. Concretely:

1. **Apple Developer Team id is never committed.** The Xcode project ships with
   `DEVELOPMENT_TEAM = ""`; simulator builds (and CI) need none, and a device or
   OTA build injects `DEVELOPMENT_TEAM=$IOS_TEAM_ID` via xcodebuild (set
   `IOS_TEAM_ID` in the gitignored `.deploy.env`).
2. **No real private IPs or hostnames.** Source reads the box address from config
   / `.deploy.env`; docs and examples use the reserved placeholders
   `192.168.1.x` (LAN), `10.0.0.x`, `172.16.0.x`.
3. **No personal home paths** (`/Users/<name>`, `/home/<name>`) — use `$HOME`,
   `~`, or a placeholder like `your-login`.
4. **No real personal names** — sample data, test fixtures, device labels, and
   examples use a neutral placeholder persona (`Alex`, `alex@example.com`,
   login `alex`), never the maintainer's real name.
5. **No secrets, ever** — enforced separately by gitleaks; production requires
   them via env (compose fails without `POSTGRES_PASSWORD`), never a default.

## Enforcement

- `scripts/check-repo-hygiene.sh` — **pattern-based** (it must not itself contain
  the values it guards, or it would republish them): flags a non-empty
  `DEVELOPMENT_TEAM`, any non-placeholder RFC1918 IP, and personal home paths.
  Runs in the **Security** CI workflow and as a **pre-commit** hook.
- A maintainer may additionally list their own literal values — real name, email,
  and any other unpatternable personal identifiers — one per line, in a
  **gitignored** `.repo-hygiene-deny`; the local hook fails if any appears in a
  tracked file. It is never committed, so it can't leak the values it protects.
  This is the only backstop for names, which the pattern checks cannot detect.
- gitleaks (already in CI) covers keys/tokens.

## Invariant

> A fresh clone reveals nothing about who maintains it or where it runs. Every
> per-person / per-deployment value is env-supplied or a documented placeholder;
> the repo-hygiene guard fails CI and the commit if a real one appears.

## Rejected

- **A value-based denylist in the committed guard** — it would embed the very IP,
  team id, or email it's meant to remove. Patterns in the repo, literals only in
  the gitignored local file.
- **Relying on review alone** — this exact class slipped through for months; an
  automated gate is the point.
- **Leaving the private IP (it's non-routable)** — true, but it's still the
  maintainer's network, it's cheap to placeholder, and the rule is simpler as
  "no environment specifics" than "no *routable* ones".

## Note on history

This ADR governs the tree going forward. Values already in past commits are
removed by a one-time `git filter-repo --replace-text` pass before publishing
(the repo is rewritten and pushed to a clean remote while still private).
