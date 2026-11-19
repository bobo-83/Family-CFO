#!/bin/sh
# Tests for the deployment tooling's failure modes (#173).
#
# Three things are pinned here, all of them silent in normal use:
#
#   1. a script must not EXIT on a command that merely skipped;
#   2. the deployment registry must survive the machine it is written on —
#      missing directories, pre-move files, unwritable destinations;
#   3. doctor.sh must ask the API container what the AI endpoint is, rather than
#      assuming the single-machine default.
#
# Mostly behavioural: each assertion drives the real functions from
# scripts/lib/deploy-env.sh in a sandboxed HOME, or runs the real script files
# and reads their exit status. The structural assertions are the lint rule in
# section 1 (the defect has a SHAPE, and the shape is what generalises) and the
# probe-shape check in section 3, both in the spirit of deny-term-shapes.test.sh.
#
# No container, no network, no docker: nothing here needs the stack running.
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)

WORK=$(mktemp -d)
# Snippets get this as $SANDBOX_REPO. NEVER the real checkout: record_deployment
# and forget_deployment also read (and rewrite) <repo-root>/.deploy.history, and
# a test must not touch the operator's own registry.
mkdir -p "$WORK/repo"
# One test makes a directory unwritable on purpose; restore before removing.
trap 'chmod -R u+rwX "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

pass=0
fail=0
ok() { printf '  PASS  %s\n' "$1"; pass=$((pass + 1)); }
ko() { printf '  FAIL  %s\n' "$1"; fail=$((fail + 1)); }

# Runs a snippet in a bash with the library loaded and HOME pointed at a private
# directory, so no test here can touch the operator's real ~/.family-cfo. The
# overrides the library honours are cleared first: a snippet must see the
# defaults, whatever the surrounding environment happens to carry.
# The snippet's exit status is the assertion.
in_lib() { # shell-snippet
  HOME="$WORK/home" DEPLOY_ENV_FILE='' DEPLOY_HISTORY_FILE='' SANDBOX_REPO="$WORK/repo" \
    bash -c '
      unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
      . "'"$REPO_ROOT"'/scripts/lib/deploy-env.sh"
      log()  { :; }; warn() { :; }; die() { exit 1; }
      '"$1"
}

# in_lib with the library's own overrides SET rather than cleared — for the
# tests that are about those overrides.
#
# The assignments are passed IN rather than written as `VAR=x in_lib …`: an
# assignment before a shell *function* call stays in the calling shell after it
# returns (POSIX), so the `VAR=x func` form leaks a DEPLOY_HISTORY_FILE into
# every test that follows and they fail for reasons nothing to do with them.
in_lib_env() { # <assignments-run-in-the-subshell> <shell-snippet>
  HOME="$WORK/home" SANDBOX_REPO="$WORK/repo" \
    bash -c "
      $1
      . '$REPO_ROOT/scripts/lib/deploy-env.sh'
      log()  { :; }; warn() { :; }; die() { exit 1; }
      $2"
}

# ===========================================================================
# 1. A skipped final step must not become a failed deploy
# ===========================================================================
# The premise, stated as an executable fact rather than a comment: under
# `set -e`, `[ 0 = 1 ] && cmd` does not abort the script — it simply IS the
# script's last command, so its status 1 becomes the script's exit status.
# That asymmetry is the whole of bug #1.
if bash -c 'set -euo pipefail; [ 0 = 1 ] && echo ios' >/dev/null 2>&1; then
  ko "premise: a skipped trailing && list should exit non-zero (bash semantics changed?)"
else
  ok "premise: a skipped trailing && list exits non-zero"
fi

