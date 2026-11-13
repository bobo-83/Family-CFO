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

# forget must clear EVERY file, because reads union them: an entry left in the
# pre-move file would simply come back.
in_lib '
  forget_deployment "'"$WORK"'/old-checkout" "remote|dup|/srv/dup"
  deploy_registry_rows "'"$WORK"'/old-checkout" | grep -q "remote|dup" && exit 1
  grep -q "remote|dup" "$HOME/.family-cfo/.deploy.history" && exit 1
  grep -q "remote|dup" "'"$WORK"'/old-checkout/.deploy.history" && exit 1
  exit 0
' && ok "forget removes the entry from every registry file" \
  || ko "forget left the entry somewhere it can be read back"

# An unwritable registry must cost the operator a warning, not a deploy: the
# call sites are plain commands under `set -e`, after the stack is up.
chmod 500 "$WORK/home/.family-cfo"
# The warning on stderr is the point of this case; it is not test noise.
if in_lib 'set -euo pipefail; record_deployment "$SANDBOX_REPO" remote doomed me 22 /srv/x a.yml' 2>/dev/null; then
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
[ -s "$WORK/home/.family-cfo/.deploy.history" ] && ko "the last row survived its own forget" \
  || ok "the last row really is gone"
# And the opposite: a file that cannot be rewritten must be REPORTED, not
# glossed over with "the registry entry is gone".
printf 'local|stuck|/nonexistent-fcfo-s\tlocal\tstuck\t-\t-\t/nonexistent-fcfo-s\tc.yml\t2026-01-01T00:00:00Z\n' \
  > "$WORK/home/.family-cfo/.deploy.history"
chmod 500 "$WORK/home/.family-cfo"
if in_lib 'set -euo pipefail; forget_deployment "$SANDBOX_REPO" "local|stuck|/nonexistent-fcfo-s"' 2>/dev/null; then
  ko "a forget that could not rewrite any file reported success"
else
  ok "a forget that could not rewrite a file reports failure"
fi
chmod 700 "$WORK/home/.family-cfo"
printf 'local|stuck|/nonexistent-fcfo-s\tlocal\tstuck\t-\t-\t/nonexistent-fcfo-s\tc.yml\t2026-01-01T00:00:00Z\n' \
  > "$WORK/home/.family-cfo/.deploy.history"
chmod 500 "$WORK/home/.family-cfo"
cli_rc=0; cli_out=$(HOME="$WORK/home" bash "$WORK/cli/scripts/deployments.sh" forget 1 2>&1) || cli_rc=$?
chmod 700 "$WORK/home/.family-cfo"
if [ "$cli_rc" -ne 0 ] && ! printf '%s\n' "$cli_out" | grep -q "entry is gone"; then
  ok "the CLI does not claim 'the registry entry is gone' when a file was unwritable"
else
  ko "the CLI promised the entry was gone while it was still listed"
fi

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
