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
# Usage:
#   scripts/patch.sh                 # rebuild api + worker + web (local)
#   scripts/patch.sh web             # only the web container
#   scripts/patch.sh api worker      # a subset
#   TARGET=remote SSH_HOST=box SSH_USER=me scripts/patch.sh web
#
# Environment overrides (same as scripts/deploy.sh):
#   TARGET local|remote  SSH_HOST  SSH_USER  SSH_PORT  SSH_KEY  REMOTE_DIR
#   COMPOSE_FILES (default: -f docker-compose.yml)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml}"

# Services this script is allowed to rebuild. vllm and db are intentionally
# excluded: rebuilding vllm would reload the model, and db must never be
# recreated by a routine patch.
DEFAULT_SERVICES=(api worker web)
PROTECTED_SERVICES="vllm db"

# Requested services = positional args, or the safe default set.
if [ "$#" -gt 0 ]; then
  SERVICES=("$@")
else
  SERVICES=("${DEFAULT_SERVICES[@]}")
fi

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

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
for svc in "${SERVICES[@]}"; do
  for protected in $PROTECTED_SERVICES; do
    if [ "$svc" = "$protected" ]; then
      die "Refusing to rebuild '$svc' — it is protected (would reload the model / recreate the database). Restart it manually if you really need to."
    fi
  done
done

log "Patching services: ${SERVICES[*]}  (vllm + db left running, no volumes removed)"

TARGET="${TARGET:-local}"
[ "$TARGET" = "local" ] || [ "$TARGET" = "remote" ] || die "TARGET must be 'local' or 'remote'."

# =============================================================================
# LOCAL
# =============================================================================
if [ "$TARGET" = "local" ]; then
  command -v docker >/dev/null 2>&1 || die "docker is not installed."
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is required."
  [ -f .env ] || die ".env not found — is this deployment set up? Use scripts/deploy.sh first."

  log "Rebuilding and recreating…"
  # shellcheck disable=SC2086
  docker compose $COMPOSE_FILES up -d --build "${SERVICES[@]}"

  web_tls_port="$(grep -E '^WEB_TLS_PORT=' .env | cut -d= -f2)"; web_tls_port="${web_tls_port:-8443}"
  log "Patched. Dashboard: https://$(detect_host_ip):${web_tls_port}"
  echo "  Verify: scripts/doctor.sh"
  exit 0
fi

# =============================================================================
# REMOTE
# =============================================================================
command -v rsync >/dev/null 2>&1 || die "rsync is required for remote patches."
ask SSH_HOST "Remote host (name or IP)"
[ -n "${SSH_HOST:-}" ] || die "SSH_HOST is required for a remote patch."
ask SSH_USER "SSH user" "${USER:-root}"
ask SSH_PORT "SSH port" "22"
ask SSH_KEY  "SSH private key path (blank = ssh default)" ""
ask REMOTE_DIR "Remote directory" "~/family-cfo"

SSH_OPTS=(-p "$SSH_PORT" -o StrictHostKeyChecking=accept-new)
[ -n "${SSH_KEY:-}" ] && SSH_OPTS+=(-i "$SSH_KEY")
SSH_TARGET="${SSH_USER}@${SSH_HOST}"
RSH="ssh ${SSH_OPTS[*]}"
remote() { ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$@"; }

log "Checking SSH + Docker on ${SSH_TARGET}…"
remote true || die "Cannot SSH to ${SSH_TARGET}."
remote 'command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1' \
  || die "Remote host is missing Docker Engine + Compose v2."
REMOTE_ABS="$(remote "cd ${REMOTE_DIR} 2>/dev/null && pwd")" \
  || die "Remote directory ${REMOTE_DIR} not found — deploy there first with scripts/deploy.sh."

log "Syncing code to ${SSH_TARGET}:${REMOTE_ABS} (excluding .env, data, build artifacts)…"
rsync -az --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.venv' \
  --exclude '__pycache__' --exclude 'dist' --exclude '.angular' \
  --exclude 'data' --exclude '*.db' --exclude '.env' \
  -e "$RSH" "$REPO_ROOT/" "${SSH_TARGET}:${REMOTE_ABS}/"

log "Rebuilding and recreating on the remote host…"
remote "cd ${REMOTE_ABS} && docker compose ${COMPOSE_FILES} up -d --build ${SERVICES[*]}"

web_tls_port="$(remote "grep -E '^WEB_TLS_PORT=' ${REMOTE_ABS}/.env | cut -d= -f2" || true)"
web_tls_port="${web_tls_port:-8443}"
log "Patched. Dashboard: https://${SSH_HOST}:${web_tls_port}"
echo "  Verify: ssh ${SSH_TARGET} 'cd ${REMOTE_ABS} && bash scripts/doctor.sh'"
