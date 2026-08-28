#!/bin/sh
# Turn the deny list into concrete MATCHERS (#118, ADR 0030): one per line,
# tab-separated —
#
#     MODE <tab> LABEL <tab> PATTERN
#
#   MODE F  PATTERN is a fixed string, matched anywhere in the text (grep -F).
#   MODE E  PATTERN is an extended regex, already escaped and bounded (grep -E).
#
# LABEL is the only thing a caller should print on a hit: a four-character
# prefix of the ORIGINAL entry plus the shape that fired — never the matched
# text itself. Guards match with deny_match / deny_tree_hit below, so the
# tree scan, the commit-msg scan and the pre-push scan cannot disagree on
# either the terms or the matching rules. They used to: each caller carried
# its own copy of the numeric bounding, and derived forms silently inherited
# the substring matching that is only safe for literals.
#
# The gap the expansion closes, found the hard way: the list held a
# maintainer's FULL name, while seven fixtures used `The <Surname>s` and
# `<Surname> Family`. Literal matching, so the guard never fired and the name
# sat on a public main for months. The first rewrite could not remove it
# either — nothing could detect it.
#
# Pattern-based, like every other guard here: this file must never contain the
# values it expands.
#
# Expansion is OPT-IN, via a `name:` prefix on the entry:
#
#     name: Firstname Surname
#     name: Firstname Middle Surname
#
# Opt-in because personhood cannot be read off capitalisation: an unmarked
# bank-name or card-name entry — the kind a finance repo actually denies —
# must match literally, not derive household forms. The surname is the LAST
# word, so three-part and accented names expand too. An unmarked entry that
# looks like a name gets a note on stderr rather than silent inaction —
# silently doing nothing is the failure mode #118 was filed for.
#
# Matching rules, and why they differ by shape:
#   - Derived forms are bounded on both sides by non-word characters. Every
#     derived form contains a space, so it cannot hide inside an identifier —
#     bounding costs NO detection, and it is what keeps a `Tran` plural from
#     matching inside `Transaction` (measured: 14 of 56 plausible
#     surnames collided with this tree as substrings; 0 of 56 bounded).
#   - Literal entries stay substring: a bare token CAN hide inside snake_case
#     or an email local-part, so bounding those would cost real detection.
#   - Numeric entries are bounded by non-number characters, as before, so an
#     amount does not trip on a longer generated number that contains it.
#   - No bare `<Surname>s` or `The <Surname>` is derived: single words and
#     short-surname articles match prose, and a guard that cries wolf is one
#     people turn off with --no-verify — which disables the real detection.

DENY_TAB=$(printf '\t')

expand_deny_terms() {
  deny_file=$1
  [ -f "$deny_file" ] || return 0

  while IFS= read -r entry; do
    [ -z "$entry" ] && continue
    case "$entry" in \#*) continue ;; esac

    case "$entry" in
      name:*)
        # Strip the marker and any spaces after it.
        term=${entry#name:}
        while :; do
          case "$term" in ' '*) term=${term# } ;; *) break ;; esac
        done
        [ -z "$term" ] && continue
        _deny_prefix=$(printf '%s' "$term" | cut -c1-4)
        printf 'F\t%s… (literal)\t%s\n' "$_deny_prefix" "$term"
        _deny_name_shapes "$_deny_prefix" "$term"
        ;;
      *)
        _deny_prefix=$(printf '%s' "$entry" | cut -c1-4)
        if printf '%s' "$entry" | grep -qE '^[0-9][0-9.,_]*$'; then
          _deny_esc=$(printf '%s' "$entry" | sed 's/[.]/\\./g')
          printf 'E\t%s… (numeric)\t(^|[^0-9.,_])%s([^0-9.,_]|$)\n' \
            "$_deny_prefix" "$_deny_esc"
        else
          printf 'F\t%s… (literal)\t%s\n' "$_deny_prefix" "$entry"
          # Possibly a name the operator forgot to mark. Say so — the
          # four-character prefix is the same disclosure the hit notes use.
          if printf '%s' "$entry" | grep -qE '^[A-Z][^ ]* [A-Z]'; then
            printf 'deny-terms: "%s…" is unmarked, so only the literal form is matched; prefix it with `name:` to derive household forms.\n' \
              "$_deny_prefix" >&2
          fi
        fi
        ;;
    esac
  done < "$deny_file"
}

# The forms a household name actually takes in copy and in fixtures.
_deny_name_shapes() { # 4-char prefix, full name
  _deny_surname=${2##* }
  # Sibilant endings take `es`, not `s`: `Hollis` -> `Hollises`, never
  # `Holliss`.
  case "$_deny_surname" in
    *s|*x|*z|*ch|*sh) _deny_plural="${_deny_surname}es" ;;
    *)                _deny_plural="${_deny_surname}s" ;;
  esac
  _deny_p=$(_deny_escape "$_deny_plural")
  _deny_s=$(_deny_escape "$_deny_surname")
  printf 'E\t%s… (household plural)\t(^|[^[:alnum:]_])[Tt]he %s([^[:alnum:]_]|$)\n' \
    "$1" "$_deny_p"
  printf 'E\t%s… (family form)\t(^|[^[:alnum:]_])%s [Ff]amily([^[:alnum:]_]|$)\n' \
    "$1" "$_deny_s"
}

_deny_escape() {
  printf '%s' "$1" | sed 's/[][\\.*^$(){}?+|]/\\&/g'
}

# Match one matcher against stdin. Callers never pick grep flags themselves.
deny_match() { # mode, pattern
  case "$1" in
    E) grep -qE -e "$2" ;;
    *) grep -qF -e "$2" ;;
  esac
}

# Match one matcher against tracked files; print the first `path:line` hit, if
# any — so a false positive costs ten seconds of looking, not the guard.
deny_tree_hit() { # mode, pattern
  case "$1" in
    E) git grep -nE -e "$2" -- . 2>/dev/null | head -n 1 | cut -d: -f1,2 ;;
    *) git grep -nF -e "$2" -- . 2>/dev/null | head -n 1 | cut -d: -f1,2 ;;
  esac
}
