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
#   - optionally runs the Playwright browser suite against that same live stack
# then tears everything down.
#
# vLLM is intentionally NOT booted: it needs a GPU and a multi-GB model
# download, which a CI/smoke run can't assume. AI enablement is disabled for
# this run so the deterministic path answers (the agentic path has its own
# stubbed-runtime tests). A real GPU-backed model boot is an operator check
# (scripts/doctor.sh after `docker compose up`).
#
# Usage: scripts/e2e-deploy-test.sh
#        E2E_PLAYWRIGHT=1 scripts/e2e-deploy-test.sh
#        KEEP=1 scripts/e2e-deploy-test.sh  # skip teardown for debugging
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PROJECT="familycfo_e2e_$$"
API_PORT="${E2E_API_PORT:-18099}"
ENV_FILE="$(mktemp)"
ARTIFACT_DIR="${E2E_ARTIFACT_DIR:-${REPO_ROOT}/artifacts/deploy-e2e}"
STACK_LOG="${ARTIFACT_DIR}/docker-compose.log"
DC=(docker compose -p "$PROJECT" -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.e2e.yml --env-file "$ENV_FILE")
rm -f "$STACK_LOG"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mE2E FAILED:\033[0m %s\n' "$*" >&2; exit 1; }

cleanup() {
  status=$?
  trap - EXIT

  if [ "$status" -ne 0 ]; then
    mkdir -p "$ARTIFACT_DIR"
    log "Capturing stack diagnostics before teardown…"
    {
      printf 'Docker Compose status\n=====================\n'
      "${DC[@]}" ps -a
      printf '\nDocker Compose logs\n===================\n'
      "${DC[@]}" logs --no-color --timestamps
    } >"$STACK_LOG" 2>&1 || true
    log "Stack diagnostics saved to $STACK_LOG"
  fi

  if [ "${KEEP:-0}" != "1" ]; then
    log "Tearing down…"
    if ! "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1; then
      printf '\033[1;31mE2E FAILED:\033[0m Docker Compose teardown failed\n' >&2
      status=1
    fi
  else
    log "KEEP=1 — leaving the stack up (project $PROJECT). Tear down with: ${DC[*]} down -v"
  fi
  rm -f "$ENV_FILE"
  exit "$status"
}
trap cleanup EXIT

command -v docker >/dev/null 2>&1 || fail "docker not installed"
docker compose version >/dev/null 2>&1 || fail "docker compose v2 required"
if [ "${E2E_PLAYWRIGHT:-0}" = "1" ]; then
  command -v node >/dev/null 2>&1 || fail "node not installed (required for Playwright mode)"
  command -v npm >/dev/null 2>&1 || fail "npm not installed (required for Playwright mode)"
fi

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

log "Building the core stack images (no vLLM)…"
log "Disk usage before image build:"
df -h / || true
"${DC[@]}" build api worker web
log "Disk usage after image build:"
df -h / || true

log "Starting the core stack…"
# --no-deps is intentional: api declares vllm as a production start-order
# dependency, and some Compose versions pull that multi-GB GPU image even with
# --scale vllm=0. Every service needed here is named explicitly; the API and
# worker entrypoints wait for PostgreSQL, and the health loop below gates use.
"${DC[@]}" up -d --no-build --no-deps db api worker web

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
elif [ "${E2E_PLAYWRIGHT:-0}" = "1" ]; then
  fail "web TLS port is not published; Playwright cannot run"
else
  log "(web port not published in this run — skipped dashboard curl)"
fi

if [ "${E2E_PLAYWRIGHT:-0}" = "1" ]; then
  log "Running Playwright against the isolated TLS stack…"
  (
    cd apps/web
    CI=true E2E_BASE_URL="https://localhost:${web_port}" npm run e2e
  ) || fail "Playwright browser tests failed"
  log "Playwright browser tests passed."
fi

printf '\n\033[1;32mE2E PASSED\033[0m — deployment smoke and requested browser checks succeeded.\n'
