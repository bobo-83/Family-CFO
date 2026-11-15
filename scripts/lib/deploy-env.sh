#!/usr/bin/env bash
#
# Loads the persisted deploy destination (`.deploy.env`, see
# .deploy.env.example) and owns the deployment registry (`.deploy.history`).
# Sourced by scripts/deploy.sh, scripts/patch.sh, scripts/deployments.sh and the
# iOS scripts.
#
# Precedence, highest first:
#   1. A real environment variable   — SSH_HOST=other scripts/patch.sh web
#   2. ~/.family-cfo/.deploy.env     — the machine-wide destination
#   3. <repo-root>/.deploy.env       — the pre-move per-checkout location
#   4. The script's prompt / default
#
# So the file is a memory, not a cage: it stops `scripts/patch.sh web` from
# quietly defaulting to `local` and rebuilding containers on your laptop, while
# a deliberate one-off override still works.
#
# 2 and 3 are ALTERNATIVES, not layers: the first file that exists is the whole
# answer, so a half-migrated pair can never splice a new host onto the old box's
# user, port and stack path. DEPLOY_ENV_FILE names one specific file and disables
# both.
#
# Three rules this file keeps, because its exit status is load-bearing:
#   * a registry that cannot be written must NOT fail a deploy that already
#     succeeded — record_deployment / _install_registry_file are deliberately
#     non-fatal (see their comments);
#   * but it must never be REWRITTEN from an input that could not be read. An
#     empty registry is a valid registry, so the writer cannot tell "nothing
#     recorded" from "the read died" — the reader has to say so, and the writer
#     has to listen (deploy_registry_rows returns non-zero, record_deployment
#     then declines to write). forget_deployment is the exception: it reports
#     failure rather than returning 0 unconditionally, because a `forget` that
#     silently did nothing is a lie about the operator's own inventory.
#   * every caller sources this at the top of a script running under
#     `set -euo pipefail`, where the status of the last statement in a function
#     becomes the function's status — and where a pipeline is non-zero if ANY of
#     its stages is. So a producer that legitimately outputs nothing must still
#     exit 0 (_print_rows). Keep the `return 0`s; the comments say what guards.

# deploy_env_file <repo-root> — the ONE file that answers for the destination,
# in the order load_deploy_env consults them. When none exists yet, the file to
# CREATE (the machine-wide one, which is the recommended home).
#
# This is public because scripts/setup-ssh.sh asks it where to write. Onboarding
# and loading must agree on which file IS the destination, or the setup script
# writes a file nothing reads and the operator's first patch goes somewhere else.
deploy_env_file() { # deploy_env_file <repo-root>
  local repo_root="$1" home shared
  if [ -n "${DEPLOY_ENV_FILE:-}" ]; then
    printf '%s' "$DEPLOY_ENV_FILE"
    return
  fi
  home="$(deploy_home)"
  shared="${home:+$home/.deploy.env}"
  if [ -n "$shared" ] && [ -f "$shared" ]; then
    printf '%s' "$shared"
  elif [ -f "$repo_root/.deploy.env" ]; then
    printf '%s' "$repo_root/.deploy.env"
  else
    printf '%s' "${shared:-$repo_root/.deploy.env}"
  fi
}

# load_deploy_env <repo-root>
#
# ONE file answers for the destination — whatever deploy_env_file names. A real
# environment variable still outranks it, per key.
#
# Deliberately not a per-key merge. Merging made a half-written shared file
# inherit the stale checkout's SSH_USER/SSH_PORT/REMOTE_DIR around it, so a
# `patch.sh web` could pair the new box with the old box's user, port and stack
# path. A destination is one address; take it from one place. Both files may
# legitimately hold different things (a checkout file is also where iOS and
# TestFlight settings live), so the shadowed file is *reported*, never merged.
load_deploy_env() {
  local repo_root="$1" chosen home shared checkout
  chosen="$(deploy_env_file "$repo_root")"
  _load_deploy_env_file "$chosen"

  # The other conventional file is not consulted, so say so: silently ignoring a
  # file the operator can see is how "my .deploy.env is being ignored" reports
  # start. stderr, because the "Loaded from" line on stdout is the answer.
  home="$(deploy_home)"
  shared="${home:+$home/.deploy.env}"
  checkout="$repo_root/.deploy.env"
  if [ "$chosen" != "$checkout" ] && [ -f "$checkout" ]; then
    printf '\033[1;33m!!\033[0m %s\n' \
      "Ignoring $checkout — $chosen is the destination. Move anything you still want and delete it, or point DEPLOY_ENV_FILE at the file you mean." >&2
  fi
  return 0
}