# The lint rule. Any scripts/*.sh whose last executable statement is a bare &&/||
# list is one flag flip away from exiting 1 on a success path, which is how both
# patch.sh and deploy.sh shipped (issue #173). This catches the CLASS instead of
# the two instances: a third script with the same tail fails here. (`if …; then`
# guards the list against errexit, but the status it leaves behind is still the
# script's exit status; the fix is to end the file with `exit 0`.)
lint_bad=0
for f in "$REPO_ROOT"/scripts/*.sh; do
  [ -f "$f" ] || continue
  last=$(awk '
    /^[[:space:]]*#/ { next }                     # comments
    /^[[:space:]]*$/ { next }                     # blank
    { line = $0 }
    END { print line }
  ' "$f")
  case "$last" in
    *'&&'* | *'||'*)
      # `exit 0` is allowed to be preceded by a list; only a tail that IS the
      # list is the defect.
      ko "$(basename "$f"): last statement is an &&/|| list — end with \`exit 0\` (#173): $last"
      lint_bad=1
      ;;
    *) ok "$(basename "$f"): last statement is not an &&/|| list" ;;
  esac
done
[ "$lint_bad" -eq 0 ] || true   # counted, not fatal: the summary decides

# The two scripts from the issue must end explicitly successful. Asserted
# separately from the lint rule so a regression says which one broke.
for f in patch.sh deploy.sh; do
  if tail -n 5 "$REPO_ROOT/scripts/$f" | grep -qE '^[[:space:]]*exit 0[[:space:]]*$'; then
    ok "$f ends with an explicit exit 0"
  else
    ko "$f does not end with an explicit exit 0"
  fi
done

# report_setup_drift is documented as advisory; its return value must be
# asserted, not incidental (patch.sh calls it as the last statement of the
# remote path).
if awk '/^report_setup_drift\(\)/,/^\}/' "$REPO_ROOT/scripts/patch.sh" |
     grep -qE '^[[:space:]]*return 0[[:space:]]*$'; then
  ok "report_setup_drift returns 0 explicitly (advisory checks stay advisory)"
else
  ko "report_setup_drift's success is incidental, not explicit"
fi

# ===========================================================================
# 2. The registry: one per machine, and never fatal
# ===========================================================================
if [ "$(in_lib 'printf "%s" "$(deploy_home)"')" = "$WORK/home/.family-cfo" ]; then
  ok "the registry lives under \$HOME/.family-cfo, not the checkout"
else
  ko "deploy_home does not resolve \$HOME/.family-cfo"
fi

# The bug in its narrowest form: the base directory does not exist on a fresh
# machine, `mv` onto a missing directory exits 1, and these scripts run under
# `set -e` with the record call placed AFTER the stack is already up.
if in_lib '
  [ ! -d "$HOME/.family-cfo" ] || exit 9
  record_deployment "$SANDBOX_REPO" remote freshbox me 22 /srv/app docker-compose.yml
'; then
  ok "recording the FIRST deployment succeeds with no ~/.family-cfo yet"
else
  ko "recording into a missing base directory failed (or created it too late)"
fi

if [ -f "$WORK/home/.family-cfo/.deploy.history" ]; then
  ok "the first record created the shared registry"
else
  ko "no registry was written to the shared location"
fi

# Same key twice is one entry, not two — and the temp file must be gone.
in_lib '
  record_deployment "$SANDBOX_REPO" remote box1 me 22 /srv/app a.yml
  record_deployment "$SANDBOX_REPO" remote box1 me 22 /srv/app a.yml
  record_deployment "$SANDBOX_REPO" remote box2 me 22 /srv/app a.yml
  n=$(awk -F'"'"'\t'"'"' '"'"'$1=="remote|box1|/srv/app"{c++} END{print c+0}'"'"' "$HOME/.family-cfo/.deploy.history")
  [ "$n" = "1" ] || exit 1
  ls "$HOME/.family-cfo" | grep -q XXXXXX && exit 1
  exit 0
' && ok "re-recording one place keeps one row and leaves no temp file" \
  || ko "re-recording duplicated an entry or left a temp file behind"

# The reader must see BOTH the shared file and the pre-move checkout file: a
# reader that only knows the new path makes an operator's old stacks vanish.
mkdir -p "$WORK/old-checkout"
printf 'local\toldbox\toldbox\t-\t-\t/srv/old\ta.yml\t2026-01-01T00:00:00Z\n' \
  | awk -F'\t' '{printf "%s|%s|%s\t%s\n", "local", $2, $6, $0}' > "$WORK/old-checkout/.deploy.history"
if in_lib '
  deploy_registry_rows "'"$WORK"'/old-checkout" | grep -q "srv/old"
' && [ -n "$(cd "$REPO_ROOT" && HOME="$WORK/home" bash -c '
      . scripts/lib/deploy-env.sh
      load_deploy_env "'"$WORK"'/old-checkout" >/dev/null 2>&1 || exit 1
      deploy_registry_rows "'"$WORK"'/old-checkout"
    ')" ]; then
  ok "the reader still finds entries recorded in the pre-move checkout file"
else
  ko "entries in the pre-move <checkout>/.deploy.history are invisible"
fi

# Same place in both files: the fresher timestamp is what is reported.
printf 'remote|dup|/srv/dup\tremote\tdup\t-\t-\t/srv/dup\ta.yml\t2026-01-01T00:00:00Z\n' \
  >> "$WORK/home/.family-cfo/.deploy.history"
printf 'remote|dup|/srv/dup\tremote\tdup\t-\t-\t/srv/dup\ta.yml\t2026-09-09T09:09:09Z\n' \
  >> "$WORK/old-checkout/.deploy.history"
if [ "$(in_lib 'deploy_registry_rows "'"$WORK"'/old-checkout" | grep -c "remote|dup|/srv/dup"')" = "1" ] &&
   in_lib 'deploy_registry_rows "'"$WORK"'/old-checkout" | grep "remote|dup" | grep -q 2026-09-09'; then
  ok "one place recorded twice dedupes to its freshest timestamp"
else
  ko "the same place appears twice, or with a stale timestamp"
fi

# forget must clear EVERY file a read consults, because reads union them: an
# entry left in the pre-move file would simply come back. What remains in the
# shared file is the TOMBSTONE — the record of the forget, not the deployment —
# and the pre-move file is retired outright once its rows are folded in.
HOME="$WORK/home" bash -c "
  set -euo pipefail
  unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
  . '$REPO_ROOT/scripts/lib/deploy-env.sh'
  forget_deployment '$WORK/old-checkout' 'remote|dup|/srv/dup'
  deploy_registry_rows '$WORK/old-checkout' | grep -qF 'remote|dup' && exit 1
  grep -qF \"\$(printf 'remote|dup|/srv/dup\tremote\t')\" '$WORK/home/.family-cfo/.deploy.history' && exit 1
  [ ! -f '$WORK/old-checkout/.deploy.history' ]
" && ok "forget removes the entry from every registry file (only its tombstone remains)" \
  || ko "forget left the entry somewhere it can be read back"

# An unwritable registry must cost the operator a warning, not a deploy: the
# call sites are plain commands under `set -e`, after the stack is up.
chmod 500 "$WORK/home/.family-cfo"
# The warning on stderr is the point of this case; it is not test noise.
if in_lib '_DEPLOY_REGISTRY_LOCK_TRIES=2; export _DEPLOY_REGISTRY_LOCK_TRIES; set -euo pipefail; record_deployment "$SANDBOX_REPO" remote doomed me 22 /srv/x a.yml' 2>/dev/null; then
  ok "a failed registry write does NOT fail the deploy"
else
  ko "an unwritable registry aborted the deploy (set -e)"
fi
if [ "$(grep -c '' "$WORK/home/.family-cfo/.deploy.history")" -ge 1 ]; then
  ok "a failed write left the previous registry intact"
else
  ko "a failed write destroyed the registry"
fi
chmod 700 "$WORK/home/.family-cfo"

# --- integrity: a partial read may never be written back ---------------------
# The registry exists to answer "what is still running out there". Recording
# from a read that failed half-way turns a permissions accident in ONE file into
# a MISSING deployment in the other — and an un-listed stack is precisely the
# box that keeps holding ports, disk and a database of household finances.
# Skipping the write is recoverable; un-recording is not.
printf 'remote|precious|/srv/p\tremote\tprecious\tu\t22\t/srv/p\tc.yml\t2026-11-01T00:00:00Z\n' \
  > "$WORK/home/.family-cfo/.deploy.history"
printf 'local|opaque|/srv/o\tlocal\topaque\t-\t-\t/srv/o\tc.yml\t2026-01-01T00:00:00Z\n' \
  > "$WORK/repo/.deploy.history"
chmod 000 "$WORK/repo/.deploy.history"
# The callers' real shell options, not a gentler imitation of them: `record_
# deployment` is a plain command under `set -euo pipefail` after the stack is up.
if in_lib 'set -euo pipefail; record_deployment "$SANDBOX_REPO" remote newbox me 22 /srv/n c.yml' 2>/dev/null; then
  ok "an unreadable sibling does not fail the deploy"
else
  ko "an unreadable sibling failed the deploy"
fi
if grep -q precious "$WORK/home/.family-cfo/.deploy.history" &&
   ! grep -q newbox "$WORK/home/.family-cfo/.deploy.history"; then
  ok "…and the existing registry is NOT rewritten from an incomplete read"
else
  ko "an unreadable sibling silently replaced the shared registry with one row"
fi
# The operator has to be told: the deploy is live and un-listed.
if in_lib 'set -euo pipefail; record_deployment "$SANDBOX_REPO" remote newbox me 22 /srv/n c.yml' 2>&1 >/dev/null |
     grep -qi 'UNLISTED'; then
  ok "a skipped record says the deploy is live but UNLISTED"
else
  ko "a skipped record was silent about the deploy being unlisted"
fi
chmod 600 "$WORK/repo/.deploy.history"

# -r is a snapshot and it lies for root, so the read itself is what decides.
# This is the case that survives a `-r` check: the FIRST file passes it and fails
# at the `cat`, the second reads fine. A loop inside a pipeline cannot see that —
# a `while` loop's status is the last command run in its body, so the successful
# second read makes the whole loop (and the pipeline, and `pipefail`) report
# success, the first file's rows vanish, and the writer installs a registry that
# quietly lost them. So each read is checked where it happens. The seam is
# patched rather than faked with permissions, per the repo's test rule: select
# the fallback explicitly, don't hope the host behaves.
printf 'remote|first|/srv/f\tremote\tfirst\tu\t22\t/srv/f\tc.yml\t2026-11-01T00:00:00Z\n' \
  > "$WORK/home/.family-cfo/.deploy.history"
printf 'local|second|/srv/s\tlocal\tsecond\t-\t-\t/srv/s\tc.yml\t2026-01-01T00:00:00Z\n' \
  > "$WORK/repo/.deploy.history"
if in_lib '
  set -euo pipefail
  cat() { case "$*" in *family-cfo*) return 1 ;; *) command cat "$@" ;; esac; }
  deploy_registry_rows "$SANDBOX_REPO" >/dev/null 2>&1
' ; then
  ko "a first file that passes -r and fails at cat was reported as a complete read"
else
  ok "a first file that passes -r and fails at cat is still an incomplete read"
fi
before_shared="$(cat "$WORK/home/.family-cfo/.deploy.history")"
in_lib '
  set -euo pipefail
  cat() { case "$*" in *family-cfo*) return 1 ;; *) command cat "$@" ;; esac; }
  record_deployment "$SANDBOX_REPO" remote third me 22 /srv/t c.yml
' 2>/dev/null || true
if [ "$(cat "$WORK/home/.family-cfo/.deploy.history")" = "$before_shared" ] &&
   ! grep -q third "$WORK/home/.family-cfo/.deploy.history"; then
  ok "…and the writer refused to rebuild the shared file from the half it could read"
else
  ko "the shared registry lost its rows to a read that failed after the -r check"
fi

# `deployments.sh list` is the other half: it must degrade to "show what can be
# read, and say it is partial", rather than dying on the failed read — and the
# numbers it prints are what `stop`/`destroy` act on, so silence is not an option.
#
# Entries are kind=local with a directory that does not exist: status_of then
# answers "0 running" from a failed cd, so the test never shells out to ssh or
# docker and costs nothing on a runner.
mkdir -p "$WORK/cli/scripts/lib"
cp "$REPO_ROOT/scripts/deployments.sh" "$WORK/cli/scripts/"
cp "$REPO_ROOT/scripts/lib/deploy-env.sh" "$WORK/cli/scripts/lib/"
printf 'local|listed|/nonexistent-fcfo-l\tlocal\tlisted\t-\t-\t/nonexistent-fcfo-l\tc.yml\t2026-11-01T00:00:00Z\n' \
  > "$WORK/home/.family-cfo/.deploy.history"
printf 'local|hidden|/nonexistent-fcfo-h\tlocal\thidden\t-\t-\t/nonexistent-fcfo-h\tc.yml\t2026-01-01T00:00:00Z\n' \
  > "$WORK/cli/.deploy.history"
chmod 000 "$WORK/cli/.deploy.history"
# `cli_rc=0` then `|| cli_rc=$?`: a bare `var=$(cmd)` under `set -e` aborts the
# whole suite when the command fails, and the status IS the assertion.
cli_rc=0; cli_out=$(HOME="$WORK/home" bash "$WORK/cli/scripts/deployments.sh" list 2>&1) || cli_rc=$?
chmod 600 "$WORK/cli/.deploy.history"
if [ "$cli_rc" -eq 0 ] && printf '%s\n' "$cli_out" | grep -q listed &&
   printf '%s\n' "$cli_out" | grep -qi 'INCOMPLETE'; then
  ok "'list' still shows the readable entries and flags the listing as incomplete"
else
  ko "'list' lost the readable entries when one file was unreadable (rc=$cli_rc)"
fi
# An empty listing while something was unreadable must not claim "nothing here".
printf 'local|hidden|/nonexistent-fcfo-h\tlocal\thidden\t-\t-\t/nonexistent-fcfo-h\tc.yml\t2026-01-01T00:00:00Z\n' \
  > "$WORK/cli/.deploy.history"
rm -f "$WORK/home/.family-cfo/.deploy.history"
chmod 000 "$WORK/cli/.deploy.history"
# `cli_rc=0` then `|| cli_rc=$?`: a bare `var=$(cmd)` under `set -e` aborts the
# whole suite when the command fails, and the status IS the assertion.
cli_rc=0; cli_out=$(HOME="$WORK/home" bash "$WORK/cli/scripts/deployments.sh" list 2>&1) || cli_rc=$?
chmod 600 "$WORK/cli/.deploy.history"
if [ "$cli_rc" -ne 0 ] && printf '%s\n' "$cli_out" | grep -qi 'not trustworthy'; then
  ok "'list' refuses to report 'nothing recorded' when a file could not be read"
else
  ko "'list' answered 'no deployments' while a registry file was unreadable"
fi

# The dangerous half of a partial listing. Rows from the unreadable file are
# simply absent, so every number below it shifts: the operator reads a table
# where #1 is boxA, types `stop 1`, and ssh is handed boxB. Listing stays
# available for diagnosis; acting on a short inventory is refused BEFORE ssh or
# docker is invoked. Refused by host name too — the missing file can hold a
# second entry for the same host at a different directory, which is the case
# where the wrong stack gets torn down.
mkdir -p "$WORK/act/scripts/lib"
cp "$REPO_ROOT/scripts/deployments.sh" "$WORK/act/scripts/"
cp "$REPO_ROOT/scripts/lib/deploy-env.sh" "$WORK/act/scripts/lib/"
printf 'local|boxA|/nonexistent-fcfo-a\tlocal\tboxA\t-\t-\t/nonexistent-fcfo-a\tc.yml\t2026-11-02T00:00:00Z\n' \
  > "$WORK/home/.family-cfo/.deploy.history"
printf 'local|boxB|/nonexistent-fcfo-b\tlocal\tboxB\t-\t-\t/nonexistent-fcfo-b\tc.yml\t2026-01-01T00:00:00Z\n' \
  > "$WORK/act/.deploy.history"
cli_rc=0; cli_out=$(HOME="$WORK/home" bash "$WORK/act/scripts/deployments.sh" list 2>&1) || cli_rc=$?
if [ "$cli_rc" -eq 0 ] && printf '%s\n' "$cli_out" | grep -q boxA && printf '%s\n' "$cli_out" | grep -q boxB; then
  ok "the complete inventory lists both boxes for acting on"
else
  ko "the complete inventory did not list both boxes"
fi
chmod 000 "$WORK/home/.family-cfo/.deploy.history"
# Selectors here are HOSTS on purpose. A numeric selector is now refused for its
# own reason (numbers belong to the listing printed by the same run), so using
# '1' here would pass on the refusal message alone and say nothing about the
# inventory guard — the exact vacuity mutation-checking would have missed.
for sel in boxA boxB; do
  cli_rc=0; cli_out=$(HOME="$WORK/home" bash "$WORK/act/scripts/deployments.sh" stop "$sel" 2>&1) || cli_rc=$?
  if [ "$cli_rc" -ne 0 ] && printf '%s\n' "$cli_out" | grep -qi 'inventory is incomplete' &&
     ! printf '%s\n' "$cli_out" | grep -q 'Stopping containers'; then
    ok "acting on a partial inventory is refused before touching a host (selector '$sel')"
  else
    ko "a partial inventory let selector '$sel' reach a host: $(printf '%s\n' "$cli_out" | grep -c 'Stopping containers') action(s)"
  fi
done
cli_rc=0; cli_out=$(HOME="$WORK/home" bash "$WORK/act/scripts/deployments.sh" destroy boxA 2>&1) || cli_rc=$?
if printf '%s\n' "$cli_out" | grep -qi 'inventory is incomplete' && ! printf '%s\n' "$cli_out" | grep -q 'Type the host name'; then
  ok "destroy — the one that deletes the database — is refused on a partial inventory too"
else
  ko "destroy proceeded on a partial inventory"
fi
chmod 600 "$WORK/home/.family-cfo/.deploy.history"

# --- The inventory is now STABLE, and selectors NAME a place -------------------
# Two rules, both about the same failure: an action reaching a box nobody
# pointed at.
#
# 1. Ordering comes from the KEY (kind|host|dir), not the last-deployed stamp.
#    Every patch rewrites that stamp, so a stamp sort renumbers the inventory
#    underneath the operator — list shows boxA as 1, patching boxB moves boxB to
#    1, and `stop 1` then means boxB. The key changes only when a place is added
#    or forgotten. (This ordering is also what `stop boxA`'s landing dir asserts:
#    the fake docker reports the directory it was run in.)
# 2. A row position is accepted ONLY in the interactive menu, by the process that
#    printed the table. On the command line a number is refused with the reason,
#    BEFORE anything is run against a host.
# Real stack roots, so `cd <dir> && docker compose …` runs and the fake docker
# can say which directory — which box — an action actually reached.
# Earlier blocks left rows in every file this sandbox can see (home, repo, and
# the act checkout above). This block asserts an exact numbering, so it starts
# from an empty one — otherwise it is testing the leftovers, not the sort.
rm -f "$WORK/home/.family-cfo/.deploy.history" "$WORK/repo/.deploy.history" \
      "$WORK/act/.deploy.history"
mkdir -p "$WORK/boxes/a" "$WORK/boxes/b" "$WORK/boxes/a2" "$WORK/bin"
cat > "$WORK/bin/docker" <<'FAKE'
#!/bin/sh
echo "ACTION LANDED ON: $(pwd)"
FAKE
chmod +x "$WORK/bin/docker"
printf 'local|boxA|%s\tlocal\tboxA\t-\t-\t%s\tc.yml\t2026-11-02T00:00:00Z\n' \
  "$WORK/boxes/a" "$WORK/boxes/a" > "$WORK/home/.family-cfo/.deploy.history"
printf 'local|boxB|%s\tlocal\tboxB\t-\t-\t%s\tc.yml\t2026-01-01T00:00:00Z\n' \
  "$WORK/boxes/b" "$WORK/boxes/b" >> "$WORK/home/.family-cfo/.deploy.history"
numbers() { HOME="$WORK/home" bash "$WORK/act/scripts/deployments.sh" list 2>/dev/null |
  awk '/^  [0-9]+ /{printf "%s=%s ", $1, $3}'; }
landed() { # <action> <selector> → what the fake docker reports, or the refusal
  cli_rc=0; cli_out=$(HOME="$WORK/home" PATH="$WORK/bin:$PATH" \
    bash "$WORK/act/scripts/deployments.sh" "$1" "$2" 2>&1) || cli_rc=$?
  printf '%s\n' "$cli_out" | grep -E 'ACTION LANDED ON|Refusing to act|is recorded|Forgot' |
    sed "s|$WORK/boxes|boxes|g"
}
before="$(numbers)"
in_lib 'set -euo pipefail; record_deployment "$SANDBOX_REPO" local boxB - - '"$WORK"'/boxes/b c.yml' \
  >/dev/null 2>&1
after="$(numbers)"
if [ "$before" = "$after" ] && [ "$before" = "1=boxA 2=boxB " ]; then
  ok "a successful patch does not renumber the inventory (key order, not stamp order)"
else
  ko "the numbering moved under the operator: '$before' → '$after'"
fi
[ "$(landed stop boxA)" = "ACTION LANDED ON: boxes/a" ] \
  && ok "a host selector acts on the box it names" \
  || ko "a host selector reached the wrong place: $(landed stop boxA)"
[ "$(landed stop boxB)" = "ACTION LANDED ON: boxes/b" ] \
  && ok "…and so does the other host" \
  || ko "the second host selector reached the wrong place: $(landed stop boxB)"
printf 'local|boxA|%s\tlocal\tboxA\t-\t-\t%s\tc.yml\t2026-10-01T00:00:00Z\n' \
  "$WORK/boxes/a2" "$WORK/boxes/a2" >> "$WORK/home/.family-cfo/.deploy.history"
if printf '%s\n' "$(landed stop boxA)" | grep -q 'is recorded 2 times' &&
   printf '%s\n' "$(landed stop boxA)" | grep -q 'local|boxA|boxes/a2'; then
  ok "a host that runs two stacks is refused, and the keys it could mean are printed"
else
  ko "an ambiguous host was resolved by luck: $(landed stop boxA)"
fi
if printf '%s\n' "$(landed forget "local|boxA|$WORK/boxes/a2")" | grep -q 'Forgot boxA'; then
  ok "the exact key resolves the ambiguous case"
else
  ko "the full key was not accepted: $(landed forget "local|boxA|$WORK/boxes/a2")"
fi
if printf '%s\n' "$(landed stop 1)" | grep -q 'Refusing to act' &&
   ! printf '%s\n' "$(landed stop 1)" | grep -q 'ACTION LANDED'; then
  ok "a row position on the command line is refused before anything runs"
else
  ko "a numeric selector reached a host: $(landed stop 1)"
fi
rm -rf "$WORK/boxes" "$WORK/bin"

# The interactive menu is the one place a row position is honoured — by the
# process that printed the table. Its safety rests on two shapes, so pin them:
# the number is resolved by resolve_display_index (not the general resolver), the
# inventory is declared complete BEFORE the prompt is shown, and the action is
# handed the resolved KEY rather than the position — so a concurrent change can
# at worst produce an honest "no recorded deployment", never an action on
# whatever now occupies that row.
menu_block=$(sed -n '/^# --- Interactive/,$p' "$REPO_ROOT/scripts/deployments.sh")
if [ -n "$menu_block" ] &&
   printf '%s\n' "$menu_block" | grep -q 'resolve_display_index "$choice"' &&
   printf '%s\n' "$menu_block" | grep -q 'chosen_key="${KINDS\[$idx\]}|\${HOSTS\[$idx\]}|\${DIRS\[$idx\]}"' &&
   printf '%s\n' "$menu_block" | grep -q 'require_complete_inventory "act interactively"' &&
   ! printf '%s\n' "$menu_block" | grep -qE 'do_(stop|remove|destroy|uninstall|forget) +"\$\(\(idx'; then
  ok "the menu resolves its number in-process and acts by key, not by row position"
else
  ko "the menu lost the shape that makes its numbers safe: $(printf '%s' "$menu_block" | grep -cE 'do_[a-z]+ +"\$\(\(idx') numeric pass-through(s)"
fi

# --- and the ORDER itself is fully determined ---------------------------------
# These rows become what the menu's numbers mean, so they cannot depend on awk's
# hash iteration (unspecified by POSIX, different between awk builds): key order,
# LC_ALL=C.
printf 'local|zz|/nonexistent-fcfo-zz\tlocal\tzz\t-\t-\t/nonexistent-fcfo-zz\tc.yml\t2026-05-05T00:00:00Z\n' \
  > "$WORK/home/.family-cfo/.deploy.history"
printf 'local|aa|/nonexistent-fcfo-aa\tlocal\taa\t-\t-\t/nonexistent-fcfo-aa\tc.yml\t2026-05-05T00:00:00Z\n' \
  >> "$WORK/home/.family-cfo/.deploy.history"
printf 'local|mm|/nonexistent-fcfo-mm\tlocal\tmm\t-\t-\t/nonexistent-fcfo-mm\tc.yml\t2020-01-01T00:00:00Z\n' \
  >> "$WORK/home/.family-cfo/.deploy.history"
order="$(in_lib 'deploy_registry_rows "$SANDBOX_REPO" | cut -f1' 2>/dev/null)"
[ "$(printf '%s\n' "$order" | head -1)" = "local|aa|/nonexistent-fcfo-aa" ] &&
  [ "$(printf '%s\n' "$order" | tail -1)" = "local|zz|/nonexistent-fcfo-zz" ] \
  && ok "rows come back in KEY order, so a number means the same thing every run" \
  || ko "row order is not deterministic: $(printf '%s\n' "$order" | tr '\n' ' ')"

# An explicit DEPLOY_HISTORY_FILE selects ONE registry — nothing else is read
# into it and nothing else is rewritten. That is what makes it usable as a test
# seam and as "put my registry over there", and it is what it did before the
# shared directory existed; unioning the pre-move file into it would smuggle
# someone else's deployments into an isolated file.
printf 'remote|otherworld|/srv/ow\tremote\totherworld\tu\t22\t/srv/ow\tc.yml\t2026-01-01T00:00:00Z\n' \
  > "$WORK/repo/.deploy.history"
: > "$WORK/only.history"
in_lib_env "DEPLOY_HISTORY_FILE=\"$WORK/only.history\"" '
  set -euo pipefail
  record_deployment "$SANDBOX_REPO" remote isolated me 22 /srv/i c.yml
' || ko "record with DEPLOY_HISTORY_FILE failed"
if grep -q isolated "$WORK/only.history" && ! grep -q otherworld "$WORK/only.history"; then
  ok "DEPLOY_HISTORY_FILE records into that file alone (no legacy rows copied in)"
else
  ko "DEPLOY_HISTORY_FILE let the pre-move registry into the isolated file"
fi
in_lib_env "DEPLOY_HISTORY_FILE=\"$WORK/only.history\"" '
  set -euo pipefail
  forget_deployment "$SANDBOX_REPO" "remote|isolated|/srv/i"
' || ko "forget with DEPLOY_HISTORY_FILE failed"
if grep -q otherworld "$WORK/repo/.deploy.history"; then
  ok "DEPLOY_HISTORY_FILE does not rewrite the pre-move registry either"
else
  ko "the override let a forget reach into the checkout's registry"
fi

# forget's status is the CLI's answer to "did you forget it?".
printf 'remote|lastone|/srv/one\tremote\tlastone\tu\t22\t/srv/one\tc.yml\t2026-01-01T00:00:00Z\n' \
  > "$WORK/home/.family-cfo/.deploy.history"
rm -f "$WORK/repo/.deploy.history"
if in_lib 'set -euo pipefail; forget_deployment "$SANDBOX_REPO" "remote|lastone|/srv/one"' 2>"$WORK/err"; then
  if [ -s "$WORK/err" ]; then
    ko "forgetting the LAST row warned, although the write succeeded: $(cat "$WORK/err")"
  else
    ok "forgetting the last row succeeds in silence (an empty registry is not an error)"
  fi
else
  ko "forgetting the last row reported failure"
fi
# The row's DATA is gone from the inventory; what remains in the file is the
# tombstone that keeps it forgotten no matter which checkout's pre-move file
# still holds a copy. (The file being non-empty is the fix, not a regression:
# a bare delete could not survive other checkouts' legacy files.)
if in_lib 'set -euo pipefail; [ -z "$(deploy_registry_rows "$SANDBOX_REPO")" ]' 2>/dev/null; then
  ok "the last row really is gone from the inventory"
else
  ko "the last row survived its own forget"
fi
if [ "$(awk -F'\t' '$2 == "forgotten" && $1 == "remote|lastone|/srv/one"' "$WORK/home/.family-cfo/.deploy.history" | wc -l | tr -d ' ')" = "1" ]; then
  ok "…and what remains in the file is its tombstone"
else
  ko "the forget left something other than the tombstone behind"
fi
# And the opposite: a file that cannot be rewritten must be REPORTED, not
# glossed over with "the registry entry is gone".
printf 'local|stuck|/nonexistent-fcfo-s\tlocal\tstuck\t-\t-\t/nonexistent-fcfo-s\tc.yml\t2026-01-01T00:00:00Z\n' \
  > "$WORK/home/.family-cfo/.deploy.history"
chmod 500 "$WORK/home/.family-cfo"
if in_lib '_DEPLOY_REGISTRY_LOCK_TRIES=2; export _DEPLOY_REGISTRY_LOCK_TRIES; set -euo pipefail; forget_deployment "$SANDBOX_REPO" "local|stuck|/nonexistent-fcfo-s"' 2>/dev/null; then
  ko "a forget that could not rewrite any file reported success"
else
  ok "a forget that could not rewrite a file reports failure"
fi
chmod 700 "$WORK/home/.family-cfo"
printf 'local|stuck|/nonexistent-fcfo-s\tlocal\tstuck\t-\t-\t/nonexistent-fcfo-s\tc.yml\t2026-01-01T00:00:00Z\n' \
  > "$WORK/home/.family-cfo/.deploy.history"
chmod 500 "$WORK/home/.family-cfo"
# Host selector, not a number: a numeric one is refused on its own terms now, and
# this assertion is about a file refusing to be rewritten.
cli_rc=0; cli_out=$(_DEPLOY_REGISTRY_LOCK_TRIES=2 HOME="$WORK/home" bash "$WORK/cli/scripts/deployments.sh" forget stuck 2>&1) || cli_rc=$?
chmod 700 "$WORK/home/.family-cfo"
if [ "$cli_rc" -ne 0 ] && ! printf '%s\n' "$cli_out" | grep -q "entry is gone"; then
  ok "the CLI does not claim 'the registry entry is gone' when a file was unwritable"
else
  ko "the CLI promised the entry was gone while it was still listed"
fi

# --- Round 4: a forget must survive OTHER checkouts' pre-move files ----------
# Both bugs reproduced before the fix:
#   1. Reads union the pre-move <checkout>/.deploy.history, but a forget could
#      only rewrite the CURRENT checkout's — a deployment forgotten from one
#      checkout came back the moment another fold-in re-read a stale copy. The
#      forget is now a TOMBSTONE row (kind "forgotten") in the shared registry:
#      suppression follows the key into every file a read consults.
#   2. record/forget read and rewrote the shared registry without holding a
#      lock across BOTH steps: two writers each read the same inventory, both
#      reported success, and the last install dropped the other's row. A
#      directory lock — the one atomic primitive POSIX gives on every
#      filesystem here — now spans each read-modify-write.
in_ck() { # <home> <code> — the lib under set -e, against an explicit HOME
  HOME="$1" bash -c "
    set -euo pipefail
    unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
    . '$REPO_ROOT/scripts/lib/deploy-env.sh'
    $2
  " 2>&1
}
ck_hidden() { # <home> <checkout> <key> → 0 iff a COMPLETE read does not list key
  HOME="$1" bash -c "
    set -euo pipefail
    unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
    . '$REPO_ROOT/scripts/lib/deploy-env.sh'
    rows=\$(deploy_registry_rows '$2') || exit 9
    printf '%s' \"\$rows\" | grep -qF '$3' && exit 1
    exit 0
  " 2>/dev/null
}
ck_lists() { # <home> <checkout> <key> → 0 iff a COMPLETE read lists key
  HOME="$1" bash -c "
    set -euo pipefail
    unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
    . '$REPO_ROOT/scripts/lib/deploy-env.sh'
    rows=\$(deploy_registry_rows '$2') || exit 9
    printf '%s' \"\$rows\" | grep -qF '$3'
  " 2>/dev/null
}
mkdir -p "$WORK/ck/home/.family-cfo" "$WORK/ck/a" "$WORK/ck/b" "$WORK/ck/c" \
         "$WORK/ck/c2" "$WORK/ck/d" "$WORK/ck/e"
rm -f "$WORK/ck/home/.family-cfo/.deploy.history" "$WORK/ck"/*/.deploy.history*
# Checkout c NEVER writes, so its pre-move file is never retired — the file a
# forget from another checkout cannot reach, which is the whole point.
printf 'local|x|/dirA\tlocal\tx\t-\t-\t/dirA\tc.yml\t2026-01-01T00:00:00Z\n' > "$WORK/ck/c/.deploy.history"
in_ck "$WORK/ck/home" "record_deployment '$WORK/ck/a' local x - - /dirA c.yml" >/dev/null
in_ck "$WORK/ck/home" "forget_deployment '$WORK/ck/b' 'local|x|/dirA'" >/dev/null
if ck_hidden "$WORK/ck/home" "$WORK/ck/c" 'local|x|/dirA'; then
  ok "a forgotten deployment stays forgotten even where a pre-move copy still sits"
