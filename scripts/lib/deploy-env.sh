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
# 2 and 3 are both read, first value wins, so a checkout that has not moved its
# file yet still gets the destination it has always used. DEPLOY_ENV_FILE points
# the loader at one specific file and disables both.
#
# Two rules this file keeps, because its exit status is load-bearing:
#   * a registry that cannot be written must NOT fail a deploy that already
#     succeeded — record_deployment / _install_registry_file are deliberately
#     non-fatal (see their comments);
#   * every caller sources this at the top of a script running under `set -e`,
#     where the status of the last statement in a function becomes the function's
#     status. Keep the `return 0`s; the comments say what each one guards.

# load_deploy_env <repo-root>
load_deploy_env() {
  local repo_root="$1"

  if [ -n "${DEPLOY_ENV_FILE:-}" ]; then
    _load_deploy_env_file "$DEPLOY_ENV_FILE"
    return 0
  fi

  # Machine-wide first: whoever moved the file to ~/.family-cfo means it to be
  # the answer from any directory. The checkout's own file is read afterwards and
  # only fills the gaps, so upgrading changes nobody's destination on its own.
  _load_deploy_env_file "$(deploy_home)/.deploy.env"
  _load_deploy_env_file "$repo_root/.deploy.env"
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
# FAMILY_CFO_HOME (a test seam too). The old per-checkout files are still READ —
# nobody loses the record of a stack they deployed before this moved — and the
# first write folds them into the shared file.
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

# Every registry file this machine knows about, newest location first: the
# shared one, then the pre-move per-checkout one. Deduplicated, and only the
# ones that actually exist. This is what READERS must use — reading only the
# shared file would make a pre-move checkout's entries vanish from `list`.
deploy_history_files() { # deploy_history_files <repo-root>
  local shared legacy
  shared="$(deploy_history_file "$1")"
  legacy="$1/.deploy.history"
  [ -f "$shared" ] && printf '%s\n' "$shared"
  [ -f "$legacy" ] && [ "$legacy" != "$shared" ] && printf '%s\n' "$legacy"
  return 0
}

# Registry rows across every file, newest-last-deployed winning when the same
# place appears in more than one (it does, right after the move). Rows for
# <exclude-key> are dropped. Keeps the file layout as the only source of truth:
# no cache, nothing to go stale.
deploy_registry_rows() { # deploy_registry_rows <repo-root> [exclude-key]
  local file
  deploy_history_files "$1" | while IFS= read -r file; do
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
}

_deploy_registry_warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

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

  # Read the existing rows FIRST, and check that read. Feeding the writer
  # straight from the reader would silently install a registry with entries
  # MISSING whenever the read failed — an empty file is a valid registry, so the
  # writer alone cannot tell "nothing there" from "the read died".
  if ! rows="$(deploy_registry_rows "$repo_root" "$key")"; then
    _deploy_registry_warn "could not read the existing registry; recording ${key} on its own."
    rows=""
  fi

  # A registry that cannot be written must never fail the deploy it is
  # describing: by this point the stack is up and healthy. Loud on stderr, and
  # otherwise out of the way.
  {
    # Drop any previous entry for the same place, then re-add it with a fresh
    # timestamp — the registry is a set of places, not an append-only log.
    # Going through deploy_registry_rows also folds in the pre-move per-checkout
    # file, so entries made before the move survive into the shared one.
    [ -n "$rows" ] && printf '%s\n' "$rows"
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
forget_deployment() { # forget_deployment <repo-root> <key>
  local repo_root="$1" key="$2" file left="" rows
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    # Same reason as in record_deployment: capture the filter's status, because
    # a failed filter piped into the writer would install an EMPTY registry and
    # silently forget everything.
    if ! rows="$(awk -F'\t' -v k="$key" '$1 != k' "$file")"; then
      left="${left}${left:+, }${file}"
      continue
    fi
    if { [ -n "$rows" ] && printf '%s\n' "$rows"; } | _install_registry_file "$file"; then
      continue
    fi
    left="${left}${left:+, }${file}"
  done <<< "$(deploy_history_files "$repo_root")"
  [ -n "$left" ] && _deploy_registry_warn "entry left in ${left} — remove it by hand if it reappears."
  return 0
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