# --- The one key rule ---------------------------------------------------------
#
# The reader and the WRITER (scripts/setup-ssh.sh, which replaces keys in place)
# must agree on what "the line that sets SSH_HOST" means, or setup appends
# SSH_HOST=new-box under a padded ` SSH_HOST = old ` and the reader — which
# keeps the FIRST value of a key — hands deploy the old box while setup reported
# the new one. So both go through these two functions and nothing else.
#
# The shape mirrors what a `KEY=VALUE` line means to `IFS='=' read -r key value`:
# the key is everything before the FIRST '=', a '#' starts a comment, and every
# whitespace character inside the key is ignored.
deploy_env_key_of_line() { # deploy_env_key_of_line <line> → the key, or empty
  local key
  key="${1%%=*}"
  key="${key%%#*}"
  # tr, not ${key//…}: this lib is sourced by scripts run under sh and dash too.
  key="$(printf '%s' "$key" | tr -d '[:space:]')"
  printf '%s' "$key"
}

# A key that is not a shell identifier must never reach `export`: `export` fails
# on it, the caller runs under `set -e`, and a stray line in a config file —
# quite possibly pasted in from an iOS guide — aborts a deploy that had nothing
# to do with that key.
deploy_env_is_key() { # deploy_env_is_key <normalized-key>
  case "$1" in
    "") return 1 ;;
    *[!A-Za-z0-9_]*) return 1 ;;   # any character outside the identifier set
    [0-9]*) return 1 ;;             # …and it may not start with a digit
    *) return 0 ;;
  esac
}

deploy_env_value_of_line() { # deploy_env_value_of_line <line> → the value
  local value
  case "$1" in
    *=*) value="${1#*=}" ;;
    *)   value="" ;;      # `KEY` with no '=' sets an empty value, as `read` does
  esac
  # Trim whitespace and one layer of matching quotes.
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  case "$value" in
    \"*\") value="${value#\"}"; value="${value%\"}" ;;
    \'*\') value="${value#\'}"; value="${value%\'}" ;;
  esac
  printf '%s' "$value"
}

# _load_deploy_env_file <file> — one file's worth of the above. Silent when the
# file does not exist: having no persisted destination is the normal first run.
_load_deploy_env_file() {
  local file="$1"
  [ -f "$file" ] || return 0

  local line key value loaded="" skipped=""
  while IFS= read -r line || [ -n "$line" ]; do
    key="$(deploy_env_key_of_line "$line")"
    [ -n "$key" ] || continue

    if ! deploy_env_is_key "$key"; then
      # Reported, never fatal: see deploy_env_is_key.
      skipped="${skipped}${skipped:+, }${key}"
      continue
    fi
    value="$(deploy_env_value_of_line "$line")"

    # An explicit environment variable outranks the file. Note SSH_KEY may be
    # intentionally blank, so test whether it is SET rather than non-empty.
    # eval, not ${!key+x}: the indirect form is a bashism, and this lib is
    # sourced under dash too. Safe to eval — deploy_env_is_key has already
    # restricted <key> to a shell identifier.
    if eval "[ -z \"\${$key+x}\" ]"; then
      export "$key=$value"
      loaded="${loaded}${loaded:+ }${key}"
    fi
  done < "$file"

  if [ -n "$skipped" ]; then
    printf '\033[1;33m!!\033[0m %s\n' \
      "Ignoring unusable line(s) in $file: $skipped — keys must be LETTERS_OR_NUMBERS=values." >&2
  fi
  if [ -n "$loaded" ]; then
    # The FULL path, not the basename: two files can now be named .deploy.env,
    # and "which one won" is exactly the question an operator debugging a patch
    # that went to the wrong box is asking.
    printf '\033[1;36m==>\033[0m Loaded from %s: %s\n' "$file" "$loaded"
  fi
  return 0
}

