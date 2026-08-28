#!/bin/sh
# Generate `git filter-repo --replace-text` rules from the deny list
# (#117, ADR 0030).
#
# The August 2026 rewrite used a hand-written replacement set, and #118
# proved that set was incomplete: nobody enumerates every textual form of
# their own name. This derives the rules from the SAME expansion the guards
# use, so a rewrite scrubs exactly what the guards detect — the detector and
# the eraser cannot disagree.
#
# Pattern-based, like the guards: this script contains no denied value. Its
# OUTPUT does — every literal and derived form in cleartext — so treat the
# output like the deny list itself: write it to mktemp with a trap, never to
# a tracked path, never to a predictable /tmp name.
#
# Rule shapes, mirroring the matcher policy in scripts/lib/deny-terms.sh:
#   - F (literal) matchers become literal rules: replaced wherever they
#     appear, even inside identifiers, because a bare token can hide there.
#   - E (bounded) matchers become regex rules with lookarounds, so a derived
#     form is replaced only as whole words. This is not just symmetry with
#     detection: an UNBOUNDED history replacement would corrupt code, turning
#     the middle of an ordinary longer word into the placeholder.
#   - POSIX classes in the matchers are translated to Python regex (which is
#     what filter-repo speaks); lookarounds consume nothing, so surrounding
#     punctuation survives the replacement.
#
# Every value is replaced with the repo's established placeholder, so
# rewritten history reads like the cleaned tree does.
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$REPO_ROOT/scripts/lib/deny-terms.sh"

DENY=${1:-.repo-hygiene-deny}
PLACEHOLDER=demo-household

[ -f "$DENY" ] || { printf 'generate-history-replacements: no deny file at %s\n' "$DENY" >&2; exit 1; }

expand_deny_terms "$DENY" | while IFS="$DENY_TAB" read -r mode label pattern; do
  [ -z "$pattern" ] && continue
  case "$mode" in
    F)
      printf '%s==>%s\n' "$pattern" "$PLACEHOLDER"
      ;;
    E)
      py=$(printf '%s' "$pattern" \
        | sed 's/(\^|\[^\[:alnum:\]_\])/(?<![A-Za-z0-9_])/; s/(\[^\[:alnum:\]_\]|\$)/(?![A-Za-z0-9_])/; s/(\^|\[^0-9.,_\])/(?<![0-9.,_])/; s/(\[^0-9.,_\]|\$)/(?![0-9.,_])/')
      printf 'regex:%s==>%s\n' "$py" "$PLACEHOLDER"
      ;;
  esac
done
