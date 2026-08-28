#!/bin/sh
# Tests for scripts/generate-history-replacements.sh (#117, ADR 0030).
#
# Behavioural: rules are generated from an invented deny list and APPLIED to
# sample texts, because the risk in a history rewrite is not a malformed rule
# but a rule that eats an ordinary word out of the middle of real code.
# Invented names only; caught-samples never use a real surname shape.
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
GEN="$REPO_ROOT/scripts/generate-history-replacements.sh"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0
ok() { printf '  PASS  %s\n' "$1"; pass=$((pass + 1)); }
ko() { printf '  FAIL  %s\n' "$1"; fail=$((fail + 1)); }

# Build a perl program from the generated rules, the same way a dry run does.
rules_to_perl() { # rules-file -> perl-file on stdout
  while IFS= read -r line; do
    lhs=${line%%==>*}; rhs=${line#*==>}
    case "$lhs" in
      regex:*) printf 's/%s/%s/g;\n' "${lhs#regex:}" "$rhs" ;;
      *)       printf 's/\\Q%s\\E/%s/g;\n' "$lhs" "$rhs" ;;
    esac
  done < "$1"
}

printf 'name: Ada Lovelace\n' > "$WORK/deny"
"$GEN" "$WORK/deny" 2>/dev/null > "$WORK/rules"
rules_to_perl "$WORK/rules" > "$WORK/scrub.pl"

apply() { printf '%s' "$1" | perl -p "$WORK/scrub.pl"; }

check() { # input, expected, description
  got=$(apply "$1")
  if [ "$got" = "$2" ]; then ok "$3"; else ko "$3 (got: $got)"; fi
}

# --- every household form is erased, in place ------------------------------
check 'households: ["The Lovelaces"]' 'households: ["demo-household"]' \
  'replaces The <Surname>s inside a fixture'
check 'dinner with the Lovelaces tonight' 'dinner with demo-household tonight' \
  'replaces the lowercase article form in prose'
check 'owner: Lovelace Family trust' 'owner: demo-household trust' \
  'replaces the family form'
check 'author: Ada Lovelace <a@b>' 'author: demo-household <a@b>' \
  'replaces the literal full name'

# --- and ordinary code survives --------------------------------------------
# The corruption case: an unbounded rule would rewrite the middle of a longer
# word, silently breaking historical code. Bounded rules must not.
check 'logs the Transcript id' 'logs the Transcript id' \
  'leaves a longer word containing a derived form alone'
check 'theLovelaces_var = 1' 'theLovelaces_var = 1' \
  'leaves an identifier containing a derived form alone'

# --- structure --------------------------------------------------------------
if grep -q '^name:' "$WORK/rules"; then
  ko "the name: marker leaked into the rules"
else
  ok "the name: marker never leaks into the rules"
fi
if grep -q '==>demo-household$' "$WORK/rules"; then
  ok "every rule replaces with the established placeholder"
else
  ko "a rule uses a different replacement"
fi

# An unmarked entry generates only its literal rule.
printf 'Vandelay Bancorp\n' > "$WORK/org"
n=$("$GEN" "$WORK/org" 2>/dev/null | wc -l | tr -d ' ')
if [ "$n" = "1" ]; then
  ok "an unmarked entry yields exactly one literal rule"
else
  ko "an unmarked entry yielded $n rules"
fi

# A missing deny file is an error here, unlike in the guards: generating an
# EMPTY rewrite plan and force-pushing it would be worse than failing.
if "$GEN" "$WORK/does-not-exist" >/dev/null 2>&1; then
  ko "a missing deny file silently produced an empty plan"
else
  ok "a missing deny file is a hard error"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