else
  ko "a forget was undone by another checkout's pre-move file"
fi
if grep -qF "$(printf 'local|x|/dirA\tforgotten\t')" "$WORK/ck/home/.family-cfo/.deploy.history"; then
  ok "the tombstone is in the shared file, and never in the listing"
else
  ko "the forget did not record its decision in the shared registry"
fi
# A rewrite must PRESERVE other keys' tombstones — recording y must not
# un-forget x.
in_ck "$WORK/ck/home" "record_deployment '$WORK/ck/b' local y - - /dirY c.yml" >/dev/null
if ck_hidden "$WORK/ck/home" "$WORK/ck/c" 'local|x|/dirA'; then
  ok "a later record keeps other keys' tombstones intact"
else
  ko "recording one deployment resurrected a forgotten one"
fi
# …and recording the FORGOTTEN place again un-forgets it: tombstone gone, row
# back. A tombstone that outlived its own re-deploy would make the place
# un-recordable.
in_ck "$WORK/ck/home" "record_deployment '$WORK/ck/a' local x - - /dirA c.yml" >/dev/null
if ck_lists "$WORK/ck/home" "$WORK/ck/c" 'local|x|/dirA' &&
   ! grep -qF "$(printf 'local|x|/dirA\tforgotten\t')" "$WORK/ck/home/.family-cfo/.deploy.history"; then
  ok "re-deploying a forgotten place removes its tombstone and lists it again"
