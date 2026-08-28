#!/bin/sh
# Tests for the deny-list shape expansion and matching policy (#118, ADR 0030).
#
# Behavioural, not structural: every assertion feeds a SAMPLE TEXT through the
# same expand -> deny_match path the guards use, and asks "would this text be
# flagged?". Asserting on emitted pattern strings would pass even if the
# matching semantics silently changed.
#
# Invented names only, with one rule for sample texts: a text asserted as
# CAUGHT must use an invented surname, because this file is itself scanned by
# the guard — a caught-text with a real surname shape would be a false
# positive planted in our own tree. Real short surnames (the class that made
# a `Tran` plural match inside `Transaction`) appear only inside innocuous prose
# asserted as IGNORED.
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$REPO_ROOT/scripts/lib/deny-terms.sh"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0
ok() { printf '  PASS  %s\n' "$1"; pass=$((pass + 1)); }
ko() { printf '  FAIL  %s\n' "$1"; fail=$((fail + 1)); }

# Would this text be flagged by a guard using this deny file?
flags() { # deny-file, sample-text
  expand_deny_terms "$1" 2>/dev/null | {
    while IFS="$DENY_TAB" read -r mode label pattern; do
      [ -z "$pattern" ] && continue
      if printf '%s\n' "$2" | deny_match "$mode" "$pattern"; then
        exit 0
      fi
    done
    exit 1
  }
}
caught()  { if flags "$1" "$2"; then ok "flags: $3"; else ko "MISSED: $3"; fi; }
ignored() { if flags "$1" "$2"; then ko "FALSE POSITIVE: $3"; else ok "ignores: $3"; fi; }

# --- a marked name is caught in every household form -------------------------
printf 'name: Ada Lovelace\n' > "$WORK/deny"

caught  "$WORK/deny" 'author: Ada Lovelace'                'the literal full name'
caught  "$WORK/deny" 'households: ["The Lovelaces"]'       'The <Surname>s in a fixture'
caught  "$WORK/deny" 'dinner with the Lovelaces tonight'   'the <Surname>s in prose'
caught  "$WORK/deny" 'owner: Lovelace Family'              '<Surname> Family'
caught  "$WORK/deny" 'the Lovelace family budget'          '<Surname> family in prose'
caught  "$WORK/deny" "Ada Lovelace's ledger"               'possessive, via the substring literal'

ignored "$WORK/deny" 'Lovelaces on their own'              'bare plural without an article'
ignored "$WORK/deny" 'The Lovelace'                        'article + singular (matched prose on short surnames)'

# --- boundaries: the false positive that motivated all of this ---------------
# A derived form contains a space, so it can never hide inside an identifier;
# bounding it costs no detection. Without bounds, `name: Minh Tran` derived
# an article-plural for `Tran`, which matched inside `Transaction` repo-wide.
printf 'name: Minh Tran\n' > "$WORK/tran"
ignored "$WORK/tran" 'logs the Transaction id'             'a derived form inside a longer word'
ignored "$WORK/tran" 'renders the Translations table'      'another longer word'

# ...while punctuation and quoting still count as word edges (invented name):
printf 'name: Kai Zyzzo\n' > "$WORK/zyzzo"
caught  "$WORK/zyzzo" 'guests=["the Zyzzos"]'              'derived form bounded by quotes'
caught  "$WORK/zyzzo" 'Say hi to the Zyzzos.'              'derived form bounded by punctuation'
ignored "$WORK/zyzzo" 'the Zyzzosphere blog'               'derived form inside a coinage'

# --- sibilant surnames take `es` ---------------------------------------------
printf 'name: Sarah Hollis\n' > "$WORK/sibilant"
caught  "$WORK/sibilant" 'The Hollises next door'          'sibilant plural with es'
ignored "$WORK/sibilant" 'The Holliss'                     'the wrong bare +s plural'

printf 'name: Ida Finch\n' > "$WORK/ch"
caught  "$WORK/ch" 'meet the Finches'                      'ch plural with es'