# --- Where the operator's state lives ---------------------------------------
#
# `.deploy.env` and `.deploy.history` describe the OPERATOR's fleet, not this
# checkout. Neither one should move when a script is invoked from somewhere
# other than the repository root — and both used to be resolved against whatever
# directory the script happened to run in, which is how one box ended up with
# several mutually invisible registries (issue #173: `cd` into `apps/api` and run
# `scripts/deploy.sh` and the entry lands in `apps/api/.deploy.history`).
#
# So both now live in ONE place per machine: `~/.family-cfo/`, overridable with
# FAMILY_CFO_HOME (a test seam too). The pre-move files under the checkout are
# handled differently for the two, and the difference is deliberate:
#
#   * .deploy.history is INVENTORY — union it. Reads consult both files and the
#     next write folds them together, so nobody loses the record of a stack they
#     deployed before the move, and an entry can never quietly disappear.
#   * .deploy.env is an ADDRESS — pick one file (see load_deploy_env). Merging
#     two partial ones splices a new host onto the old box's user, port and
#     stack path, which sends a patch somewhere nobody meant.
#
# Prints nothing when HOME is unset and FAMILY_CFO_HOME is not given: callers
# must then fall back to the checkout, rather than try to write at `/.`.
deploy_home() {
  if [ -n "${FAMILY_CFO_HOME:-}" ]; then
    printf '%s' "$FAMILY_CFO_HOME"
  elif [ -n "${HOME:-}" ]; then
    printf '%s' "$HOME/.family-cfo"
  fi
}

# --- Deployment registry ------------------------------------------------------
# Every successful deploy/patch records WHERE it went, so you can later ask what
# is still running out there and shut it down. Without this, a stack stood up on
# a box you've since forgotten about just keeps running — holding ports, disk,
# and a database full of household financial data.
#
# Format is TSV: key, kind, host, user, port, dir, compose-files, last-deployed.
# A row whose kind is "forgotten" is a TOMBSTONE: not a deployment, but the
# record that a place was forgotten. It suppresses its key in every file a read
# consults — a pre-move checkout file elsewhere on this machine cannot resurrect
# it — and record_deployment removes it when the place is deployed again.

# The file NEW entries are written to. DEPLOY_HISTORY_FILE overrides it outright
# (tests, or an operator who wants the registry somewhere else).
deploy_history_file() { # deploy_history_file <repo-root>
  local home
  if [ -n "${DEPLOY_HISTORY_FILE:-}" ]; then
    printf '%s' "$DEPLOY_HISTORY_FILE"
    return
  fi
  home="$(deploy_home)"
  if [ -n "$home" ]; then
    printf '%s' "$home/.deploy.history"
  else
    printf '%s' "$1/.deploy.history"
  fi
}

# Every registry file READS consult, newest location first: the shared one, then
# the pre-move per-checkout one. Deduplicated, and only the ones that exist.
# Reading only the shared file would make a pre-move checkout's entries vanish
# from `list`.
#
# An explicit DEPLOY_HISTORY_FILE selects ONE registry, exactly as it did before
# the shared directory existed: it is the seam for "this is the whole file, and
# nothing else". Unioning a legacy file into it would make a test or an operator
# isolate one registry and get someone else's deployments in it — recording
# would copy them in, forgetting would rewrite the other file. The migration
# union belongs to the DEFAULT path only. (FAMILY_CFO_HOME is different: it
# relocates the base directory, and the pre-move file is still worth folding in.)
deploy_history_files() { # deploy_history_files <repo-root>
  local shared legacy
  shared="$(deploy_history_file "$1")"
  if [ -n "${DEPLOY_HISTORY_FILE:-}" ]; then
    [ -f "$shared" ] && printf '%s\n' "$shared"
    return 0
  fi
  legacy="$1/.deploy.history"
  [ -f "$shared" ] && printf '%s\n' "$shared"
  [ -f "$legacy" ] && [ "$legacy" != "$shared" ] && printf '%s\n' "$legacy"
  return 0
}

