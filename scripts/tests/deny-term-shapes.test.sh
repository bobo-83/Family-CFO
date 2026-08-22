#!/bin/sh
# Tests for the deny-list shape expansion (#118, ADR 0030).
#
# The gap: the list held a maintainer's FULL name while fixtures used
# "The <Surname>s" and "<Surname> Family". Literal matching, so the guard never
# fired -- and the first history rewrite could not remove what nothing could
# detect. Wiring this expansion in immediately surfaced an eighth occurrence on
# main that the previous scan had missed.
#
# Invented names only. This file must never contain a real denied value.
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$REPO_ROOT/scripts/lib/deny-terms.sh"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0
ok() { printf '  PASS  %s\n' "$1"; pass=$((pass + 1)); }
ko() { printf '  FAIL  %s\n' "$1"; fail=$((fail + 1)); }

# Capture once rather than piping into `grep -q`: -q exits on the first match
# and closes the pipe, which makes the producer print a broken-pipe warning
# that reads like a failure.
derived() { expand_deny_terms "$1"; }

expands() { # term-file, expected-line
  if printf '%s\n' "$(derived "$1")" | grep -xF "$2" >/dev/null; then
    ok "derives '$2'"
  else
    ko "did NOT derive '$2'"
  fi
}
omits() {
  if printf '%s\n' "$(derived "$1")" | grep -xF "$2" >/dev/null; then
    ko "wrongly derived '$2'"
  else
    ok "leaves '$2' alone"
  fi
}

printf 'Ada Lovelace\n' > "$WORK/deny"

expands "$WORK/deny" "Ada Lovelace"
expands "$WORK/deny" "The Lovelaces"
expands "$WORK/deny" "the Lovelaces"
expands "$WORK/deny" "The Lovelace"
expands "$WORK/deny" "Lovelace Family"
expands "$WORK/deny" "Lovelace family"
expands "$WORK/deny" "Lovelaces"

# A single word is NOT expanded: a bare surname matches ordinary prose, and a
# guard that cries wolf is one people learn to ignore.
printf 'Lovelace\n' > "$WORK/single"
if [ "$(expand_deny_terms "$WORK/single" | wc -l | tr -d ' ')" = "1" ]; then
  ok "a single-word entry expands to itself only"
else
  ko "a single-word entry was expanded"
fi

# Numbers and comments pass through untouched, so the numeric bounding in the
# callers still sees exactly what it expects.
printf '# a comment\n123456\n' > "$WORK/mixed"
if [ "$(expand_deny_terms "$WORK/mixed")" = "123456" ]; then
  ok "comments are dropped and numbers pass through unchanged"
else
  ko "comment/number handling changed"
fi

omits "$WORK/mixed" "The 123456s"

# A missing file is not an error -- the guards run on machines without one.
if expand_deny_terms "$WORK/does-not-exist" >/dev/null 2>&1; then
  ok "a missing deny file is silently empty"
else
  ko "a missing deny file errored"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
