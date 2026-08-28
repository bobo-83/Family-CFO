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
# Expansion is OPT-IN, via a `name:` prefix on the entry:
#
#     name: Firstname Surname
#     name: Firstname Middle Surname
#
# It is opt-in because personhood cannot be read off capitalisation. An earlier
# revision expanded any two capitalised words, which turned a `Chase Bank` or
# `Apple Card` entry — both plausible in a finance repo's list — into the bare
# terms `Banks` and `Cards`, each matching eleven tracked files and blocking
# every commit until someone guessed why. A marker also lets accented and
# three-part names expand, which a shape-sniffing regex silently refused to do.
#
# Unmarked entries are matched literally, exactly as written. An unmarked entry
# that looks like a name gets a note on stderr rather than silent inaction —
# silently doing nothing is the failure mode #118 was filed for.

# Print every term to match for, one per line: the originals, plus derived
# shapes for entries marked `name:`.
expand_deny_terms() {
  deny_file=$1
  [ -f "$deny_file" ] || return 0

  while IFS= read -r term; do
    [ -z "$term" ] && continue
    case "$term" in \#*) continue ;; esac

    case "$term" in
      name:*)
        # Strip the marker and any spaces after it.
        term=${term#name:}
        while :; do
          case "$term" in ' '*) term=${term# } ;; *) break ;; esac
        done
        [ -z "$term" ] && continue
        printf '%s\n' "$term"
        expand_name_shapes "$term"
        ;;
      *)
        printf '%s\n' "$term"
        # Two or more capitalised words and not marked: possibly a name the
        # operator forgot to mark. Say so — the four-character prefix is the
        # same disclosure the callers' notes use.
        if printf '%s' "$term" | grep -qE '^[A-Z][^ ]* [A-Z]'; then
          printf 'deny-terms: "%s…" is unmarked, so only the literal form is matched; prefix it with `name:` to derive household forms.\n' \
            "$(printf '%s' "$term" | cut -c1-4)" >&2
        fi
        ;;
    esac
  done < "$deny_file"
}

# The forms a household name actually takes in copy and in fixtures.
#
# Deliberately NOT derived:
#   - a bare `<Surname>s`. It is a single word, the case this guard excludes as
#     cry-wolf, and substring matching makes it worse: `Tran` yields `Trans`
#     inside `Transaction`, `Wood` yields `Woods` inside `Woodstock`. Across 56
#     plausible surnames it was the single largest source of false positives,
#     and short Vietnamese surnames — the shape this repo's list is likeliest
#     to hold — fared worst.
#   - a bare `The <Surname>`. Nobody writes a household that way; it existed
#     only to catch `The <Surname>es` by substring, which the sibilant branch
#     below now derives properly. On short surnames it matched ordinary prose
#     (`The Le`, `The Do`).
expand_name_shapes() {
  _surname=${1##* }
  # Sibilant endings take `es`, not `s`: `Jones` -> `Joneses`, never `Joness`.
  case "$_surname" in
    *s|*x|*z|*ch|*sh) _plural="${_surname}es" ;;
    *)                _plural="${_surname}s" ;;
  esac

  printf 'The %s\n' "$_plural"
  printf 'the %s\n' "$_plural"
  printf '%s Family\n' "$_surname"
  printf '%s family\n' "$_surname"
}