else
  ko "a place stayed forgotten after being deployed again"
fi
# A forget is a DECISION, not a row with a timestamp: a pre-move copy stamped
# later than the forget must not outvote it.
printf 'local|z|/dirZ\tlocal\tz\t-\t-\t/dirZ\tc.yml\t2099-01-01T00:00:00Z\n' > "$WORK/ck/c2/.deploy.history"
in_ck "$WORK/ck/home" "record_deployment '$WORK/ck/a' local z - - /dirZ c.yml" >/dev/null
in_ck "$WORK/ck/home" "forget_deployment '$WORK/ck/b' 'local|z|/dirZ'" >/dev/null
# A later record — from the checkout that HOLDS the stale copy — rewrites the
# shared file; the rewrite must not let the 2099 legacy copy outvote the forget.
in_ck "$WORK/ck/home" "record_deployment '$WORK/ck/c2' local w3 - - /dirW3 c.yml" >/dev/null
if ck_hidden "$WORK/ck/home" "$WORK/ck/c2" 'local|z|/dirZ'; then
  ok "a tombstone suppresses a legacy row even when that row is stamped later"
else
  ko "the fresher stamp outvoted the forget"
fi
# The rewrite itself must not INSTALL the stale copy beside the tombstone: the
# file is read by other checkouts' scripts too, and a live row sitting next to
# the forget is one tombstone-blind reader away from acting on it.
if grep -qF "$(printf 'local|z|/dirZ\tlocal\t')" "$WORK/ck/home/.family-cfo/.deploy.history"; then
  ko "a rewrite installed a live row for a forgotten place, beside its tombstone"
else
  ok "…and the rewrite left no live copy of the forgotten place in the file"
fi
# The reviewer's exact repro: a row migrated from a checkout's OWN pre-move
# file, forgotten elsewhere, then the checkout writes again. The fold is
# one-way — the first write retires the file, so there is nothing to re-fold.
printf 'local|x2|/old\tlocal\tx2\t-\t-\t/old\tc.yml\t2026-01-01T00:00:00Z\n' > "$WORK/ck/d/.deploy.history"
in_ck "$WORK/ck/home" "record_deployment '$WORK/ck/d' local w - - /dirW c.yml" >/dev/null
if [ ! -f "$WORK/ck/d/.deploy.history" ] && [ -f "$WORK/ck/d/.deploy.history.migrated" ] &&
   grep -qF 'local|x2|/old' "$WORK/ck/d/.deploy.history.migrated" &&
   ck_lists "$WORK/ck/home" "$WORK/ck/d" 'local|x2|/old'; then
  ok "a folded pre-move file is retired (renamed, rows preserved and folded in)"
else
  ko "the pre-move file was not retired, or its rows were lost"
fi
in_ck "$WORK/ck/home" "forget_deployment '$WORK/ck/b' 'local|x2|/old'" >/dev/null
in_ck "$WORK/ck/home" "record_deployment '$WORK/ck/d' local w2 - - /dirW2 c.yml" >/dev/null
if ck_hidden "$WORK/ck/home" "$WORK/ck/d" 'local|x2|/old'; then
  ok "…and a forgotten row cannot come back from the checkout that owned it"
else
  ko "forgetting was undone by the same checkout writing again"
fi
# An explicit DEPLOY_HISTORY_FILE never retires: its file was never consulted.
printf 'local|k|/keep\tlocal\tk\t-\t-\t/keep\tc.yml\t2026-01-01T00:00:00Z\n' > "$WORK/ck/e/.deploy.history"
HOME="$WORK/ck/home" bash -c "
  set -euo pipefail
  unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
  export DEPLOY_HISTORY_FILE='$WORK/ck/home/.family-cfo/.deploy.history'
  . '$REPO_ROOT/scripts/lib/deploy-env.sh'
  record_deployment '$WORK/ck/e' local e1 - - /dirE c.yml
" >/dev/null 2>&1
if [ -f "$WORK/ck/e/.deploy.history" ] && [ ! -f "$WORK/ck/e/.deploy.history.migrated" ]; then
  ok "an explicit DEPLOY_HISTORY_FILE never retires the sibling it never read"
else
  ko "the one-file seam retired a pre-move file it never consulted"
fi