_deploy_registry_warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

# Registry rows across every file, newest-last-deployed winning when the same
# place appears in more than one (it does, right after the move). Rows for
# <exclude-key> are dropped. Keeps the file layout as the only source of truth:
# no cache, nothing to go stale.
#
# STATUS IS PART OF THE CONTRACT. Rows from the readable files always print — a
# listing should show what it knows — but a file that exists and cannot be read
# makes this return non-zero and say which one. Callers that are about to REWRITE
# the registry must treat that as "do not write": the writer cannot distinguish
# "there was nothing there" from "the read died", because an empty registry is a
# legal registry. Only an all-or-nothing read may be written back.
#
# Hence the shape of the read below, which is not incidental:
#   * It is NOT inside a pipeline. A `while` loop's exit status is the LAST
#     command executed in its body, so with files A and B, `cat A` failing and
#     `cat B` succeeding leaves the loop — and therefore the pipeline — reporting
#     success even under `pipefail`, because no PIPELINE element ever failed. The
#     rows of A are gone and nothing said so. So each read is checked where it
#     happens, in the current shell, into `incomplete`.
#   * `-r` alone is not enough either: it is a snapshot (a chmod can race it) and
#     it lies for root, so the `cat` status is what actually decides.
_deploy_registry_read() { # _deploy_registry_read <repo-root> → rows on stdout, 0 iff whole
  local file one raw="" incomplete=0
  # A literal newline: the join below needs one BETWEEN files, and the two
  # obvious spellings are both wrong here — `$(printf '\n')` is stripped to
  # nothing by command substitution, and $'\n' is a bashism that dash expands
  # to the five literal characters `$'\n'`, gluing two files' rows into one
  # corrupted line mid-inventory.
  local nl="
"
  local list tmp
  # The file list feeds the `while read` below from a TEMP FILE — not a pipe
  # and not a `<<<` herestring. A pipe would run the loop in a subshell and the
  # `incomplete` status would be lost (the exact trap described above); a
  # herestring is a bashism, and this lib is sourced under dash too.
  #
  # EVERY step that materialises the list is checked HERE, not left to errexit:
  # this function only ever runs inside a conditional in its callers, and
  # errexit is OFF in a conditional — an unguarded failure would silently
  # continue with an empty list, read zero paths, report SUCCESS, and the
  # writer would then replace the whole registry with just its new row.
  if ! list="$(deploy_history_files "$1")"; then
    _deploy_registry_warn "cannot enumerate the registry files."
    return 1
  fi
  tmp="$(mktemp "${TMPDIR:-/tmp}/deploy-history-files.XXXXXX" 2>/dev/null)" || {
    _deploy_registry_warn "cannot enumerate the registry files."
    return 1
  }
  if ! printf '%s\n' "$list" > "$tmp"; then
    rm -f -- "$tmp" || :
    _deploy_registry_warn "cannot enumerate the registry files."
    return 1
  fi
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    if [ ! -r "$file" ]; then
      _deploy_registry_warn "cannot read ${file}."
      incomplete=1
      continue
    fi
    # Command substitution strips trailing newlines; every registry row is a
    # single line, so re-joining with one below reconstructs the file exactly
    # (and normalises a missing final newline).
    if ! one="$(cat -- "$file")"; then
      _deploy_registry_warn "failed while reading ${file}."
      incomplete=1
      continue
    fi
    raw="${raw}${raw:+${nl}}${one}"
  done < "$tmp"
  rm -f -- "$tmp" || :
  _print_rows "$raw"
  return "$incomplete"
}

