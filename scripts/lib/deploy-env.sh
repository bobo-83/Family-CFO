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

# load_deploy_env <repo-root>
#
# ONE file answers for the destination — the first that EXISTS, in this order:
# DEPLOY_ENV_FILE, ~/.family-cfo/.deploy.env, <repo-root>/.deploy.env. A real
# environment variable still outranks whatever that file says.
#
# Deliberately not a per-key merge. Merging made a half-written shared file
# inherit the stale checkout's SSH_USER/SSH_PORT/REMOTE_DIR around it, so a
# `patch.sh web` could pair the new box with the old box's user, port and stack
# path. A destination is one address; take it from one place. Both files may
# legitimately hold different things (a checkout file is also where iOS and
# TestFlight settings live), so the shadowed file is *reported*, never merged.
load_deploy_env() {
  local repo_root="$1" home shared checkout
  home="$(deploy_home)"
  shared="${home:+$home/.deploy.env}"
  checkout="$repo_root/.deploy.env"

  if [ -n "${DEPLOY_ENV_FILE:-}" ]; then
    _load_deploy_env_file "$DEPLOY_ENV_FILE"
    return 0
  fi

  if [ -n "$shared" ] && [ -f "$shared" ]; then
    _load_deploy_env_file "$shared"
    # The other file is not consulted, so say so: silently ignoring a file the
    # operator can see is how "my .deploy.env is being ignored" bug reports start.
    [ -f "$checkout" ] && printf '\033[1;33m!!\033[0m %s\n' \
      "Ignoring $checkout — $shared is the destination. Move anything you still want and delete it, or point DEPLOY_ENV_FILE at the file you mean." >&2
    return 0
  fi

  _load_deploy_env_file "$checkout"
  return 0
}

# _load_deploy_env_file <file> — one file's worth of the above. Silent when the
# file does not exist: having no persisted destination is the normal first run.
_load_deploy_env_file() {
  local file="$1"
  [ -f "$file" ] || return 0

  local key value loaded=""
  while IFS='=' read -r key value || [ -n "$key" ]; do
    # Strip comments, blank lines, and surrounding whitespace.
    key="${key%%#*}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    [ -n "$key" ] || continue

    # Trim whitespace and one layer of matching quotes from the value.
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    case "$value" in
      \"*\") value="${value#\"}"; value="${value%\"}" ;;
      \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac

    # An explicit environment variable outranks the file. Note SSH_KEY may be
    # intentionally blank, so test whether it is SET rather than non-empty.
    if [ -z "${!key+x}" ]; then
      export "$key=$value"
      loaded="${loaded}${loaded:+ }${key}"
    fi
  done < "$file"

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
deploy_registry_rows() { # deploy_registry_rows <repo-root> [exclude-key]
  local file readable="" incomplete=0
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    if [ -r "$file" ]; then
      readable="${readable}${readable:+$'\n'}${file}"
    else
      incomplete=1
      _deploy_registry_warn "cannot read ${file}."
    fi
  done <<< "$(deploy_history_files "$1")"

  # `if !` rather than a bare pipeline: this function's status is the answer to
  # "is the registry whole?", and errexit belongs to the caller, not here. The
  # -r test above can lie (root reads anything, and a chmod can race us), which
  # is why the cat failure has to be caught too.
  if ! printf '%s\n' "$readable" | while IFS= read -r file; do
         [ -n "$file" ] || continue
         cat -- "$file"
       done |
    awk -F'\t' -v k="${2:-}" '
      $1 == k { next }
      # Same place recorded in two files: keep the fresher timestamp. Field 8 is
      # an ISO-8601 UTC stamp, so it orders correctly as a plain string.
      { if (!($1 in fresh) || $8 >= fresh[$1]) { row[$1] = $0; fresh[$1] = $8 } }
      END { for (key in row) print row[key] }
    '
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

record_deployment() { # record_deployment <repo-root> <kind> <host> <user> <port> <dir> <compose>
  local repo_root="$1" kind="$2" host="$3" user="$4" port="$5" dir="$6" compose="$7"
  local file key stamp rows
  file="$(deploy_history_file "$repo_root")"
  key="${kind}|${host}|${dir}"
  stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  # ALL the existing rows, or none of the write. Replacing the file with what we
  # managed to read turns a permissions accident in one file into a MISSING
  # deployment in the other — the exact data this registry exists to prevent
  # (a stack nobody remembers running keeps holding ports, disk and a database
  # of household finances). Not recording is recoverable; un-recording is not.
  if ! rows="$(deploy_registry_rows "$repo_root" "$key")"; then
    _deploy_registry_warn "NOT recording ${key}: the registry could not be read in full (see above). This deploy is LIVE but UNLISTED — fix that file and deploy again, so the box is not forgotten."
    return 0
  fi

  # A registry that cannot be written must never fail the deploy it is
  # describing: by this point the stack is up and healthy. Loud on stderr, and
  # otherwise out of the way.
  {
    # Drop any previous entry for the same place, then re-add it with a fresh
    # timestamp — the registry is a set of places, not an append-only log.
    # Going through deploy_registry_rows also folds in the pre-move per-checkout
    # file, so entries made before the move survive into the shared one.
    _print_rows "$rows"
    # Empty fields are written as "-": tab is an IFS *whitespace* character, so
    # a run of empty columns would collapse into one on read and silently shift
    # every later field left.
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$key" "$kind" "$host" "${user:--}" "${port:--}" "${dir:--}" "${compose:--}" "$stamp"
  } | _install_registry_file "$file" ||
    _deploy_registry_warn "deployment NOT recorded in ${file} — run \`bash scripts/deployments.sh\` to check what this machine already knows about."
  return 0
}

# Removes an entry from EVERY registry file. All of them, deliberately: an entry
# left behind in the pre-move per-checkout file would reappear on the next read,
# because reads union the files. That is what makes `forget` mean forever.
#
# Returns non-zero when any file could not be read or rewritten. Unlike
# record_deployment's write — where failure must not fail a deploy — this status
# is the CLI's answer to "did you forget it?", and reporting success while the
# row is still listed leaves the operator with a registry they cannot trust.
forget_deployment() { # forget_deployment <repo-root> <key>
  local repo_root="$1" key="$2" file left="" rows rc=0
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    if ! rows="$(awk -F'\t' -v k="$key" '$1 != k' "$file")"; then
      _deploy_registry_warn "cannot read ${file}; its entry for ${key} is still there."
      rc=1
      continue
    fi
    if ! _print_rows "$rows" | _install_registry_file "$file"; then
      left="${left}${left:+, }${file}"
      rc=1
    fi
  done <<< "$(deploy_history_files "$repo_root")"
  [ -n "$left" ] && _deploy_registry_warn "could not update ${left} — ${key} may still be listed there."
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
