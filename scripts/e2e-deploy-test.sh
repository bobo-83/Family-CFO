#!/usr/bin/env bash
#
# Family CFO — real deployment end-to-end test.
#
# Builds the images and boots the CORE stack (db + api + worker + web) with
# Docker Compose in an isolated project, then exercises it over HTTP:
#   - waits for the API to become healthy (migrations applied on boot)
#   - logs in as the seeded demo owner
#   - sends a chat message and asserts a grounded recommendation comes back
#   - checks the web tier serves the dashboard
# then tears everything down.
#
# vLLM is intentionally NOT booted: it needs a GPU and a multi-GB model
# download, which a CI/smoke run can't assume. AI enablement is disabled for
# this run so the deterministic path answers (the agentic path has its own
# stubbed-runtime tests). A real GPU-backed model boot is an operator check
# (scripts/doctor.sh after `docker compose up`).
#
# Usage: scripts/e2e-deploy-test.sh   [KEEP=1 to skip teardown for debugging]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PROJECT="familycfo_e2e_$$"
API_PORT="${E2E_API_PORT:-18099}"
ENV_FILE="$(mktemp)"
DC=(docker compose -p "$PROJECT" -f docker-compose.yml -f docker-compose.dev.yml --env-file "$ENV_FILE")

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mE2E FAILED:\033[0m %s\n' "$*" >&2; exit 1; }

cleanup() {
  if [ "${KEEP:-0}" != "1" ]; then
    log "Tearing down…"
    "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  else
    log "KEEP=1 — leaving the stack up (project $PROJECT). Tear down with: ${DC[*]} down -v"
  fi
  rm -f "$ENV_FILE"
}
trap cleanup EXIT

command -v docker >/dev/null 2>&1 || fail "docker not installed"
docker compose version >/dev/null 2>&1 || fail "docker compose v2 required"

# Ephemeral, self-contained env: random secrets, AI off, API published locally.
# The web ports must not collide with a production stack on the same host
# (8080/8443 are the compose defaults), so the e2e run gets its own.
WEB_PORT="${E2E_WEB_PORT:-18080}"
WEB_TLS_PORT="${E2E_WEB_TLS_PORT:-18443}"
cat > "$ENV_FILE" <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 16 2>/dev/null || echo e2epw$RANDOM$RANDOM)
FAMILY_CFO_BACKUP_ENCRYPTION_KEY=$(openssl rand -base64 32 2>/dev/null || echo e2ekeyE2EkeyE2EkeyE2EkeyE2Ekey00=)
FAMILY_CFO_AI_ENABLED=false
API_PORT=${API_PORT}
WEB_PORT=${WEB_PORT}
WEB_TLS_PORT=${WEB_TLS_PORT}
EOF

BASE="http://localhost:${API_PORT}/api/v1"

log "Building and starting the core stack (no vLLM)…"
# --scale vllm=0 keeps the GPU/model service down even though api depends_on it.
"${DC[@]}" up -d --build --scale vllm=0 db api worker web

log "Waiting for the API to become healthy…"
deadline=$(( $(date +%s) + 180 ))
until curl -fsS "${BASE}/health" >/dev/null 2>&1; do
  [ "$(date +%s)" -gt "$deadline" ] && { "${DC[@]}" logs api | tail -40; fail "API did not become healthy in time"; }
  sleep 3
done
log "API healthy."

log "Seeding the demo household…"
"${DC[@]}" exec -T api python -c "
from family_cfo_api.db import create_database_engine
from family_cfo_api.config import get_settings
from family_cfo_api import fixtures
engine = create_database_engine(get_settings().database_url)
fixtures.seed_demo_household(engine)
print('seeded')
" >/dev/null || fail "demo seed failed"

log "Logging in as the demo owner…"
token="$(curl -fsS -X POST "${BASE}/auth/sessions" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${DEMO_EMAIL:-demo@family-cfo.local}\",\"password\":\"demo-password-123\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')" \
  || fail "login failed"
[ -n "$token" ] || fail "no access token returned"

log "Sending a chat message…"
answer="$(curl -fsS -X POST "${BASE}/chat/messages" \
  -H "Authorization: Bearer ${token}" -H 'Content-Type: application/json' \
  -d '{"message":"How are we doing?"}' \
  | python3 -c 'import sys,json; r=json.load(sys.stdin)["recommendation"]; assert r["answer"]; assert r["calculation_refs"]; print(r["answer"][:60])')" \
  || fail "chat request did not return a grounded recommendation"
log "Chat OK: ${answer}…"

log "Checking the dashboard (web tier)…"
web_port="$("${DC[@]}" port web 443 2>/dev/null | sed 's/.*://')"
if [ -n "$web_port" ]; then
  curl -ksSf -o /dev/null "https://localhost:${web_port}/" || fail "dashboard not served"
  log "Dashboard served on https://localhost:${web_port}"
else
  log "(web port not published in this run — skipped dashboard curl)"
fi

printf '\n\033[1;32mE2E PASSED\033[0m — build + boot + login + grounded chat all succeeded.\n'
