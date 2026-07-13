#!/usr/bin/env bash
#
# Family CFO — what have I deployed, and is it still running?
#
# Every successful `scripts/deploy.sh` / `scripts/patch.sh` records where it went
# (.deploy.history). This script reads that back, checks each place for LIVE
# containers, and lets you shut them down — because a stack stood up on a box you
# have since forgotten about keeps running: holding ports, filling disk, and
# sitting on a database full of your household's financial data.
#
# Usage:
#   scripts/deployments.sh              # list, then offer to act (interactive)
#   scripts/deployments.sh list         # just list; never prompts
#   scripts/deployments.sh stop    <n>  # stop containers        (data kept, restartable)
#   scripts/deployments.sh remove  <n>  # remove containers      (VOLUMES KEPT: db + model safe)
#   scripts/deployments.sh destroy <n>  # remove containers AND VOLUMES — deletes the database
#   scripts/deployments.sh uninstall <n># iOS entries: remove the app from the phone
#   scripts/deployments.sh forget  <n>  # drop from the registry only; touches nothing
#
# <n> is the number from the listing, or a host name.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck source=lib/deploy-env.sh
. "$REPO_ROOT/scripts/lib/deploy-env.sh"

HISTORY="$(deploy_history_file "$REPO_ROOT")"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$HISTORY" ] || {
  log "No deployments recorded yet."
  echo "  Nothing has been deployed from this checkout (or .deploy.history was removed)."
  exit 0
}

# --- Load the registry -------------------------------------------------------
KINDS=() HOSTS=() USERS=() PORTS=() DIRS=() COMPOSES=() STAMPS=()
# "-" is how an empty field is stored (see record_deployment); turn it back.
undash() { [ "$1" = "-" ] && printf '' || printf '%s' "$1"; }
while IFS=$'\t' read -r _key kind host user port dir compose stamp; do
  [ -n "${kind:-}" ] || continue
  KINDS+=("$kind"); HOSTS+=("$host")
  USERS+=("$(undash "$user")"); PORTS+=("$(undash "$port")")
  DIRS+=("$(undash "$dir")"); COMPOSES+=("$(undash "$compose")")
  STAMPS+=("$stamp")
done < "$HISTORY"

COUNT="${#KINDS[@]}"
[ "$COUNT" -gt 0 ] || { log "No deployments recorded yet."; exit 0; }

# Recorded user/port may be empty, meaning "ssh works it out from ~/.ssh/config".
# Passing an empty -p or a bare `@host` would break exactly that.
ssh_target_for() { # ssh_target_for <index>
  if [ -n "${USERS[$1]}" ]; then
    printf '%s@%s' "${USERS[$1]}" "${HOSTS[$1]}"
  else
    printf '%s' "${HOSTS[$1]}"
  fi
}

ssh_opts_for() { # ssh_opts_for <index> -> prints options, one per line
  printf '%s\n' -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8
  [ -n "${PORTS[$1]}" ] && printf '%s\n' -p "${PORTS[$1]}"
  return 0
}

ssh_to() { # ssh_to <index> <command...>
  local i="$1"; shift
  local opts=()
  while IFS= read -r opt; do opts+=("$opt"); done < <(ssh_opts_for "$i")
  # BatchMode: probing status must never sit at a password prompt.
  ssh "${opts[@]}" -o BatchMode=yes "$(ssh_target_for "$i")" "$@" 2>/dev/null
}

# Running containers at a place, or a word explaining why we can't tell. Never
# guesses: "unreachable" is an honest answer and "0" is not.
status_of() { # status_of <index>
  local i="$1" kind="${KINDS[$i]}" out
  case "$kind" in
    local)
      command -v docker >/dev/null 2>&1 || { printf 'no-docker'; return; }
      out="$(cd "${DIRS[$i]}" 2>/dev/null && docker compose ${COMPOSES[$i]} ps --services --status running 2>/dev/null | grep -c . || true)"
      printf '%s running' "${out:-0}"
      ;;
    remote)
      out="$(ssh_to "$i" "cd ${DIRS[$i]} 2>/dev/null && docker compose ${COMPOSES[$i]} ps --services --status running 2>/dev/null | grep -c ." || true)"
      if [ -z "$out" ]; then
        printf 'unreachable'
      else
        printf '%s running' "$out"
      fi
      ;;
    ios)
      if xcrun devicectl list devices 2>/dev/null | grep -q "${USERS[$i]}.*connected"; then
        printf 'device connected'
      else
        printf 'device offline'
      fi
      ;;
  esac
}

print_list() {
  printf '\n  %-3s %-7s %-24s %-18s %-20s %s\n' "#" "KIND" "WHERE" "STATUS" "LAST DEPLOYED" "DIR"
  printf '  %s\n' "-------------------------------------------------------------------------------------------------------"
  local i status
  for i in $(seq 0 $((COUNT - 1))); do
    status="$(status_of "$i")"
    printf '  %-3s %-7s %-24s %-18s %-20s %s\n' \
      "$((i + 1))" "${KINDS[$i]}" "${HOSTS[$i]}" "$status" "${STAMPS[$i]}" "${DIRS[$i]}"
  done
  echo
}

# Resolve "3" or "192.168.1.10" to an index.
resolve_index() { # resolve_index <selector>
  local sel="$1" i
  if printf '%s' "$sel" | grep -qE '^[0-9]+$'; then
    [ "$sel" -ge 1 ] && [ "$sel" -le "$COUNT" ] || die "No deployment #${sel} (there are ${COUNT})."
    printf '%s' "$((sel - 1))"
    return
  fi
  for i in $(seq 0 $((COUNT - 1))); do
    if [ "${HOSTS[$i]}" = "$sel" ]; then printf '%s' "$i"; return; fi
  done
  die "No recorded deployment for '${sel}'."
}

