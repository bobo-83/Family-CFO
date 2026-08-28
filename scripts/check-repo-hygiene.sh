#!/bin/sh
# Repo hygiene guard (M121, ADR 0030): fail if a personal identifier or an
# environment-specific value has crept into tracked files. This is the automated
# backstop for "nothing in the repo should identify the maintainer or their
# network" — run in CI and by the pre-commit hook.
#
# The checks are PATTERN-based, never value-based: this script must not itself
# contain the very identifiers it guards against (that would republish them). A
# maintainer who wants to also block their own literal values can list them, one
# per line, in a gitignored .repo-hygiene-deny file — the local hook honours it.
set -eu

# Optional $1: a commit-message file (wired from .githooks/commit-msg). Tracked
# files were always scanned, but a commit MESSAGE naming a live figure sailed
# straight to a public remote — found the hard way on 2026-08-05.
MSG_FILE="${1:-}"

fail=0
note() { printf 'repo-hygiene: %s\n' "$1" >&2; fail=1; }

tracked() { git ls-files -- "$@"; }

# 1) Apple Developer Team id must never be committed — the project ships with an
#    empty DEVELOPMENT_TEAM and device builds inject IOS_TEAM_ID (see
#    scripts/deploy-ios*.sh). A 10-char alphanumeric value is a real team id.
if git grep -nE 'DEVELOPMENT_TEAM = "?[A-Z0-9]{10}"?;' -- '*.pbxproj' '*.pbxproj.wired' >/dev/null 2>&1; then
  git grep -nE 'DEVELOPMENT_TEAM = "?[A-Z0-9]{10}"?;' -- '*.pbxproj' '*.pbxproj.wired' >&2
  note "committed Apple DEVELOPMENT_TEAM — blank it and set IOS_TEAM_ID at build time"
fi

# 2) No personal absolute home paths (a real login name leaks the maintainer).
if git grep -nE '/(Users|home)/[a-z][a-z0-9_-]+/' -- . \
     | grep -vE '/(Users|home)/(you|user|your-login|USERNAME|<[^>]+>)/' >/dev/null 2>&1; then
  git grep -nE '/(Users|home)/[a-z][a-z0-9_-]+/' -- . \
     | grep -vE '/(Users|home)/(you|user|your-login|USERNAME|<[^>]+>)/' >&2
  note "personal home path in a tracked file — use a placeholder or \$HOME"
fi

# 3) No non-example private IPs. Full RFC1918 dotted-quads only (so version
#    strings like 10.15.7 are not matched), bounded by non-digits. Allow the
#    documentation placeholders 192.168.1.x / 10.0.0.x / 172.16.0.x; flag any
#    other literal, which is almost always someone's actual box.
IP_RE='(^|[^0-9.])(192\.168\.[0-9]{1,3}\.[0-9]{1,3}|10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})([^0-9.]|$)'
IP_ALLOW='192\.168\.1\.[0-9]{1,3}|10\.0\.0\.[0-9]{1,3}|172\.16\.0\.[0-9]{1,3}'
if git grep -nE "$IP_RE" -- . | grep -vE "$IP_ALLOW" >/dev/null 2>&1; then
  git grep -nE "$IP_RE" -- . | grep -vE "$IP_ALLOW" >&2
  note "a specific private IP is in the repo — use a placeholder (192.168.1.x) and read the real one from env"
fi

# 4) Local-only: literal identifiers the maintainer chose to block (gitignored).
if [ -f .repo-hygiene-deny ]; then
  # #118: match the FORMS an identifier takes, not only the one someone wrote
  # down. A full name in the list never matched the "The <Surname>s" a fixture
  # actually used, which is how one survived on a public main for months.
  . "$(dirname "$0")/lib/deny-terms.sh"
  # mktemp, not a predictable /tmp path: this file holds every denied
  # identifier in cleartext -- more of them than the deny list itself, since
  # the derived forms are here too. A guessable name in a shared directory is
  # a symlink target and a squat, and the default umask leaves it readable by
  # anyone on the box. The trap matters as much: `rm -f` alone leaks the file
  # on any interrupt. Same handling as .githooks/pre-push.
  TERMS=$(mktemp)
  trap 'rm -f "$TERMS"' EXIT INT TERM
  expand_deny_terms .repo-hygiene-deny > "$TERMS"
  while IFS= read -r term; do
    [ -z "$term" ] && continue
    case "$term" in \#*) continue ;; esac
    # A purely numeric term must not match INSIDE a longer number: a five-digit
    # amount otherwise trips on a generated line-number reference that merely
    # contains it. Bound it by non-digits — a guard that cries wolf is a guard
    # people learn to ignore. (No literal example here: this file must never
    # contain the values it guards.)
    if printf '%s' "$term" | grep -qE '^[0-9][0-9.,_]*$'; then
      escaped=$(printf '%s' "$term" | sed 's/[.]/\\./g')
      pattern="(^|[^0-9.,_])${escaped}([^0-9.,_]|$)"
      if git grep -nE "$pattern" -- . >/dev/null 2>&1; then
        note "denylisted identifier present: $(printf '%s' "$term" | cut -c1-4)…"
      fi
    elif git grep -nF "$term" -- . >/dev/null 2>&1; then
      note "denylisted identifier present: $(printf '%s' "$term" | cut -c1-4)…"
    fi
    if [ -n "$MSG_FILE" ] && [ -f "$MSG_FILE" ] && grep -qF "$term" "$MSG_FILE" 2>/dev/null; then
      note "denylisted identifier in the COMMIT MESSAGE: $(printf '%s' "$term" | cut -c1-4)…"
    fi
  done < "$TERMS"
fi

if [ "$fail" -ne 0 ]; then
  printf 'repo-hygiene: FAILED — see docs/adr/0030-no-personal-identifiers.md\n' >&2
  exit 1
fi
printf 'repo-hygiene: OK\n'
