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

# Owner-only, like the .deploy.env beside it: hostnames, users and paths.
# `ls -l`'s first field is portable; `stat`'s format flags are not. Strip the
# macOS '@'/'+' suffix that flags extended attributes or an ACL.
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
# Read from BOTH locations, shared winning, a real environment variable winning
# over both. A checkout-local file that has not moved yet must keep working:
# silently losing it would send `patch.sh web` at the laptop instead of the box.
mkdir -p "$WORK/home/.family-cfo" "$WORK/both"
printf 'SSH_HOST=from-checkout\nSSH_USER=u1\nSSH_KEY=\n' > "$WORK/both/.deploy.env"
printf 'SSH_HOST=from-shared\nSSH_PORT=2222\n' > "$WORK/home/.family-cfo/.deploy.env"
env_probe() { # expected-var=value pairs on stdout
  HOME="$WORK/home" bash -c "
    unset FAMILY_CFO_HOME DEPLOY_ENV_FILE DEPLOY_HISTORY_FILE
    . '$REPO_ROOT/scripts/lib/deploy-env.sh'
    $1
    load_deploy_env '$WORK/both' >/dev/null 2>&1 || exit 1
    printf 'SSH_HOST=%s\nSSH_USER=%s\nSSH_PORT=%s\nSSH_KEY_SET=%s\n' \
      \"\${SSH_HOST-}\" \"\${SSH_USER-}\" \"\${SSH_PORT-}\" \"\${SSH_KEY+yes}\"
  "
}
got="$(env_probe 'export SSH_USER=from-env')"
[ "$(printf '%s\n' "$got" | grep '^SSH_HOST=')" = "SSH_HOST=from-shared" ] \
  && ok "the machine-wide .deploy.env outranks the checkout's" \
  || ko "the checkout's .deploy.env still wins (got: $(printf '%s\n' "$got" | grep '^SSH_HOST='))"
[ "$(printf '%s\n' "$got" | grep '^SSH_USER=')" = "SSH_USER=from-env" ] \
  && ok "a real environment variable still outranks both files" \
  || ko "a file value overrode an explicit environment variable"
[ "$(printf '%s\n' "$got" | grep '^SSH_PORT=')" = "SSH_PORT=2222" ] \
  && ok "values are merged across the two locations" \
  || ko "the shared file's unique key was not loaded"

rm -f "$WORK/home/.family-cfo/.deploy.env"
[ "$(env_probe '' | grep '^SSH_HOST=')" = "SSH_HOST=from-checkout" ] \
  && ok "a pre-move checkout-local .deploy.env still sets the destination" \
  || ko "upgrading silently lost a checkout-local destination"

# A blank value is a value (SSH_KEY may be deliberately empty — see the lib).
[ "$(env_probe '' | grep '^SSH_KEY_SET=')" = "SSH_KEY_SET=yes" ] \
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
