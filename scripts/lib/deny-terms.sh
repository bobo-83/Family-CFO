#!/bin/sh
# Expand the deny list into every textual form an identifier actually takes
# (#118, ADR 0030).
#
# The gap this closes, found the hard way: the list held a maintainer's FULL
# name, while seven fixtures used `The <Surname>s` and `<Surname> Family`.
# Literal matching, so the guard never fired and the name sat on a public main
# for months. The first rewrite could not remove it either — nothing could
# detect it. Predicting every form of your own name before it appears is not
# something anyone reliably does, so derive them instead.
#
# Pattern-based, like every other guard here: this file must never contain the
# values it expands.
#
# Only "Firstname Surname" entries expand. A single word is left exactly as
# written, because a bare surname matches ordinary prose (and, in this repo,
# Vietnamese text and a binary) — a guard that cries wolf is one people learn
# to ignore, which is the reasoning that also bounds the numeric terms.

# Print every term to match for, one per line: the originals, plus derived
# shapes for two-word capitalised names.
expand_deny_terms() {
  deny_file=$1
  [ -f "$deny_file" ] || return 0

  while IFS= read -r term; do
    [ -z "$term" ] && continue
    case "$term" in \#*) continue ;; esac

    printf '%s\n' "$term"

    # Two capitalised words, nothing else: treat as Firstname Surname.
    if printf '%s' "$term" | grep -qE '^[A-Z][a-zA-Z'"'"'-]+ [A-Z][a-zA-Z'"'"'-]+$'; then
      surname=${term#* }
      # The forms a household name actually takes in copy and in fixtures.
      printf 'The %ss\n' "$surname"
      printf 'the %ss\n' "$surname"
      printf 'The %s\n' "$surname"
      printf '%s Family\n' "$surname"
      printf '%s family\n' "$surname"
      printf '%ss\n' "$surname"
    fi
  done < "$deny_file"
}