deploy_registry_rows() { # deploy_registry_rows <repo-root> [exclude-key]
  local raw incomplete=0
  if ! raw="$(_deploy_registry_read "$1")"; then
    incomplete=1
  fi
  # `if !` rather than a bare pipeline: this function's status is the answer to
  # "is the registry whole?", and errexit belongs to the caller. _print_rows
  # guarantees status 0 for the legitimate empty case, so a non-zero here is the
  # filter or the sort genuinely failing — which is also an incomplete read.
  #
  # The sort is load-bearing, and the KEY is what makes it safe. POSIX leaves
  # `for (key in row)` in awk UNSPECIFIED, and these rows become the NUMBERS an
  # operator acts on (`deployments.sh stop boxA`, the menu's `2`) — an order that
  # varies between awk builds, machines or runs is how a numbered command lands
  # on the wrong box.
  #
  # Deliberately NOT by the last-deployed stamp: every successful patch rewrites
  # that stamp, so a sort on it renumbers the inventory underneath the operator —
  # list shows A as 1, patching B moves B to 1, and `stop 1` now means B. The key
  # (kind|host|dir) only changes when a place is added or forgotten, so the
  # number an operator read still means the same box a minute later. LC_ALL=C so
  # collation cannot shift it either. Recency stays available where it belongs:
  # as the listing's own LAST DEPLOYED column.
  #
  # A tombstone (field 2 "forgotten") is not a deployment: it records that a
  # place was forgotten, and suppresses every row for that key in EVERY file a
  # read consults — including pre-move checkout files this process cannot
  # rewrite. Without it, a forget made from one checkout would be undone by the
  # next fold-in from another. The tombstone rows themselves never print.
  if ! _print_rows "$raw" | awk -F'\t' -v k="${2:-}" '
      $2 == "forgotten" { forgotten[$1] = 1; next }
      $1 == k { next }
      # Same place recorded in two files: keep the fresher timestamp. Field 8 is
      # an ISO-8601 UTC stamp, so it orders correctly as a plain string.
      { if (!($1 in fresh) || $8 >= fresh[$1]) { row[$1] = $0; fresh[$1] = $8 } }
      # Suppression happens at EMIT time, not as rows stream: a marker must
      # suppress its key no matter whether the live row was read BEFORE it
      # (the shared registry is read first, a checkout fallback second — and
      # either file may hold either row). A `$1 in forgotten { next }` rule
      # kept every live row that happened to precede its marker.
      END { for (key in row) if (!(key in forgotten)) print row[key] }
    ' | LC_ALL=C sort -t"$(printf '\t')" -k1,1
  then
    incomplete=1
  fi
  return "$incomplete"
}

# The WRITE-side projection — what a rewrite of the shared registry installs.
# Same union and dedup as the inventory, two differences, both load-bearing:
#   * tombstone rows are KEPT. A rewrite that dropped them would un-forget a
#     key the next time a pre-move file was folded in — the resurrection this
#     round is about.
#   * a live row for a tombstoned key is dropped REGARDLESS of stamps. A forget
#     is a decision, not a row with a timestamp; a legacy copy stamped later
#     than the forget must not outvote it — and the rewrite must not install
#     that stale copy beside the tombstone for a later, tombstone-blind reader
#     to act on.
# Rows for <exclude-key> are dropped outright: recording that key supersedes
# both its live row and its tombstone (re-deploying a place un-forgets it).
_deploy_registry_rows_for_write() { # _deploy_registry_rows_for_write <repo-root> <exclude-key>
  local raw incomplete=0
  if ! raw="$(_deploy_registry_read "$1")"; then
    incomplete=1
  fi
  if ! _print_rows "$raw" | awk -F'\t' -v k="$2" '
      $1 == k { next }
      $2 == "forgotten" {
        # Several tombstones for one key can exist during the move; keep the
        # freshest so the rewrite is deterministic (an UNSPECIFIED last-one
        # here would make the installed file depend on awk iteration order).
        if (!($1 in tfresh) || $8 >= tfresh[$1]) { tomb[$1] = $0; tfresh[$1] = $8 }
        next
      }
      { if (!($1 in fresh) || $8 >= fresh[$1]) { row[$1] = $0; fresh[$1] = $8 } }
      # Same emit-time rule as the inventory: a marker suppresses its key
      # regardless of which file held the live row, or which came first.
      END {
        for (key in row) if (!(key in tomb)) print row[key]
        for (key in tomb) print tomb[key]
      }
    ' | LC_ALL=C sort -t"$(printf '\t')" -k1,1
  then
    incomplete=1
  fi
  return "$incomplete"
}