# --- shapes a capitalisation sniff used to refuse silently -------------------
# Three parts: the surname is the LAST word, not the second.
printf 'name: Ana Maria Silva\n' > "$WORK/three"
caught  "$WORK/three" 'so The Silvas arrived'              'three-part name, plural of the last word'
caught  "$WORK/three" 'the Silva Family trust'             'three-part name, family form'
ignored "$WORK/three" 'The Marias visit'                   'the middle word is not the surname'

# Non-ASCII letters: an [A-Z][a-z]+ regex matched none of this.
printf 'name: José García\n' > "$WORK/accent"
caught  "$WORK/accent" 'hosting the Garcías'               'accented plural'
caught  "$WORK/accent" 'the García family plan'            'accented family form'

# Spacing after the marker is not load-bearing.
printf 'name:Ada Lovelace\n' > "$WORK/tight"
caught  "$WORK/tight" 'greet the Lovelaces'                'marker without a space still expands'

# The marker must never leak into a pattern, or the guards would hunt for the
# literal string "name: ..." instead of the name.
if expand_deny_terms "$WORK/deny" 2>/dev/null | cut -f3 | grep -q '^name:'; then
  ko "the name: marker leaked into a pattern"
else
  ok "the name: marker never leaks into a pattern"
fi

# --- expansion is opt-in -----------------------------------------------------
# Personhood cannot be read off capitalisation. Two capitalised words are just
# as likely to be a real bank or card issuer in this repo's list, and
# deriving from the second word would turn such an entry into terms
# that match ordinary code. Invented org name for the same reason as above.
printf 'Vandelay Bancorp\n' > "$WORK/org"
caught  "$WORK/org" 'issuer: Vandelay Bancorp'             'an unmarked entry, literally'
ignored "$WORK/org" 'consult The Bancorps'                 'no household plural without the marker'
ignored "$WORK/org" 'the Bancorp Family office'            'no family form without the marker'

# ...but an unmarked name-shaped entry is NOT silently ignored. Silence is the
# failure mode #118 was filed for.
if expand_deny_terms "$WORK/org" 2>&1 >/dev/null | grep -q 'unmarked'; then
  ok "an unmarked name-shaped entry warns on stderr"
else
  ko "an unmarked name-shaped entry was silently left alone"
fi
# The note must not print the whole entry -- same four-character disclosure
# the guards' hit notes use.
if expand_deny_terms "$WORK/org" 2>&1 >/dev/null | grep -q 'Vandelay Bancorp'; then
  ko "the note disclosed the full entry"
else
  ok "the note discloses only a four-character prefix"
fi

# A lone word has no name shape: no expansion, no warning.
printf 'Lovelace\n' > "$WORK/single"
if [ "$(expand_deny_terms "$WORK/single" 2>/dev/null | wc -l | tr -d ' ')" = "1" ]; then
  ok "a single-word entry yields exactly one matcher"
else
  ko "a single-word entry was expanded"
fi
if [ -z "$(expand_deny_terms "$WORK/single" 2>&1 >/dev/null)" ]; then
  ok "a single-word entry does not warn"
else
  ko "a single-word entry warned"
fi

# A single-word literal stays SUBSTRING-matched: a bare token can hide inside
# an identifier, so bounding literals would cost real detection.
caught  "$WORK/single" 'user=adaLovelace_prod'               'a literal hiding inside an identifier'

# --- numeric entries keep their own bounding ---------------------------------
printf '# a comment\n123456\n' > "$WORK/mixed"
caught  "$WORK/mixed" 'total 123456 end'                   'a numeric term standing alone'
ignored "$WORK/mixed" 'ref 91234567 in a longer number'    'a numeric term inside a longer number'
ignored "$WORK/mixed" '# a comment'                        'a comment line is not a term'

# A missing file is not an error -- the guards run on machines without one.
if expand_deny_terms "$WORK/does-not-exist" >/dev/null 2>&1; then
  ok "a missing deny file is silently empty"
else
  ko "a missing deny file errored"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