# The lock spans the read AND the write. While it is held, a writer waits —
# and then lands BOTH rows, not just its own.
shared="$WORK/ck/home/.family-cfo/.deploy.history"
rm -f "$WORK/ck"/*/.deploy.history*
printf 'local|base|/base\tlocal\tbase\t-\t-\t/base\tc.yml\t2026-01-01T00:00:00Z\n' > "$shared"
mkdir "$shared.lock"
in_ck "$WORK/ck/home" "record_deployment '$WORK/ck/a' local slow - - /slow c.yml" >/dev/null &
waiter=$!
sleep 0.5
if grep -qF 'local|slow|/slow' "$shared"; then
  ko "a record wrote while another write held the lock"
else
  ok "a record waits while the lock is held"
fi
rmdir "$shared.lock"
wait "$waiter" 2>/dev/null || true
if grep -qF 'local|base|/base' "$shared" && grep -qF 'local|slow|/slow' "$shared" &&
   [ ! -d "$shared.lock" ]; then
  ok "…then lands both rows and releases the lock"
else
  ko "the waiting record lost a row or left the lock behind"
fi
# A lock that is never released must be refused loudly, not waited on forever:
# a record stays non-fatal (LIVE but UNLISTED, like a partial read), a forget
# reports failure — its whole answer is whether the entry is gone.
mkdir "$shared.lock"
lock_rc=0; lock_out=$(in_ck "$WORK/ck/home" \
  "_DEPLOY_REGISTRY_LOCK_TRIES=2; export _DEPLOY_REGISTRY_LOCK_TRIES; set -euo pipefail; record_deployment '$WORK/ck/a' local late - - /late c.yml") || lock_rc=$?
if [ "$lock_rc" -eq 0 ] && ! grep -qF 'local|late|/late' "$shared" &&
   printf '%s\n' "$lock_out" | grep -q "LIVE but UNLISTED"; then
  ok "a record under a stuck lock is refused loudly, without failing the deploy"
else
  ko "a stuck lock was ignored (rc=$lock_rc) or misreported: $lock_out"
fi
lock_rc=0; lock_out=$(in_ck "$WORK/ck/home" \
  "_DEPLOY_REGISTRY_LOCK_TRIES=2; export _DEPLOY_REGISTRY_LOCK_TRIES; set -euo pipefail; forget_deployment '$WORK/ck/a' 'local|base|/base'") || lock_rc=$?
if [ "$lock_rc" -ne 0 ] && grep -qF 'local|base|/base' "$shared"; then
  ok "a forget under a stuck lock reports failure and leaves the entry listed"
else
  ko "a stuck lock let a forget claim success: rc=$lock_rc"
fi
rmdir "$shared.lock"
rm -rf "$WORK/ck"

# …and the union itself must survive every shell that sources this lib: the
# two-file join used a bashism ($'\n') that dash expands to five literal
# characters, gluing the files' last and first rows into one corrupted line.
# Every lib-level test above runs under bash, so this runs the lib under dash
# with BOTH files populated — exactly the shape that was broken.
mkdir -p "$WORK/dashhome/.family-cfo"
printf 'remote|pp|/srv/pp\tremote\tpp\t-\t-\t/srv/pp\tc.yml\t2026-01-01T00:00:00Z\n' \
  > "$WORK/dashhome/.family-cfo/.deploy.history"
rm -f "$WORK/cli/.deploy.history"
printf 'remote|qq|/srv/qq\tremote\tqq\t-\t-\t/srv/qq\tc.yml\t2026-02-02T00:00:00Z\n' \
  > "$WORK/cli/.deploy.history"
dash_rows=$(HOME="$WORK/dashhome" dash -c "
  unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
  . '$REPO_ROOT/scripts/lib/deploy-env.sh'
  deploy_registry_rows '$WORK/cli'
" 2>/dev/null)
if [ "$(printf '%s\n' "$dash_rows" | grep -c .)" = "2" ] &&
   printf '%s\n' "$dash_rows" | awk -F'\t' 'NF != 8 { bad = 1 } END { exit bad }' &&
   printf '%s\n' "$dash_rows" | grep -qF 'remote|pp|/srv/pp' &&
   printf '%s\n' "$dash_rows" | grep -qF 'remote|qq|/srv/qq'; then
  ok "the two-file union reads correctly under dash: two rows, all fields intact"
else
  ko "the union is corrupted under dash: $(printf '%s' "$dash_rows" | head -c 120)"
fi
rm -f "$WORK/cli/.deploy.history" "$WORK/dashhome/.family-cfo/.deploy.history"

# --- Round 5: failures after the lock, and order-independent suppression -----
# Three bugs found by review on a744a18, all reproduced first:
#   1. [P1] The install pipeline ran as a BARE command. This lib only ever runs
#      under callers' `set -euo pipefail`, so a failed install exited the shell
#      before the warning, before `installed=$?` and before the unlock —
#      aborting a deploy that had already succeeded and leaving
#      `.deploy.history.lock` behind, which blocked every later write.
#   2. [P1] The reader checks mktemp but ran the write that POPULATES the file
#      list unguarded — and since the reader only ever runs inside a
#      conditional, errexit is OFF there: the failure continued with an empty
#      list, read zero paths, reported SUCCESS, and the writer replaced the
#      whole registry with just its new row.
#   3. [P2] Both projections applied `$1 in forgotten` as rows STREAMED, so a
#      live row read before its marker was collected and emitted anyway. The
#      supported checkout-local fallback hits this for real: forget with HOME
#      unset writes the marker to the checkout file, and the shared registry —
#      read FIRST — holds the live row.
#
# The failures are injected at the exact step (a stubbed installer that fails
# after a real lock acquisition; a stubbed mktemp / printf / deploy_history_files
# with the filesystem left writable), never via permissions. Each assertion
# covers the exit status, the registry contents, and whether the lock survived.
# `plain_shell` runs its snippet verbatim in a fresh `set -euo pipefail` shell —
# record is invoked as a PLAIN COMMAND there, because calling it from `if` or
# `||` stands errexit down and hides the bare-pipeline bug.
plain_shell() { # <home> <snippet> → output; the snippet's shell status is the rc
  HOME="$1" bash -c "
    set -euo pipefail
    unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
    . '$REPO_ROOT/scripts/lib/deploy-env.sh'
    $2
  " 2>&1
}
reg_row() { # <file> <key> <kind> — one synthetic registry row
  printf '%s\t%s\t%s\t-\t-\t/dir\tc.yml\t2026-01-01T00:00:00Z\n' "$2" "$3" "$(printf '%s' "$2" | cut -d'|' -f2)"
}

# 1. Install failure AFTER the lock: record warns, returns success (the deploy
#    is already live), the previous file is intact, the lock is released.
rm -rf "$WORK/r5"; mkdir -p "$WORK/r5/home/.family-cfo" "$WORK/r5/repo"
reg_row x 'local|keep|/keep' local > "$WORK/r5/home/.family-cfo/.deploy.history"
t1_rc=0; t1_out=$(plain_shell "$WORK/r5/home" "
  _install_registry_file() { return 1; }
  record_deployment '$WORK/r5/repo' local fresh - - /fresh c.yml
  echo RECORD-RETURNED
") || t1_rc=$?
if [ "$t1_rc" -eq 0 ] && printf '%s\n' "$t1_out" | grep -q RECORD-RETURNED &&
   grep -qF 'local|keep|/keep' "$WORK/r5/home/.family-cfo/.deploy.history" &&
   ! grep -qF 'local|fresh|/fresh' "$WORK/r5/home/.family-cfo/.deploy.history" &&
   [ ! -d "$WORK/r5/home/.family-cfo/.deploy.history.lock" ] &&
   printf '%s\n' "$t1_out" | grep -q "deployment NOT recorded"; then
  ok "an install failure after the lock warns, keeps the old file, returns success, releases the lock"
else
  ko "an install failure aborted the deploy, replaced the file, or leaked the lock (rc=$t1_rc): $t1_out"
fi
# …and forget under the same failure reports FAILURE — its whole answer is
# whether the entry is gone — and still releases the lock.
t1f_rc=0; t1f_out=$(plain_shell "$WORK/r5/home" "
  _install_registry_file() { return 1; }
  forget_deployment '$WORK/r5/repo' 'local|keep|/keep'
") || t1f_rc=$?
if [ "$t1f_rc" -ne 0 ] &&
   grep -qF 'local|keep|/keep' "$WORK/r5/home/.family-cfo/.deploy.history" &&
   [ ! -d "$WORK/r5/home/.family-cfo/.deploy.history.lock" ] &&
   printf '%s\n' "$t1f_out" | grep -q "could not update"; then
  ok "a forget whose install fails reports failure and releases the lock"
else
  ko "a failing forget claimed success, lost the file, or leaked the lock (rc=$t1f_rc): $t1f_out"
fi

# 2. A failure while building the file list is an INCOMPLETE read: record
#    refuses (LIVE but UNLISTED), forget reports failure, the registry is never
#    replaced with empty or partial data, the lock is released. Injected at all
#    three steps — mktemp (creating), printf (populating), deploy_history_files
#    (capturing) — with the filesystem left writable throughout.
for inj in 'mktemp() { return 1; }' \
           "printf() { case \"\$1\" in '%s\\n') return 1 ;; *) command printf \"\$@\" ;; esac; }" \
           'deploy_history_files() { return 1; }'; do
  case "$inj" in
    mktemp*)               inj_name=mktemp ;;
    printf*)               inj_name=printf ;;
    deploy_history_files*) inj_name=deploy_history_files ;;
  esac
  reg_row x 'local|keep|/keep' local > "$WORK/r5/home/.family-cfo/.deploy.history"
  t2_rc=0; t2_out=$(plain_shell "$WORK/r5/home" "
    $inj
    record_deployment '$WORK/r5/repo' local fresh - - /fresh c.yml
  ") || t2_rc=$?
  if [ "$t2_rc" -eq 0 ] &&
     grep -qF 'local|keep|/keep' "$WORK/r5/home/.family-cfo/.deploy.history" &&
     ! grep -qF 'local|fresh|/fresh' "$WORK/r5/home/.family-cfo/.deploy.history" &&
     [ ! -d "$WORK/r5/home/.family-cfo/.deploy.history.lock" ] &&
     printf '%s\n' "$t2_out" | grep -q "could not be read in full"; then
    ok "a registry-file-list failure ($inj_name): record refuses, file intact, lock released"
  else
    ko "a list failure let record replace the registry or leak the lock (rc=$t2_rc): $t2_out"
  fi
  t2f_rc=0; t2f_out=$(plain_shell "$WORK/r5/home" "
    $inj
    forget_deployment '$WORK/r5/repo' 'local|keep|/keep'
  ") || t2f_rc=$?
  if [ "$t2f_rc" -ne 0 ] &&
     grep -qF 'local|keep|/keep' "$WORK/r5/home/.family-cfo/.deploy.history" &&
     [ ! -d "$WORK/r5/home/.family-cfo/.deploy.history.lock" ]; then
    ok "…and forget reports failure, file intact, lock released"
  else
    ko "a list failure let forget claim success or leak the lock (rc=$t2f_rc): $t2f_out"
  fi
done
# A GENUINELY empty inventory is not a failure: with no registry files at all,
# the read succeeds, the record writes, and the row is there.
rm -f "$WORK/r5/home/.family-cfo/.deploy.history"
t2e_rc=0; t2e_out=$(plain_shell "$WORK/r5/home" "
  rows=\$(deploy_registry_rows '$WORK/r5/repo') || exit 9
  [ -z \"\$rows\" ] || exit 1
  record_deployment '$WORK/r5/repo' local fresh - - /fresh c.yml
  grep -qF 'local|fresh|/fresh' '$WORK/r5/home/.family-cfo/.deploy.history'
") || t2e_rc=$?
if [ "$t2e_rc" -eq 0 ]; then
  ok "an empty inventory reads as complete, and recording into it works"
else
  ko "an empty inventory was treated as incomplete or failed to record (rc=$t2e_rc): $t2e_out"
fi

# The real enumerator must carry failures from its dependencies and emissions;
# replacing the whole helper above only checks the reader's outer guard. Run
# these under dash too: errexit is not the contract for a conditional caller.
registry_shell() { # <snippet> — reg_shell selects the shell under test
  HOME="$WORK/r5/home" "$reg_shell" -c "
    set -eu
    [ '$reg_shell' != bash ] || set -o pipefail
    unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
    . '$REPO_ROOT/scripts/lib/deploy-env.sh'
    $1
  " 2>&1
}
for reg_shell in bash dash; do
  dep_rc=0; dep_out=$(registry_shell "
    deploy_history_file() { return 1; }
    if deploy_history_files '$WORK/r5/repo'; then exit 9; fi
    if _deploy_registry_read '$WORK/r5/repo'; then exit 9; fi
  ") || dep_rc=$?
  if [ "$dep_rc" -eq 0 ] && printf '%s\n' "$dep_out" | grep -q 'cannot enumerate'; then
    ok "a failed path resolution propagates through the real enumerator ($reg_shell)"
  else
    ko "a failed path resolution became a complete read ($reg_shell, rc=$dep_rc): $dep_out"
  fi
  for failure in emission-shared emission-legacy emission-explicit reopen; do
    shared="$WORK/r5/home/.family-cfo/.deploy.history"
    legacy="$WORK/r5/repo/.deploy.history"
    list_file="$WORK/r5/file-list"
    write_seen="$WORK/r5/write-succeeded"
    reg_row x 'local|keep|/keep' local > "$shared"
    reg_row x 'local|legacy|/legacy' local > "$legacy"
    cp "$shared" "$WORK/r5/shared-before"
    cp "$legacy" "$WORK/r5/legacy-before"
    fail_path="$shared"; select_file=""
    case "$failure" in
      emission-legacy) fail_path="$legacy" ;;
      emission-explicit) select_file="export DEPLOY_HISTORY_FILE='$shared'" ;;
    esac
    inj="
      $select_file
      printf() {
        if [ \"\$1\" = '%s\n' ] && [ \"\${2:-}\" = '$fail_path' ]; then return 1; fi
        command printf \"\$@\"
      }
    "
    if [ "$failure" = reopen ]; then
      # The write really succeeds before unlinking its still-open output.
      # No permissions assumption or production hook is needed to hit the gap
      # between that successful write and the loop opening the same pathname.
      inj="
        mktemp() {
          case \"\$1\" in
            */deploy-history-files.XXXXXX)
              : > '$list_file'
              command printf '%s\n' '$list_file'
              ;;
            *) command mktemp \"\$@\" ;;
          esac
        }
        printf() {
          command printf \"\$@\" || return 1
          if [ \"\$1\" = '%s\n' ] && [ -f '$list_file' ]; then
            rm -f '$list_file' || return 1
            : > '$write_seen'
          fi
          return 0
        }
      "
    fi
    # Check the reader's status itself as well as both rewriting callers:
    # record is deliberately non-fatal, so its status alone cannot prove safety.
    for operation in read record forget; do
      cp "$WORK/r5/shared-before" "$shared"
      cp "$WORK/r5/legacy-before" "$legacy"
      rm -f "$legacy.migrated" "$list_file" "$write_seen"
      case "$operation" in
        read) action="if _deploy_registry_read '$WORK/r5/repo'; then exit 9; fi" ;;
        record) action="record_deployment '$WORK/r5/repo' local fresh - - /fresh c.yml" ;;
        forget) action="if forget_deployment '$WORK/r5/repo' 'local|keep|/keep'; then exit 9; fi" ;;
      esac
      fail_rc=0; fail_out=$(registry_shell "$inj
        $action") || fail_rc=$?
      if [ "$fail_rc" -eq 0 ] &&
         cmp -s "$shared" "$WORK/r5/shared-before" &&
         cmp -s "$legacy" "$WORK/r5/legacy-before" &&
         [ ! -d "$shared.lock" ] &&
         { [ "$failure" != reopen ] || [ -f "$write_seen" ]; } &&
         { [ "$operation" != record ] || printf '%s\n' "$fail_out" | grep -q 'could not be read in full'; }; then
        ok "$failure: $operation refuses an incomplete read, preserves both files, releases the lock ($reg_shell)"
      else
        ko "$failure: $operation accepted an incomplete read or changed inventory ($reg_shell, rc=$fail_rc): $fail_out"
      fi
    done
  done
done
rm -f "$WORK/r5/repo/.deploy.history"

# 3. A marker suppresses its key regardless of ORDER — live row first or marker
#    first, in one file or across the shared/checkout pair — in list output AND
#    in the next rewrite; re-recording the place makes it visible again.
# 3a. The reviewer's flow through the supported checkout-local fallback:
#     record with HOME set (shared), forget with HOME unset (marker lands in
#     the checkout file, read SECOND), then list with HOME restored.
mkdir -p "$WORK/r5/fb/home/.family-cfo" "$WORK/r5/fb/ck"
in_fallback() { # <home> <unset-home?> <snippet>
  if [ "$2" = no-home ]; then
    env -u HOME -u FAMILY_CFO_HOME bash -c "
      set -euo pipefail
      unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
      . '$REPO_ROOT/scripts/lib/deploy-env.sh'
      $3
    " 2>&1
  else
    plain_shell "$1" "$3"
  fi
}
in_fallback "$WORK/r5/fb/home" home "record_deployment '$WORK/r5/fb/ck' local gone - - /gone c.yml" >/dev/null
in_fallback "$WORK/r5/fb/home" no-home "forget_deployment '$WORK/r5/fb/ck' 'local|gone|/gone'" >/dev/null
if grep -qF "$(printf 'local|gone|/gone\tforgotten\t')" "$WORK/r5/fb/ck/.deploy.history" &&
   ! plain_shell "$WORK/r5/fb/home" "deploy_registry_rows '$WORK/r5/fb/ck'" | grep -qF 'local|gone|/gone'; then
  ok "a marker in the checkout file suppresses the shared row read before it"
else
  ko "suppression depends on row order: the marker came too late for the live row"
fi
# The NEXT rewrite (recording y from that checkout) must keep the suppression.
in_fallback "$WORK/r5/fb/home" home "record_deployment '$WORK/r5/fb/ck' local y - - /y c.yml" >/dev/null
if ! plain_shell "$WORK/r5/fb/home" "deploy_registry_rows '$WORK/r5/fb/ck'" | grep -qF 'local|gone|/gone'; then
  ok "…and the next rewrite keeps the forgotten key suppressed"
else
  ko "a rewrite resurrected the forgotten key"
fi
# Re-recording the place makes it visible again, in both files' terms.
in_fallback "$WORK/r5/fb/home" home "record_deployment '$WORK/r5/fb/ck' local gone - - /gone c.yml" >/dev/null
if plain_shell "$WORK/r5/fb/home" "deploy_registry_rows '$WORK/r5/fb/ck'" | grep -qF 'local|gone|/gone' &&
   ! grep -qF "$(printf 'local|gone|/gone\tforgotten\t')" "$WORK/r5/fb/ck/.deploy.history"; then
  ok "re-recording the forgotten place removes the marker and lists it again"
else
  ko "the place stayed forgotten after being deployed again"
fi
# 3b. Both orders WITHIN one file, and the rewrite's contents: no live copy of
#     a marked key may survive a rewrite, whichever order it was read in.
for order in live-first marker-first; do
  rm -f "$WORK/r5/fb/ck/.deploy.history" "$WORK/r5/fb/home/.family-cfo/.deploy.history"
  if [ "$order" = live-first ]; then
    reg_row x 'local|x|/d' local > "$WORK/r5/fb/home/.family-cfo/.deploy.history"
    printf 'local|x|/d\tforgotten\t-\t-\t-\t-\t-\t2026-02-02T00:00:00Z\n' >> "$WORK/r5/fb/home/.family-cfo/.deploy.history"
  else
    printf 'local|x|/d\tforgotten\t-\t-\t-\t-\t-\t2026-02-02T00:00:00Z\n' > "$WORK/r5/fb/home/.family-cfo/.deploy.history"
    reg_row x 'local|x|/d' local >> "$WORK/r5/fb/home/.family-cfo/.deploy.history"
  fi
  if plain_shell "$WORK/r5/fb/home" "deploy_registry_rows '$WORK/r5/fb/ck'" | grep -qF 'local|x|/d'; then
    ko "suppression failed with the $order layout"
  else
    ok "the marker suppresses its live row in the $order layout"
  fi
  plain_shell "$WORK/r5/fb/home" "record_deployment '$WORK/r5/fb/ck' local y - - /y c.yml" >/dev/null
  if grep -qF "$(printf 'local|x|/d\tlocal\t')" "$WORK/r5/fb/home/.family-cfo/.deploy.history"; then
    ko "the $order rewrite installed a live copy of the marked key"
  else
    ok "…and the rewrite installs no live copy of it ($order)"
  fi
done
rm -rf "$WORK/r5"

# Owner-only, like the .deploy.env beside it: hostnames, users and paths.
# Recorded through the lib, not printf'd in — the mode is the point, and it comes
# from mktemp surviving the mv. `ls -l`'s first field is portable; `stat`'s
# format flags are not. Strip the macOS '@'/'+' suffix for xattrs and ACLs.
rm -f "$WORK/home/.family-cfo/.deploy.history" "$WORK/repo/.deploy.history"
in_lib 'set -euo pipefail; record_deployment "$SANDBOX_REPO" remote modecheck me 22 /srv/m c.yml' \
  >/dev/null 2>&1
if [ -f "$WORK/home/.family-cfo/.deploy.history" ] &&
   [ "$(ls -l "$WORK/home/.family-cfo/.deploy.history" | awk '{print $1}' | tr -d '@+.' )" = "-rw-------" ]; then
  ok "the registry is written 0600"
else
  ko "the registry is not owner-only"
fi

# An unreadable registry must be LEFT ALONE. The read feeds the write, so a
# failed read that is not checked would install an EMPTY registry — "forget
# everything" masquerading as "forget one".
printf 'remote|keepme|/srv/k\tremote\tkeepme\t-\t-\t/srv/k\ta.yml\t2026-01-01T00:00:00Z\n' \
  > "$WORK/home/.family-cfo/.deploy.history"
chmod 000 "$WORK/home/.family-cfo/.deploy.history"
in_lib 'forget_deployment "$SANDBOX_REPO" "remote|keepme|/srv/k"' 2>/dev/null || true
chmod 600 "$WORK/home/.family-cfo/.deploy.history"
if grep -q keepme "$WORK/home/.family-cfo/.deploy.history"; then
  ok "an unreadable registry is left alone instead of being overwritten with nothing"
else
  ko "an unreadable registry was replaced by an empty one"
fi

# No HOME at all: fall back to the checkout rather than try to write at /.
if FAMILY_CFO_HOME_TEST='' bash -c '
      unset HOME
      . "'"$REPO_ROOT"'/scripts/lib/deploy-env.sh"
      [ "$(deploy_history_file /some/checkout)" = "/some/checkout/.deploy.history" ]
    '; then
  ok "with no HOME the registry falls back to the checkout, not /"
else
  ko "with no HOME the registry resolves outside the checkout"
fi

# FAMILY_CFO_HOME is the operator's (and the tests') override.
if bash -c '
      export HOME="'"$WORK"'/home" FAMILY_CFO_HOME="'"$WORK"'/custom"
      . "'"$REPO_ROOT"'/scripts/lib/deploy-env.sh"
      [ "$(deploy_history_file x)" = "'"$WORK"'/custom/.deploy.history" ]
    '; then
  ok "FAMILY_CFO_HOME overrides the base directory"
else
  ko "FAMILY_CFO_HOME is not honoured"
fi

# --- .deploy.env -------------------------------------------------------------
# ONE file answers for the destination — the first that exists — and a real
# environment variable outranks it. Not a per-key merge: a shared file that
# names only a host must not pick up the stale checkout's user, port and stack
# path and send a patch at a Frankenstein box. And a checkout-local file that
# has not moved yet must keep working on its own.
mkdir -p "$WORK/home/.family-cfo" "$WORK/both"
printf 'SSH_HOST=from-checkout\nSSH_USER=u1\nSSH_KEY=\n' > "$WORK/both/.deploy.env"
printf 'SSH_HOST=from-shared\nSSH_PORT=2222\n' > "$WORK/home/.family-cfo/.deploy.env"
env_probe() { # shell-prelude → var=value lines on stdout
  HOME="$WORK/home" bash -c "
    unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
    . '$REPO_ROOT/scripts/lib/deploy-env.sh'
    $1
    load_deploy_env '$WORK/both' >/dev/null 2>&1 || exit 1
    printf 'SSH_HOST=%s\nSSH_USER=%s\nSSH_PORT=%s\nREMOTE_DIR=%s\nSSH_KEY_SET=%s\n' \
      \"\${SSH_HOST-}\" \"\${SSH_USER-}\" \"\${SSH_PORT-}\" \"\${REMOTE_DIR-}\" \"\${SSH_KEY+yes}\"
  "
}
got="$(env_probe 'export SSH_USER=from-env')"
[ "$(printf '%s\n' "$got" | grep '^SSH_HOST=')" = "SSH_HOST=from-shared" ] \
  && ok "the machine-wide .deploy.env is the destination when it exists" \
  || ko "the checkout's .deploy.env still wins (got: $(printf '%s\n' "$got" | grep '^SSH_HOST='))"
[ "$(printf '%s\n' "$got" | grep '^SSH_USER=')" = "SSH_USER=from-env" ] \
  && ok "a real environment variable still outranks the file" \
  || ko "a file value overrode an explicit environment variable"
# The whole point of picking one file: keys that exist ONLY in the shadowed
# checkout file stay unset rather than riding along with the new host.
printf 'SSH_HOST=new-box\n' > "$WORK/home/.family-cfo/.deploy.env"
printf 'SSH_HOST=old-box\nSSH_USER=old-user\nSSH_PORT=2222\nREMOTE_DIR=/srv/old-stack\n' \
  > "$WORK/both/.deploy.env"
got="$(env_probe 'unset SSH_USER')"
[ "$(printf '%s\n' "$got" | grep '^SSH_HOST=')" = "SSH_HOST=new-box" ] &&
  [ "$(printf '%s\n' "$got" | grep '^SSH_USER=')" = "SSH_USER=" ] &&
  [ "$(printf '%s\n' "$got" | grep '^SSH_PORT=')" = "SSH_PORT=" ] &&
  [ "$(printf '%s\n' "$got" | grep '^REMOTE_DIR=')" = "REMOTE_DIR=" ] \
  && ok "a half-migrated shared file does not inherit the old checkout's user, port and path" \
  || ko "the destination was merged across both files: $(printf '%s\n' "$got" | tr '\n' ' ')"
# Two files on disk, one of them ignored, must be SAID — not silently shadowed.
if HOME="$WORK/home" bash -c "
     unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
     . '$REPO_ROOT/scripts/lib/deploy-env.sh'
     unset SSH_USER SSH_PORT REMOTE_DIR
     load_deploy_env '$WORK/both' 2>&1 >/dev/null
   " | grep -q 'Ignoring'; then
  ok "the shadowed checkout .deploy.env is reported, not quietly ignored"
else
  ko "the shadowed checkout .deploy.env was ignored in silence"
fi

# With no shared file at all, the pre-move checkout file is the whole answer —
# upgrading must not lose a destination nobody moved. Including a blank value:
# SSH_KEY may be deliberately empty, so "set" is not "non-empty".
rm -f "$WORK/home/.family-cfo/.deploy.env"
printf 'SSH_HOST=only-checkout\nSSH_USER=u1\nSSH_KEY=\n' > "$WORK/both/.deploy.env"
got="$(env_probe 'unset SSH_USER')"
[ "$(printf '%s\n' "$got" | grep '^SSH_HOST=')" = "SSH_HOST=only-checkout" ] &&
  [ "$(printf '%s\n' "$got" | grep '^SSH_USER=')" = "SSH_USER=u1" ] \
  && ok "a pre-move checkout-local .deploy.env still sets the destination on its own" \
  || ko "upgrading silently lost a checkout-local destination: $(printf '%s\n' "$got" | tr '\n' ' ')"
[ "$(printf '%s\n' "$got" | grep '^SSH_KEY_SET=')" = "SSH_KEY_SET=yes" ] \
  && ok "a blank value in .deploy.env still counts as set" \
  || ko "a blank SSH_KEY was treated as unset"

# --- Where the destination file IS -------------------------------------------
# setup-ssh.sh WRITES the destination and load_deploy_env READS it. If the two
# disagree, onboarding produces a file nothing loads and the first patch goes
# somewhere else — so both ask the same function, and this pins its answers.
env_where() { # <prelude setting up the files> → the chosen path
  HOME="$WORK/home" bash -c "
    unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
    . '$REPO_ROOT/scripts/lib/deploy-env.sh'
    $1
    deploy_env_file '$WORK/both'
  "
}
rm -rf "$WORK/home/.family-cfo" "$WORK/both"; mkdir -p "$WORK/home/.family-cfo" "$WORK/both"
[ "$(env_where '')" = "$WORK/home/.family-cfo/.deploy.env" ] \
  && ok "with neither file present, the destination to create is the machine-wide one" \
  || ko "a fresh machine would be pointed at $(env_where '')"
printf 'SSH_HOST=c\n' > "$WORK/both/.deploy.env"
[ "$(env_where '')" = "$WORK/both/.deploy.env" ] \
  && ok "with only the checkout file, that file is the destination" \
  || ko "a pre-move checkout file is not recognised as the destination"
printf 'SSH_HOST=s\n' > "$WORK/home/.family-cfo/.deploy.env"
[ "$(env_where '')" = "$WORK/home/.family-cfo/.deploy.env" ] \
  && ok "with both, the shared file is the destination (one source, no merge)" \
  || ko "both files exist and the shared one did not win"
[ "$(env_where 'DEPLOY_ENV_FILE=/tmp/explicit.env')" = "/tmp/explicit.env" ] \
  && ok "DEPLOY_ENV_FILE outranks both conventions" \
  || ko "DEPLOY_ENV_FILE was not honoured"

# Structural, in the same spirit as the doctor.sh checks: if setup-ssh.sh ever
# goes back to a hardcoded checkout path, onboarding silently writes a file the
# loader ignores.
if grep -q 'deploy_env_file' "$REPO_ROOT/scripts/setup-ssh.sh" &&
   ! grep -qE '^DEPLOY_ENV="\$REPO_ROOT/\.deploy\.env"' "$REPO_ROOT/scripts/setup-ssh.sh"; then
  ok "setup-ssh.sh asks the library where the destination lives"
else
  ko "setup-ssh.sh has a hardcoded destination again — it can write a file nothing reads"
fi
# And it must not clobber the rest of a shared config (Apple team ids, ASC key
# ids, IOS_DEVICE) when it updates the keys it owns. Two shapes are pinned:
# the OWNED_KEYS list is what says which keys are an address, and the key
# comparison goes through the loader's rule (deploy_env_key_of_line), not a
# literal grep — the loader strips comments and all whitespace from a key and
# keeps the FIRST value, so a padded ` SSH_HOST = old ` left in place would keep
# winning over the line setup appends.
if grep -q 'OWNED_KEYS="SSH_HOST SSH_USER SSH_PORT SSH_KEY REMOTE_DIR"' "$REPO_ROOT/scripts/setup-ssh.sh" &&
   grep -q 'deploy_env_key_of_line "$line"' "$REPO_ROOT/scripts/setup-ssh.sh" &&
   ! grep -qE "awk -F= .*SSH_HOST|grep -qE \"\\^SSH_HOST=" "$REPO_ROOT/scripts/setup-ssh.sh"; then
  ok "setup-ssh.sh replaces exactly its OWNED_KEYS, matched by the loader's key rule"
else
  ko "setup-ssh.sh is matching keys by literal text again — padded ' SSH_HOST = old ' would survive and keep winning"
fi

# Run that block for real, against a shared config holding settings it must not
# touch. Extracted rather than imitated: a test that re-implements the script's
# logic proves nothing about the script.
#
# Values go in as positional parameters, not `VAR=x func`: an assignment before a
# shell *function* stays in the calling shell after it returns (POSIX), so
# `HOME=… func` would leak HOME into every assertion that follows this one.
setup_block=$(awk '/^# --- 6\. \.deploy\.env/{on=1} on{print} on && /^# --- 7\./{exit}' \
  "$REPO_ROOT/scripts/setup-ssh.sh" | sed '$d')
run_setup_block() { # <home> <repo-root> <alias> <remote-dir> <deploy-env-file> [prelude]
  # `chmod 000` cannot express "the read fails": CI runners are root, and root
  # reads anything, so a permission-based version of this test would pass on a
  # laptop and fail on the runner. Patch the seam instead — the repo's rule for
  # selecting a fallback path explicitly. The <prelude> is injected verbatim into
  # the subshell, so a caller can replace `awk` or `printf` with a failing one.
  #
  # The lib is sourced because the block now asks the LOADER for the effective
  # destination (effective_value → load_deploy_env) and normalises keys with
  # deploy_env_key_of_line — both of which are the point of this round's fix.
  HOME="$1" REPO_ROOT="$2" ALIAS="$3" REMOTE_DIR="$4" DEPLOY_ENV="$5" bash -c "
    set -euo pipefail
    unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
    . '$REPO_ROOT/scripts/lib/deploy-env.sh'
    ${6:-}
    ok(){ :; }; warn(){ printf 'W|%s\n' \"\$*\"; }; die(){ printf 'E|%s\n' \"\$*\" >&2; exit 1; }; step(){ :; }
    $setup_block
  " 2>&1
}
cat_fails='cat() { case "$*" in *.deploy.env*) return 1 ;; *) command cat "$@" ;; esac; };'
printf_fails='printf() { case "$1" in REMOTE_DIR=*) return 1 ;; *) command printf "$@" ;; esac; };'
if [ -z "$setup_block" ]; then
  ko "could not find setup-ssh.sh's destination block to test it"
else
  # A checkout that still has its own .deploy.env, so the shadow notice is on a
  # real path rather than an imagined one.
  mkdir -p "$WORK/setup/.family-cfo" "$WORK/setup-repo"
  printf 'SSH_HOST=old-checkout-box\n' > "$WORK/setup-repo/.deploy.env"
  cat > "$WORK/setup/.family-cfo/.deploy.env" <<'FIXTURE'
# my own notes, keep these
IOS_TEAM_ID=ABCDE12345
SSH_HOST=stale-box
ASC_KEY_ID=KEEPME
SSH_USER=whoever
FIXTURE
  out=$(run_setup_block "$WORK/setup" "$WORK/setup-repo" boxC '~/srv/c' \
                       "$WORK/setup/.family-cfo/.deploy.env") || true
  sed -n '1,20p' "$WORK/setup/.family-cfo/.deploy.env" > "$WORK/setup/after"
  if printf '%s\n' "$out" | grep -q '^E|'; then
    ko "setup-ssh.sh's destination block failed to run: $out"
  elif grep -q '^SSH_HOST=boxC$' "$WORK/setup/after" &&
     grep -q '^REMOTE_DIR=~/srv/c$' "$WORK/setup/after" &&
     grep -q '^IOS_TEAM_ID=ABCDE12345$' "$WORK/setup/after" &&
     grep -q '^ASC_KEY_ID=KEEPME$' "$WORK/setup/after" &&
     ! grep -q '^SSH_USER=' "$WORK/setup/after" &&
     grep -q 'my own notes' "$WORK/setup/after" &&
     ! grep -q 'stale-box' "$WORK/setup/after"; then
    ok "setup-ssh.sh repoints SSH_HOST/REMOTE_DIR and leaves every other setting alone"
  else
    ko "setup-ssh.sh damaged the shared destination file: $(tr '\n' '|' < "$WORK/setup/after")"
  fi
  # The whole point of the fix: the operator must be able to tell WHICH file the
  # scripts will read, and that a second one is being ignored.
  if printf '%s\n' "$out" | grep -q 'NOT read while' &&
     printf '%s\n' "$out" | grep -q "$WORK/setup-repo/.deploy.env"; then
    ok "setup-ssh.sh names the file it wrote and flags the shadowed checkout one"
  else
    ko "setup-ssh.sh wrote one file in silence while another .deploy.env sat unread"
  fi
  # Running it twice must be a no-op, not a duplicate-append.
  n_before=$(grep -c '^SSH_HOST=' "$WORK/setup/.family-cfo/.deploy.env")
  run_setup_block "$WORK/setup" "$WORK/setup-repo" boxC '~/srv/c' \
                  "$WORK/setup/.family-cfo/.deploy.env" >/dev/null || true
  n_after=$(grep -c '^SSH_HOST=' "$WORK/setup/.family-cfo/.deploy.env")
  [ "$n_before" = "$n_after" ] && [ "$n_after" = "1" ] \
    && ok "running setup-ssh.sh again leaves one SSH_HOST, not a stack of them" \
    || ko "re-running setup-ssh.sh duplicated SSH_HOST ($n_before → $n_after)"
  # A fresh machine has neither file: this is the onboarding path, and it is the
  # one the first draft of this block aborted — `[ -f "$DEPLOY_ENV" ] && awk …` as
  # a standalone statement is a failing command under `set -e` when the file does
  # not exist yet, so the script died before writing anything.
  rm -rf "$WORK/setup/.family-cfo" "$WORK/setup-repo"; mkdir -p "$WORK/setup-repo"
  out=$(run_setup_block "$WORK/setup" "$WORK/setup-repo" boxD '~/srv/d' \
                       "$WORK/setup/.family-cfo/.deploy.env") || true
  if printf '%s\n' "$out" | grep -q '^E|'; then
    ko "setup-ssh.sh could not create the destination on a fresh machine: $out"
  elif [ "$(ls -l "$WORK/setup/.family-cfo/.deploy.env" | awk '{print $1}' | tr -d '@+.')" = "-rw-------" ] &&
       grep -q '^SSH_HOST=boxD$' "$WORK/setup/.family-cfo/.deploy.env" &&
       grep -q '^REMOTE_DIR=~/srv/d$' "$WORK/setup/.family-cfo/.deploy.env" &&
       grep -q 'No TARGET on purpose' "$WORK/setup/.family-cfo/.deploy.env"; then
    ok "the destination it creates is 0600, documented, and holds the full address"
  else
    ko "the destination setup-ssh.sh creates is wrong or world-readable"
  fi
  # And a file it cannot READ must not be replaced by just the two lines this
  # script owns — the same rule as the registry's, because a writer cannot tell
  # "nothing in there" from "the read died". The failure is injected at the seam.
  out=$(run_setup_block "$WORK/setup" "$WORK/setup-repo" boxE '~/srv/e' \
                       "$WORK/setup/.family-cfo/.deploy.env" "$cat_fails") || true
  dest="$WORK/setup/.family-cfo/.deploy.env"
  if printf '%s\n' "$out" | grep -q '^E|' && grep -q '^SSH_HOST=boxD$' "$dest" &&
     ! grep -q 'boxE' "$dest"; then
    ok "setup-ssh.sh refuses to rewrite a destination it could not read, and leaves it intact"
  else
    ko "setup-ssh.sh flattened a destination file whose read failed (out: $(printf '%s' "$out" | head -c 120))"
  fi
  # A destination must never be installed half-written. Failing the LAST line of
  # the write is the case a brace group can hide: the group's status is the last
  # command's, so without the check on it a truncated file would be moved into
  # place and reported as success.
  out=$(run_setup_block "$WORK/setup" "$WORK/setup-repo" boxF '~/srv/f' "$dest" \
                       "$printf_fails") || true
  if printf '%s\n' "$out" | grep -q '^E|' && grep -q '^SSH_HOST=boxD$' "$dest" &&
     ! grep -q 'boxF' "$dest" && ! ls "$dest".?????? >/dev/null 2>&1; then
    ok "a destination whose write fails halfway is not installed, and no temp file is left behind"
  else
    ko "a half-failed destination write was installed anyway (out: $(printf '%s' "$out" | head -c 120))"
  fi

  # …and what it WROTE is not taken on trust either: after the write the script
  # asks the LOADER what it now resolves, and refuses to claim success if the
  # answer is not the box it was told. The seam makes the loader answer nothing —
  # patching `effective_value` itself would not work: the script's own definition
  # is inside the extracted block and would overwrite the stub.
  out=$(run_setup_block "$WORK/setup" "$WORK/setup-repo" boxG '~/srv/g' "$dest" \
                       'load_deploy_env() { return 0; };') || true
  if printf '%s\n' "$out" | grep -q '^E|' && grep -q '^SSH_HOST=boxG$' "$dest"; then
    ok "setup refuses to claim success when the loader resolves something other than what it wrote"
  else
    ko "setup reported a destination the loader does not actually resolve (out: $(printf '%s' "$out" | head -c 120))"
  fi

  # --- Repointing must not carry the OLD box's login onto the new host ---------
  # SSH_USER/SSH_PORT/SSH_KEY describe how ssh reached the previous box. Keeping
  # them while replacing SSH_HOST produces, BY SETUP, the `old-user@new-box:2222`
  # destination that one-source loading exists to prevent — the worst kind of
  # bug, the one the onboarding script itself installs. What is NOT an address
  # (Apple team ids, App Store Connect key ids, IOS_DEVICE) must survive.
  effective_of() { # <home> <repo-root> <KEY> → what load_deploy_env resolves for it
    HOME="$1" bash -c "
      unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE SSH_HOST SSH_USER SSH_PORT SSH_KEY REMOTE_DIR
      . '$REPO_ROOT/scripts/lib/deploy-env.sh'
      load_deploy_env '$2' >/dev/null 2>&1
      printf '%s' \"\${$3-}\"
    "
  }
  printf 'SSH_HOST=old-box\nSSH_USER=old-user\nSSH_PORT=2222\nSSH_KEY=/keys/old\nREMOTE_DIR=/srv/old\nIOS_TEAM_ID=ABCDE12345\nASC_KEY_ID=KEEPME\n' \
    > "$dest"
  out=$(run_setup_block "$WORK/setup" "$WORK/setup-repo" new-box '~/family-cfo' "$dest") || true
  if printf '%s\n' "$out" | grep -q 'Cleared the previous box' &&
     printf '%s\n' "$out" | grep -q 'SSH_USER=old-user' &&
     printf '%s\n' "$out" | grep -q 'SSH_PORT=2222' &&
     printf '%s\n' "$out" | grep -q 'SSH_KEY=/keys/old'; then
    ok "repointing says which old-box login settings it cleared"
  else
    ko "setup dropped the old box's ssh overrides in silence (out: $(printf '%s' "$out" | head -c 120))"
  fi
  [ "$(effective_of "$WORK/setup" "$WORK/setup-repo" SSH_HOST)" = "new-box" ] &&
    [ -z "$(effective_of "$WORK/setup" "$WORK/setup-repo" SSH_USER)" ] &&
    [ -z "$(effective_of "$WORK/setup" "$WORK/setup-repo" SSH_PORT)" ] &&
    [ -z "$(effective_of "$WORK/setup" "$WORK/setup-repo" SSH_KEY)" ] &&
    [ "$(effective_of "$WORK/setup" "$WORK/setup-repo" REMOTE_DIR)" = "~/family-cfo" ] &&
    grep -q '^IOS_TEAM_ID=ABCDE12345$' "$dest" && grep -q '^ASC_KEY_ID=KEEPME$' "$dest" &&
    ! grep -q 'keys/old' "$dest" &&
    ! grep -q 'old-box' "$dest" \
    && ok "…and the new destination loads clean, with the iOS/ASC settings intact" \
    || ko "repointing produced: host=$(effective_of "$WORK/setup" "$WORK/setup-repo" SSH_HOST) user=$(effective_of "$WORK/setup" "$WORK/setup-repo" SSH_USER) port=$(effective_of "$WORK/setup" "$WORK/setup-repo" SSH_PORT) key=$(effective_of "$WORK/setup" "$WORK/setup-repo" SSH_KEY) dir=$(effective_of "$WORK/setup" "$WORK/setup-repo" REMOTE_DIR)"

  # --- Padded, commented and duplicate keys are matched by the loader's rule ---
  # The loader strips a trailing comment and ALL whitespace from a key and keeps
  # the first value it sees. A writer that greps literal `^SSH_HOST=` leaves
  # ` SSH_HOST = old ` in place — and it keeps winning, so setup reports
  # `new-box` while the next patch goes to `old-box`. A bare `SSH_KEY` with no
  # '=' is owned too: the loader reads it as an empty value.
  printf ' SSH_HOST = old-box \n\tREMOTE_DIR = /srv/old\nSSH_KEY # a key, no =\n# a comment\nIOS_TEAM_ID=ABCDE12345\n' \
    > "$dest"
  out=$(run_setup_block "$WORK/setup" "$WORK/setup-repo" new-box '~/family-cfo' "$dest") || true
  if printf '%s\n' "$out" | grep -q '^E|'; then
    ko "setup could not rewrite a file with padded keys: $out"
  elif [ "$(effective_of "$WORK/setup" "$WORK/setup-repo" SSH_HOST)" = "new-box" ] &&
       [ "$(effective_of "$WORK/setup" "$WORK/setup-repo" REMOTE_DIR)" = "~/family-cfo" ] &&
       [ -z "$(effective_of "$WORK/setup" "$WORK/setup-repo" SSH_KEY)" ] &&
       grep -q '^IOS_TEAM_ID=ABCDE12345$' "$dest" &&
       grep -q '^# a comment$' "$dest" &&
       ! grep -q 'SSH_HOST =' "$dest" && ! grep -q 'REMOTE_DIR =' "$dest"; then
    ok "padded/commented/duplicate keys are replaced, not left to keep winning — and the loaded answer is the new box"
  else
    ko "setup left a key the loader reads differently than it writes (host=$(effective_of "$WORK/setup" "$WORK/setup-repo" SSH_HOST) dir=$(effective_of "$WORK/setup" "$WORK/setup-repo" REMOTE_DIR) file: $(tr '\n' '|' < "$dest"))"
  fi
fi

# --- The key rule is SHARED, not re-implemented -------------------------------
# One function answers "which key does this line set", used by both the loader
# and setup-ssh.sh. Pinned on the shapes that already caused a bug: padding, an
# inline comment, no '=', a comment-only line.
key_of() { # <line> → the key the loader sees for it
  in_lib "printf '%s' \"\$(deploy_env_key_of_line '$1')\"" 2>/dev/null
}
if [ "$(key_of ' SSH_HOST = x ')" = "SSH_HOST" ] &&
   [ "$(key_of 'SSH_KEY#note=v')" = "SSH_KEY" ] &&
   [ "$(key_of 'SSH_HOST')" = "SSH_HOST" ] &&
   [ -z "$(key_of '# just a comment')" ] &&
   [ "$(key_of 'IOS_TEAM_ID=1')" = "IOS_TEAM_ID" ]; then
  ok "the loader's key rule handles padding, comments and key-less lines"
else
  ko "deploy_env_key_of_line disagrees with the loader: [$(key_of ' SSH_HOST = x ')] [$(key_of 'SSH_KEY#note=v')] [$(key_of 'SSH_HOST')] [$(key_of '# just a comment')] [$(key_of 'IOS_TEAM_ID=1')]"
fi
if in_lib 'deploy_env_is_key SSH_HOST && deploy_env_is_key X && ! deploy_env_is_key foo-bar &&
           ! deploy_env_is_key 1BAD && ! deploy_env_is_key ""' 2>/dev/null; then
  ok "non-identifier keys are recognisable as unusable"
else
  ko "deploy_env_is_key accepts a key `export` will reject"
fi
# A `.deploy.env` holding a stray `foo-bar=1` — quite possibly pasted in from an
# iOS guide — used to abort the deploy: `export` rejects that key, the caller
# runs under `set -e`, and a config typo killed a deploy that had nothing to do
# with it. Now it is reported and skipped.
mkdir -p "$WORK/junk"
printf 'foo-bar=1\nSSH_HOST=good-box\n' > "$WORK/junk/.deploy.env"
if in_lib_env "DEPLOY_ENV_FILE='$WORK/junk/.deploy.env'" '
    unset SSH_HOST
    set -euo pipefail
    load_deploy_env "$SANDBOX_REPO" >/dev/null
    [ "$SSH_HOST" = "good-box" ] || exit 1
  ' 2>"$WORK/junk/err"; then
  ok "a stray unusable line is skipped, not a deploy-killing export failure"
else
  ko "a malformed key in .deploy.env aborted the load under set -e"
fi
grep -q 'foo-bar' "$WORK/junk/err" \
  && ok "…and the skipped line is named, not swallowed" \
  || ko "the unusable key was skipped in silence"
rm -rf "$WORK/junk"

# ===========================================================================
# 3. doctor.sh asks the container, not the host
# ===========================================================================
# On a cluster box the api runs on the HOST network (docker-compose.cluster.yml:
# network_mode: host + `!reset []`), where the compose service name `vllm` does
# not resolve, and the overlay sets FAMILY_CFO_AI_BASE_URL=http://127.0.0.1:8001
# — a value that exists only in the compose file, never in the host's .env. A
# hardcoded http://vllm:8000 therefore reported a dead AI on a healthy box.
doctor_probe_shape() { # awk-range extractor for the vLLM check
  awk '/^if svc_running vllm; then$/ {on=1} on {print} on && /^fi$/ {exit}' \
    "$REPO_ROOT/scripts/doctor.sh"
}
probe="$(doctor_probe_shape)"
if [ -z "$probe" ]; then
  ko "the vLLM check in doctor.sh was not found (renamed? this check is load-bearing)"
else
  ok "the vLLM check is still there"
  printf '%s\n' "$probe" | grep -q "printenv FAMILY_CFO_AI_BASE_URL" \
    && ok "doctor.sh reads the AI URL from the api container" \
    || ko "doctor.sh does not read FAMILY_CFO_AI_BASE_URL from the container"
  printf '%s\n' "$probe" | grep -q "http://vllm:8000/v1/models" \
    && ko "doctor.sh still probes a hardcoded http://vllm:8000" \
    || ok "doctor.sh no longer hardcodes the probe URL"
fi

# And behaviourally, with the block run against stubs: the URL it probes must be
# the one the container reports — asked OF the container, not read off the host.
if [ -n "$probe" ] && rm -f "$WORK/home/probed" && HOME="$WORK/home" bash -c "
      set -u
      svc_running() { return 0; }
      pass() { printf 'PASS %s\n' \"\$*\" >> \"\$HOME/probed\"; }
      warn() { printf 'WARN %s\n' \"\$*\" >> \"\$HOME/probed\"; }
      in_api() {
        case \"\$*\" in
          *'printenv FAMILY_CFO_AI_BASE_URL'*) printf 'http://127.0.0.1:8001/\n' ;;
          *urlopen*)
            # The URL must arrive as its own argument (never interpolated into
            # the source), already stripped of the trailing slash the overlay's
            # value may carry.
            printf 'ARG %s\n' \"\${!#}\" >> \"\$HOME/probed\"
            printf 'CODE %s\n' \"\$*\" >> \"\$HOME/probed\"
            ;;
        esac
        return 0
      }
      DC='docker compose'
      $probe
    " 2>/dev/null &&
   grep -q '^ARG http://127.0.0.1:8001$' "$WORK/home/probed" &&
   grep -q '/v1/models' "$WORK/home/probed" &&
   ! grep -q 'vllm:8000' "$WORK/home/probed"; then
  ok "the probe follows the container's URL (and strips its trailing slash)"
else
  ko "the probe did not follow the container's FAMILY_CFO_AI_BASE_URL"
fi
rm -f "$WORK/home/probed"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
