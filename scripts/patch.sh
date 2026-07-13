#!/usr/bin/env bash
#
# Family CFO — patch a running deployment.
#
# Rebuilds and recreates ONLY the application containers (api, worker, web) on a
# local or remote host. It never touches the vllm or db services and never
# removes a volume, so the AI model (in the model_cache volume) is NOT
# re-downloaded and the database is left alone. The api container applies any
# new database migrations on startup (additive), so a schema change ships with
# an `api` patch automatically.
#
# `ios` is a target too — it builds and installs the iPhone app onto a paired
# device over WiFi (scripts/deploy-ios.sh). It is NOT in the default set: you
# patch the phone when you mean to. But it composes with the container targets,
# which matters because an iOS change that needs an API or web change must ship
# BOTH halves — a phone talking to a box that lacks its endpoint is the failure
# this composition exists to prevent:
#
#   scripts/patch.sh api web ios
#
# The two halves run in different places: containers are rebuilt wherever the
# stack lives (local or remote over SSH), while `ios` always builds on the Mac
# you are sitting at, because that is where Xcode is. So if the box is remote,
# run the container half against it and the `ios` half from the Mac.
#
# Usage:
#   scripts/patch.sh                 # rebuild api + worker + web (local)
#   scripts/patch.sh web             # only the web container
#   scripts/patch.sh api worker      # a subset
#   scripts/patch.sh ios             # only the iPhone app (over WiFi)
#   scripts/patch.sh web ios         # the box's web tier AND the phone
#   SSH_HOST=box scripts/patch.sh web            # a remote box (TARGET inferred)
#   SSH_HOST="box1 box2" scripts/patch.sh web    # several boxes, in order
#
# Choosing the destination — the two halves differ, deliberately:
#   * The SERVER is DECLARED, never discovered. TARGET=local is this machine;
#     SSH_HOST names a remote box (and setting it implies TARGET=remote, so you
#     can't silently rebuild containers on your laptop by forgetting TARGET).
#     SSH_HOST may list several hosts — they are patched one at a time, in
#     order, stopping at the first failure.
#   * The PHONE is DISCOVERED, and never guessed at: with exactly one connected
#     device it is used, with several the run refuses until you name one with
#     IOS_DEVICE. See scripts/deploy-ios.sh.
#
# Environment overrides (same as scripts/deploy.sh):
#   TARGET local|remote  SSH_HOST (one or more)  SSH_USER  SSH_PORT  SSH_KEY  REMOTE_DIR
#   COMPOSE_FILES (default: -f docker-compose.yml)
#   iOS-specific: IOS_DEVICE  IOS_CONFIG  IOS_TEST  NO_LAUNCH  (see scripts/deploy-ios.sh)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml}"

# Services this script is allowed to rebuild. vllm and db are intentionally
# excluded: rebuilding vllm would reload the model, and db must never be
# recreated by a routine patch.
DEFAULT_SERVICES=(api worker web)
PROTECTED_SERVICES="vllm db"

# Requested targets = positional args, or the safe default set.
if [ "$#" -gt 0 ]; then
  REQUESTED=("$@")
else
  REQUESTED=("${DEFAULT_SERVICES[@]}")
fi

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# How a target is routed: `ios` is the one reserved word and means the phone
# (scripts/deploy-ios.sh, built here on the Mac). Everything else must name a
# real service in the compose file, and is rebuilt on whichever host the stack
# lives on. Anything else is a typo, and is rejected below rather than being
# handed to Docker to fail on later.
PATCH_IOS=0
SERVICES=()
for target in "${REQUESTED[@]}"; do
  if [ "$target" = "ios" ]; then
    PATCH_IOS=1
  else
    SERVICES+=("$target")
  fi
done

detect_host_ip() {
  local ip
  ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
  [ -z "$ip" ] && ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "${ip:-localhost}"
}

ask() {
  local __var="$1" __prompt="$2" __default="${3:-}" __reply
  if [ -n "${!__var:-}" ]; then return; fi
  if [ -n "$__default" ]; then
    read -r -p "$__prompt [$__default]: " __reply || true
    printf -v "$__var" '%s' "${__reply:-$__default}"
  else
    read -r -p "$__prompt: " __reply || true
    printf -v "$__var" '%s' "$__reply"
  fi
}

# Refuse to patch a protected service (the whole point is to leave them alone).
for svc in ${SERVICES[@]+"${SERVICES[@]}"}; do
  for protected in $PROTECTED_SERVICES; do
    if [ "$svc" = "$protected" ]; then
      die "Refusing to rebuild '$svc' — it is protected (would reload the model / recreate the database). Restart it manually if you really need to."
    fi
  done
done

# Reject a name that is neither `ios` nor a real compose service, so a typo
# fails here with something readable instead of surfacing as a Docker error
# three steps later (or, worse, as a no-op that looks like success).
validate_services() { # validate_services <known-services...>
  local known="$*" svc
  for svc in ${SERVICES[@]+"${SERVICES[@]}"}; do
    case " $known " in
      *" $svc "*) ;;
      *) die "Unknown target '$svc'. Valid targets: ios (the iPhone app) or a compose service — ${known}." ;;
    esac
  done
  log "Patching services: ${SERVICES[*]}  (vllm + db left running, no volumes removed)"
}

patch_ios() {
  log "Patching the iPhone app over WiFi…"
  bash "$REPO_ROOT/scripts/deploy-ios.sh"
}

