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
# that reads like a failure. stderr is dropped -- the unmarked-entry note is
# asserted separately.
derived() { expand_deny_terms "$1" 2>/dev/null; }

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

# --- a marked name expands into the household forms -------------------------
printf 'name: Ada Lovelace\n' > "$WORK/deny"

expands "$WORK/deny" "Ada Lovelace"
expands "$WORK/deny" "The Lovelaces"
expands "$WORK/deny" "the Lovelaces"
expands "$WORK/deny" "Lovelace Family"
expands "$WORK/deny" "Lovelace family"

# The marker itself must never leak into the term list, or the guards would
# search the tree for the literal string "name: ...".
omits "$WORK/deny" "name: Ada Lovelace"

# A bare `<Surname>s` is a single word, the case this guard excludes as
# cry-wolf, and substring matching makes it worse: it is what turned `Tran`
# into a match inside `Transaction`. A bare `The <Surname>` matched ordinary
# prose on short surnames.
omits "$WORK/deny" "Lovelaces"
omits "$WORK/deny" "The Lovelace"

# --- sibilant surnames take `es` --------------------------------------------
printf 'name: Sarah Hollis\n' > "$WORK/sibilant"
expands "$WORK/sibilant" "The Hollises"
expands "$WORK/sibilant" "the Hollises"
omits   "$WORK/sibilant" "The Holliss"

printf 'name: Ida Finch\n' > "$WORK/ch"
expands "$WORK/ch" "The Finches"
omits   "$WORK/ch" "The Finchs"

# --- shapes a capitalisation sniff used to refuse silently -------------------
# Three parts: the surname is the LAST word, not the second.
printf 'name: Ana Maria Silva\n' > "$WORK/three"
expands "$WORK/three" "The Silvas"
expands "$WORK/three" "Silva Family"
omits   "$WORK/three" "The Marias"

# Non-ASCII letters: an [A-Z][a-z]+ regex matched none of this.
printf 'name: José García\n' > "$WORK/accent"
expands "$WORK/accent" "The Garcías"
expands "$WORK/accent" "García Family"

# Spacing after the marker is not load-bearing.
printf 'name:Ada Lovelace\n' > "$WORK/tight"
expands "$WORK/tight" "The Lovelaces"

# --- expansion is opt-in ----------------------------------------------------
# Personhood cannot be read off capitalisation. Two capitalised words are just
# as likely to be a bank or a card in this repo's list, and deriving from the
# second word turns them into terms that match ordinary code.
printf 'Chase Bank\n' > "$WORK/notaname"
expands "$WORK/notaname" "Chase Bank"
omits   "$WORK/notaname" "Banks"
omits   "$WORK/notaname" "The Banks"
omits   "$WORK/notaname" "Bank Family"

# ...but an unmarked name-shaped entry is NOT silently ignored. Silence is the
# failure mode #118 was filed for.
if expand_deny_terms "$WORK/notaname" 2>&1 >/dev/null | grep -q 'unmarked'; then
  ok "an unmarked name-shaped entry warns on stderr"
else
  ko "an unmarked name-shaped entry was silently left alone"
fi

# The note must not print the whole entry -- same four-character disclosure the
# callers' notes use.
if expand_deny_terms "$WORK/notaname" 2>&1 >/dev/null | grep -q 'Chase Bank'; then
  ko "the note disclosed the full entry"
else
  ok "the note discloses only a four-character prefix"
fi

# A lone word is neither expanded nor warned about: it has no name shape.
printf 'Lovelace\n' > "$WORK/single"
if [ "$(expand_deny_terms "$WORK/single" 2>/dev/null | wc -l | tr -d ' ')" = "1" ]; then
  ok "a single-word entry expands to itself only"
else
  ko "a single-word entry was expanded"
fi
if [ -z "$(expand_deny_terms "$WORK/single" 2>&1 >/dev/null)" ]; then
  ok "a single-word entry does not warn"
else
  ko "a single-word entry warned"
fi

# --- pass-through -----------------------------------------------------------
# Numbers and comments pass through untouched, so the numeric bounding in the
# callers still sees exactly what it expects.
printf '# a comment\n123456\n' > "$WORK/mixed"
if [ "$(expand_deny_terms "$WORK/mixed" 2>/dev/null)" = "123456" ]; then
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