# <rows-string> on stdout, and ALWAYS status 0 — including the zero-rows case,
# which is a legitimate result (forgetting the last entry) rather than a failure.
# Without the `return 0` the `[ -n ]` test is the last command, the pipeline is
# non-zero under `pipefail`, and a successful rewrite is reported as a failed
# one. That is not a cosmetic bug: the caller's whole answer is this status.
_print_rows() { # _print_rows <rows-string>
  [ -n "$1" ] && printf '%s\n' "$1"
  return 0
}

# Installs stdin as <file>'s new contents, and does NOT take the old file down
# with it if the write goes wrong.
#
# Two rules here, both learned the hard way:
#
#   * mkdir -p FIRST. `mv` onto a missing DESTINATION DIRECTORY exits 1 on both
#     BSD and GNU coreutils — and these scripts run under `set -e` with the
#     record call placed after the stack is already up. A missing ~/.family-cfo
#     would therefore abort a fully successful deploy on its last line.
#   * the temp file is created in the DESTINATION's directory, not by mktemp's
#     default location. /tmp is a different filesystem from a home directory (on
#     many Linux boxes it is a tmpfs), and `mv` across filesystems is not a
#     rename — it is copy-then-unlink, which can die half-way and take the old
#     registry with it. Same directory, and the swap is one atomic rename.
#
# Both mv(1)s — BSD on macOS and GNU coreutils on the Linux targets — behave the
# same way for these flags, so this needs no platform branch. `-f` keeps it from
# prompting if the destination was ever made read-only.
_install_registry_file() { # _install_registry_file <file>   (new contents on stdin)
  local file="$1" tmp
  mkdir -p -- "$(dirname -- "$file")" 2>/dev/null || return 1
  tmp="$(mktemp "${file}.XXXXXX" 2>/dev/null)" || return 1
  if ! cat > "$tmp"; then
    rm -f -- "$tmp"
    return 1
  fi
  if ! mv -f -- "$tmp" "$file" 2>/dev/null; then
    rm -f -- "$tmp"
    _deploy_registry_warn "could not write the deployment registry at ${file}"
    return 1
  fi
  return 0
}

# The lock that makes a registry update a read-modify-write ATOMIC, not just the
# rename: two writers that both read the same inventory and both install their
# version would EACH report success, and the first one's row would be gone —
# sharing one file across checkouts turned a within-checkout race into a
# cross-checkout one. A directory is the one lock primitive POSIX guarantees to
# be atomic on every filesystem these scripts touch (flock(1) does not exist on
# the Mac); it is held across the read, the merge and the install, so a write is
# serialized against the state it was computed from.
#
# A writer that dies holding it blocks the next writes for 5s (50 × 0.1s), which
# then refuse loudly and name the directory to remove — an honest failure beats
# a stale lock nobody can see. _DEPLOY_REGISTRY_LOCK_TRIES is a test seam.
_deploy_registry_lock() { # _deploy_registry_lock <registry-file> → 0 iff held
  local lock="$1.lock" i=0
  mkdir -p -- "$(dirname -- "$lock")" 2>/dev/null || return 1
  while :; do
    if mkdir -- "$lock" 2>/dev/null; then
      return 0
    fi
    i=$((i + 1))
    if [ "$i" -ge "${_DEPLOY_REGISTRY_LOCK_TRIES:-50}" ]; then
      return 1
    fi
    sleep 0.1
  done
}

_deploy_registry_unlock() { # _deploy_registry_unlock <registry-file>
  rmdir -- "$1.lock" 2>/dev/null || :
}