# Phone-only: nothing to do with Docker, so don't demand it (this may well be a
# Mac with no stack on it at all).
if [ "$PATCH_IOS" = "1" ] && [ "${#SERVICES[@]}" -eq 0 ]; then
  patch_ios
  exit 0
fi

# Which machine gets patched. Unlike the phone, servers are DECLARED, never
# discovered — so the danger isn't ambiguity, it's silently patching the wrong
# box. Naming SSH_HOST means you meant a remote host, so honour that rather than
# quietly rebuilding containers on the laptop you happen to be sitting at.
if [ -z "${TARGET:-}" ] && [ -n "${SSH_HOST:-}" ]; then
  TARGET="remote"
fi
TARGET="${TARGET:-local}"
[ "$TARGET" = "local" ] || [ "$TARGET" = "remote" ] || die "TARGET must be 'local' or 'remote'."

# =============================================================================
# LOCAL
# =============================================================================
if [ "$TARGET" = "local" ]; then
  command -v docker >/dev/null 2>&1 || die "docker is not installed."
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is required."
  [ -f .env ] || die ".env not found — is this deployment set up? Use scripts/deploy.sh first."

  # shellcheck disable=SC2086
  validate_services "$(docker compose $COMPOSE_FILES config --services 2>/dev/null | tr '\n' ' ')"

  log "Rebuilding and recreating…"
  # shellcheck disable=SC2086
  docker compose $COMPOSE_FILES up -d --build "${SERVICES[@]}"

  web_tls_port="$(grep -E '^WEB_TLS_PORT=' .env | cut -d= -f2)"; web_tls_port="${web_tls_port:-8443}"
  log "Patched. Dashboard: https://$(detect_host_ip):${web_tls_port}"
  echo "  Verify: scripts/doctor.sh"
  # The phone goes last: it must never come up against a box that doesn't yet
  # have the endpoint it was built to call.
  [ "$PATCH_IOS" = "1" ] && patch_ios
  exit 0
fi

# =============================================================================
# REMOTE
# =============================================================================
command -v rsync >/dev/null 2>&1 || die "rsync is required for remote patches."
ask SSH_HOST "Remote host(s) — name or IP, space- or comma-separated for several"
[ -n "${SSH_HOST:-}" ] || die "SSH_HOST is required for a remote patch."
ask SSH_USER "SSH user" "${USER:-root}"
ask SSH_PORT "SSH port" "22"
ask SSH_KEY  "SSH private key path (blank = ssh default)" ""
ask REMOTE_DIR "Remote directory" "~/family-cfo"

# SSH_HOST may name several boxes: SSH_HOST="box1 box2" or "box1,box2". They are
# patched one at a time, in order, and the run STOPS at the first failure — a
# half-patched fleet is easier to reason about than one that kept going after a
# box refused, and you can re-run for the rest.
IFS=', ' read -r -a SSH_HOSTS <<< "$SSH_HOST"
[ "${#SSH_HOSTS[@]}" -gt 0 ] || die "SSH_HOST is required for a remote patch."

patch_remote_host() { # patch_remote_host <host>
  local host="$1"
  local ssh_opts=(-p "$SSH_PORT" -o StrictHostKeyChecking=accept-new)
  [ -n "${SSH_KEY:-}" ] && ssh_opts+=(-i "$SSH_KEY")
  local ssh_target="${SSH_USER}@${host}"
  local rsh="ssh ${ssh_opts[*]}"
  remote() { ssh "${ssh_opts[@]}" "$ssh_target" "$@"; }

  log "── ${ssh_target} ─────────────────────────────────────────"
  log "Checking SSH + Docker on ${ssh_target}…"
  remote true || die "Cannot SSH to ${ssh_target}."
  remote 'command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1' \
    || die "${ssh_target} is missing Docker Engine + Compose v2."

  local remote_abs
  remote_abs="$(remote "cd ${REMOTE_DIR} 2>/dev/null && pwd")" \
    || die "Remote directory ${REMOTE_DIR} not found on ${ssh_target} — deploy there first with scripts/deploy.sh."

  validate_services "$(remote "cd ${remote_abs} && docker compose ${COMPOSE_FILES} config --services 2>/dev/null" | tr '\n' ' ')"

  log "Syncing code to ${ssh_target}:${remote_abs} (excluding .env, data, build artifacts)…"
  rsync -az --delete \
    --exclude '.git' --exclude 'node_modules' --exclude '.venv' \
    --exclude '__pycache__' --exclude 'dist' --exclude '.angular' \
    --exclude 'data' --exclude '*.db' --exclude '.env' \
    -e "$rsh" "$REPO_ROOT/" "${ssh_target}:${remote_abs}/"

  log "Rebuilding and recreating on ${ssh_target}…"
  remote "cd ${remote_abs} && docker compose ${COMPOSE_FILES} up -d --build ${SERVICES[*]}"

  local port
  port="$(remote "grep -E '^WEB_TLS_PORT=' ${remote_abs}/.env | cut -d= -f2" || true)"
  port="${port:-8443}"
  log "Patched ${host}. Dashboard: https://${host}:${port}"
  echo "  Verify: ssh ${ssh_target} 'cd ${remote_abs} && bash scripts/doctor.sh'"
}

log "Remote hosts to patch: ${SSH_HOSTS[*]}"
for host in "${SSH_HOSTS[@]}"; do
  patch_remote_host "$host"
done

# The phone goes last, and builds here on the Mac — not on any remote box, which
# has no Xcode. It is built ONCE regardless of how many servers were patched;
# the phone talks to whichever box it was paired with.
[ "$PATCH_IOS" = "1" ] && patch_ios