compose_at() { # compose_at <index> <compose-args...>
  local i="$1"; shift
  case "${KINDS[$i]}" in
    local)
      # shellcheck disable=SC2086
      (cd "${DIRS[$i]}" && docker compose ${COMPOSES[$i]} "$@")
      ;;
    remote)
      local opts=()
      while IFS= read -r opt; do opts+=("$opt"); done < <(ssh_opts_for "$i")
      # No BatchMode here: acting on a stack may legitimately need a passphrase
      # prompt, and that prompt belongs to ssh — not to this script.
      ssh "${opts[@]}" "$(ssh_target_for "$i")" \
        "cd ${DIRS[$i]} && docker compose ${COMPOSES[$i]} $*"
      ;;
    ios)
      die "'${HOSTS[$i]}' is an iPhone, not a container stack — use: scripts/deployments.sh uninstall $((i + 1))"
      ;;
  esac
}

do_stop() {
  local i; i="$(resolve_index "$1")"
  log "Stopping containers on ${HOSTS[$i]} (data kept — 'scripts/patch.sh' brings it back)…"
  compose_at "$i" stop
  log "Stopped."
}

do_remove() {
  local i; i="$(resolve_index "$1")"
  log "Removing containers on ${HOSTS[$i]} — VOLUMES ARE KEPT, so the database and the model cache survive…"
  compose_at "$i" down
  log "Containers removed. The data is still there; a deploy will bring the stack back."
}

do_destroy() {
  local i; i="$(resolve_index "$1")"
  local host="${HOSTS[$i]}"
  warn "This DELETES THE VOLUMES on ${host}: the PostgreSQL database (every account,"
  warn "transaction and conversation in that household) and the model cache (a multi-GB"
  warn "re-download). There is no undo, and no backup is taken for you."
  printf 'Type the host name (%s) to confirm: ' "$host"
  local reply; read -r reply || true
  [ "$reply" = "$host" ] || { log "Cancelled — nothing was touched."; return; }
  compose_at "$i" down -v
  log "Destroyed ${host} including its volumes."
}

do_uninstall() {
  local i; i="$(resolve_index "$1")"
  [ "${KINDS[$i]}" = "ios" ] || die "'${HOSTS[$i]}' is not an iPhone deployment."
  log "Removing the app from ${HOSTS[$i]}…"
  xcrun devicectl device uninstall app --device "${USERS[$i]}" "${COMPOSES[$i]}" \
    || die "Uninstall failed — is the phone unlocked and on the network?"
  log "Uninstalled ${COMPOSES[$i]} from ${HOSTS[$i]}."
}

do_forget() {
  local i; i="$(resolve_index "$1")"
  local key="${KINDS[$i]}|${HOSTS[$i]}|${DIRS[$i]}"
  local tmp; tmp="$(mktemp)"
  awk -F'\t' -v k="$key" '$1 != k' "$HISTORY" > "$tmp"
  mv "$tmp" "$HISTORY"
  log "Forgot ${HOSTS[$i]} — the registry entry is gone. Nothing was stopped or deleted."
}

# --- Dispatch ----------------------------------------------------------------
ACTION="${1:-}"
case "$ACTION" in
  list)      print_list; exit 0 ;;
  stop)      [ $# -ge 2 ] || die "Usage: scripts/deployments.sh stop <#|host>";      print_list; do_stop "$2";      exit 0 ;;
  remove)    [ $# -ge 2 ] || die "Usage: scripts/deployments.sh remove <#|host>";    print_list; do_remove "$2";    exit 0 ;;
  destroy)   [ $# -ge 2 ] || die "Usage: scripts/deployments.sh destroy <#|host>";   print_list; do_destroy "$2";   exit 0 ;;
  uninstall) [ $# -ge 2 ] || die "Usage: scripts/deployments.sh uninstall <#|host>"; print_list; do_uninstall "$2"; exit 0 ;;
  forget)    [ $# -ge 2 ] || die "Usage: scripts/deployments.sh forget <#|host>";    print_list; do_forget "$2";    exit 0 ;;
  ""|menu)   ;;
  *)         die "Unknown action '$ACTION'. Try: list | stop | remove | destroy | uninstall | forget" ;;
esac

# --- Interactive -------------------------------------------------------------
log "Recorded deployments (${COUNT}):"
print_list

# Only offer destructive actions on a terminal — piped into something, just list.
[ -t 0 ] || exit 0

printf 'Act on which one? (number, or Enter to leave everything alone): '
read -r choice || true
[ -n "${choice:-}" ] || { log "Nothing changed."; exit 0; }
idx="$(resolve_index "$choice")"

echo
echo "  ${HOSTS[$idx]}  (${KINDS[$idx]})"
if [ "${KINDS[$idx]}" = "ios" ]; then
  echo "    u) uninstall the app from the phone"
else
  echo "    s) stop     — containers down, data kept, restartable"
  echo "    r) remove   — containers removed, VOLUMES KEPT (database + model survive)"
  echo "    d) destroy  — containers AND volumes removed: DELETES THE DATABASE"
fi
echo "    f) forget   — drop from this list only; nothing on the host is touched"
echo "    Enter) cancel"
printf 'Choice: '
read -r act || true

case "${act:-}" in
  s) do_stop      "$((idx + 1))" ;;
  r) do_remove    "$((idx + 1))" ;;
  d) do_destroy   "$((idx + 1))" ;;
  u) do_uninstall "$((idx + 1))" ;;
  f) do_forget    "$((idx + 1))" ;;
  *) log "Cancelled — nothing was touched." ;;
esac