# The fold is one-way, and this is what makes it one-way: once a successful
# write has folded the pre-move file's rows into the shared registry, that file
# stops being read. Left in place it resurrects entries forever — including
# ones forgotten from ANOTHER checkout, which cannot rewrite this file. Renamed,
# not deleted: nothing is destroyed, it just stops feeding the inventory.
# Only the DEFAULT path retires: an explicit DEPLOY_HISTORY_FILE is the "this
# one file is the whole registry" seam, and its file was never consulted here,
# so its sibling must not be moved.
_deploy_retire_legacy_registry() { # _deploy_retire_legacy_registry <repo-root> <shared-file>
  [ -n "${DEPLOY_HISTORY_FILE:-}" ] && return 0
  local legacy="$1/.deploy.history"
  [ -f "$legacy" ] || return 0
  [ "$legacy" != "$2" ] || return 0
  if mv -f -- "$legacy" "$legacy.migrated" 2>/dev/null; then
    _deploy_registry_warn "retired ${legacy} — its rows are now in ${2}."
  else
    _deploy_registry_warn "could not retire ${legacy} — its rows are already in ${2}, but the file will keep being consulted."
  fi
  return 0
}

record_deployment() { # record_deployment <repo-root> <kind> <host> <user> <port> <dir> <compose>
  local repo_root="$1" kind="$2" host="$3" user="$4" port="$5" dir="$6" compose="$7"
  local file key stamp rows installed=0
  file="$(deploy_history_file "$repo_root")"
  key="${kind}|${host}|${dir}"
  stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  # The lock spans the read AND the write: the rename alone does not make a
  # read-modify-write safe (see _deploy_registry_lock).
  if ! _deploy_registry_lock "$file"; then
    _deploy_registry_warn "NOT recording ${key}: another registry write is in progress (${file}.lock) — this deploy is LIVE but UNLISTED. If no deploy is running, remove ${file}.lock."
    return 0
  fi

  # ALL the existing rows, or none of the write. Replacing the file with what we
  # managed to read turns a permissions accident in one file into a MISSING
  # deployment in the other — the exact data this registry exists to prevent
  # (a stack nobody remembers running keeps holding ports, disk and a database
  # of household finances). Not recording is recoverable; un-recording is not.
  # The write-side projection, not the inventory: it keeps other keys'
  # tombstones alive and drops this key's (a re-deploy supersedes a forget).
  if ! rows="$(_deploy_registry_rows_for_write "$repo_root" "$key")"; then
    _deploy_registry_unlock "$file"
    _deploy_registry_warn "NOT recording ${key}: the registry could not be read in full (see above). This deploy is LIVE but UNLISTED — fix that file and deploy again, so the box is not forgotten."
    return 0
  fi

  # A registry that cannot be written must never fail the deploy it is
  # describing: by this point the stack is up and healthy. Loud on stderr, and
  # otherwise out of the way.
  {
    # Drop any previous entry for the same place, then re-add it with a fresh
    # timestamp — the registry is a set of places, not an append-only log.
    # Going through the projection also folds in the pre-move per-checkout
    # file, so entries made before the move survive into the shared one.
    _print_rows "$rows"
    # Empty fields are written as "-": tab is an IFS *whitespace* character, so
    # a run of empty columns would collapse into one on read and silently shift
    # every later field left.
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$key" "$kind" "$host" "${user:--}" "${port:--}" "${dir:--}" "${compose:--}" "$stamp"
  # `|| installed=$?`, not a bare pipeline: this function runs under the
  # callers' `set -euo pipefail`, and a bare failing pipeline would exit the
  # shell BEFORE the warning, before `installed=$?` and before the unlock —
  # aborting a deploy that already succeeded and leaving the registry locked.
  # In a conditional, errexit stands down and the status lands in `installed`.
  # (No second `installed=$?` here: that would capture the OR-LIST's status —
  # always 0 — and clobber the failure right back out of `installed`.)
  } | _install_registry_file "$file" || installed=$?
  if [ "$installed" -eq 0 ]; then
    # The fold just moved every pre-move row into the shared file; retire the
    # source so it cannot be folded — and resurrect forgotten keys — again.
    _deploy_retire_legacy_registry "$repo_root" "$file"
  else
    _deploy_registry_warn "deployment NOT recorded in ${file} — run \`bash scripts/deployments.sh\` to check what this machine already knows about."
  fi
  _deploy_registry_unlock "$file"
  return 0
}

