#!/usr/bin/env bash
#
# Loads the persisted deploy destination from `.deploy.env` (see
# .deploy.env.example). Sourced by scripts/deploy.sh and scripts/patch.sh.
#
# Precedence, highest first:
#   1. A real environment variable   — SSH_HOST=other scripts/patch.sh web
#   2. .deploy.env                   — the committed-to destination
#   3. The script's prompt / default
#
# So the file is a memory, not a cage: it stops `scripts/patch.sh web` from
# quietly defaulting to `local` and rebuilding containers on your laptop, while
# a deliberate one-off override still works.

# load_deploy_env <repo-root>
load_deploy_env() {
  local repo_root="$1"
  local file="${DEPLOY_ENV_FILE:-$repo_root/.deploy.env}"
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
    printf '\033[1;36m==>\033[0m Loaded from %s: %s\n' "$(basename "$file")" "$loaded"
  fi
}

# --- Deployment registry ------------------------------------------------------
# Every successful deploy/patch records WHERE it went, so you can later ask what
# is still running out there and shut it down. Without this, a stack stood up on
# a box you've since forgotten about just keeps running — holding ports, disk,
# and a database full of household financial data.
#
# Format is TSV: key, kind, host, user, port, dir, compose-files, last-deployed.
deploy_history_file() {
  printf '%s' "${DEPLOY_HISTORY_FILE:-$1/.deploy.history}"
}

record_deployment() { # record_deployment <repo-root> <kind> <host> <user> <port> <dir> <compose>
  local repo_root="$1" kind="$2" host="$3" user="$4" port="$5" dir="$6" compose="$7"
  local file key stamp tmp
  file="$(deploy_history_file "$repo_root")"
  key="${kind}|${host}|${dir}"
  stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  tmp="$(mktemp)"
  {
    # Drop any previous entry for the same place, then re-add it with a fresh
    # timestamp — the registry is a set of places, not an append-only log.
    [ -f "$file" ] && awk -F'\t' -v k="$key" '$1 != k' "$file"
    # Empty fields are written as "-": tab is an IFS *whitespace* character, so
    # a run of empty columns would collapse into one on read and silently shift
    # every later field left.
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$key" "$kind" "$host" "${user:--}" "${port:--}" "${dir:--}" "${compose:--}" "$stamp"
  } > "$tmp"
  mv "$tmp" "$file"
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