# Forgets a place — permanently, from every file a future read could consult.
#
# The decision is RECORDED in the shared registry as a tombstone row (field 2
# "forgotten"), not merely implemented by deleting rows. The pre-move
# per-checkout files of OTHER checkouts cannot be rewritten from here, and a
# delete-only forget came back the next time one of them was folded in — the
# resurrection this round is about. A tombstone follows the key wherever its
# rows still sit, in any file; recording the place again removes it, because a
# re-deploy supersedes a forget.
#
# Returns non-zero when the shared registry could not be read in full or
# rewritten. Unlike record_deployment's write — where failure must not fail a
# deploy — this status is the CLI's answer to "did you forget it?", and
# reporting success while the row is still listed leaves the operator with a
# registry they cannot trust.
forget_deployment() { # forget_deployment <repo-root> <key>
  local repo_root="$1" key="$2" file rows stamp installed=0 rc=0
  file="$(deploy_history_file "$repo_root")"
  stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  if ! _deploy_registry_lock "$file"; then
    _deploy_registry_warn "could not lock ${file}.lock — the entry for ${key} is still listed. If no deploy is running, remove ${file}.lock."
    return 1
  fi

  if ! rows="$(_deploy_registry_rows_for_write "$repo_root" "$key")"; then
    _deploy_registry_unlock "$file"
    _deploy_registry_warn "cannot read the registry in full; the entry for ${key} is still there."
    return 1
  fi

  {
    _print_rows "$rows"
    # The tombstone. Same "-" convention as records: empty fields must not
    # collapse, or every later field shifts left on read.
    printf '%s\tforgotten\t-\t-\t-\t-\t-\t%s\n' "$key" "$stamp"
  # Same as record: a bare failing pipeline would exit before the warning and
  # the unlock, reporting neither the failure nor releasing the lock. And no
  # second `installed=$?` — same clobber as record.
  } | _install_registry_file "$file" || installed=$?
  if [ "$installed" -ne 0 ]; then
    _deploy_registry_warn "could not update ${file} — ${key} may still be listed there."
    rc=1
  else
    # The fold moved every pre-move row into the shared file; retire the source.
    _deploy_retire_legacy_registry "$repo_root" "$file"
  fi
  _deploy_registry_unlock "$file"
  return "$rc"
}

# Is this host the machine named by <host>? Used to work out whether the box is
# "over there" or "right here".
host_is_self() {
  local host="$1"
  [ -n "$host" ] || return 1
  case "$host" in localhost | 127.0.0.1 | ::1) return 0 ;; esac
  [ "$host" = "$(hostname 2>/dev/null)" ] && return 0
  [ "$host" = "$(hostname -s 2>/dev/null)" ] && return 0

  local ips
  if command -v ip >/dev/null 2>&1; then
    ips="$(ip -o -4 addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1)"
  else
    ips="$(ifconfig 2>/dev/null | awk '/inet /{print $2}')"
  fi
  printf '%s\n' $ips | grep -qx -- "$host"
}

# Work out local vs remote from WHERE YOU ARE, rather than making you remember.
#
# The same .deploy.env is correct on both machines: on the Linux box, SSH_HOST
# IS this machine, so the stack is patched locally; on the MacBook it isn't, so
# it goes over SSH to the box. Nobody has to set TARGET, and a stale TARGET in a
# file can't send a patch to the wrong place.
#
# An explicit TARGET always wins, for the odd case that doesn't fit.
resolve_target() {
  if [ -n "${TARGET:-}" ]; then
    return
  fi
  if [ -z "${SSH_HOST:-}" ]; then
    TARGET="local"
    return
  fi

  local hosts count first
  hosts="$(printf '%s' "$SSH_HOST" | tr ',' ' ')"
  count="$(printf '%s' "$hosts" | wc -w | tr -d ' ')"
  first="$(printf '%s' "$hosts" | awk '{print $1}')"

  if [ "$count" -eq 1 ] && host_is_self "$first"; then
    TARGET="local"
    printf '\033[1;36m==>\033[0m This machine IS %s — patching locally.\n' "$first"
  else
    TARGET="remote"
  fi
  export TARGET
}
