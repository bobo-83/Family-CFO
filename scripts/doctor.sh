#!/usr/bin/env bash
#
# Family CFO — deployment doctor.
#
# A read-only health report for a running stack: checks Docker, each container,
# the API/DB/web/vLLM endpoints, disk space, and the GPU. Makes no changes.
# Exit code is non-zero if any REQUIRED check fails (AI checks are advisory
# unless FAMILY_CFO_AI_ENABLED=true).
#
# Usage:
#   scripts/doctor.sh                 # inspect the local stack
#   COMPOSE_FILES="-f docker-compose.yml" scripts/doctor.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml}"
# shellcheck disable=SC2086
DC="docker compose $COMPOSE_FILES"

green='\033[1;32m'; red='\033[1;31m'; yellow='\033[1;33m'; dim='\033[2m'; reset='\033[0m'
fail_count=0
warn_count=0

pass() { printf "  ${green}✔${reset} %s\n" "$*"; }
fail() { printf "  ${red}x${reset} %s\n" "$*"; fail_count=$((fail_count + 1)); }
warn() { printf "  ${yellow}!${reset} %s\n" "$*"; warn_count=$((warn_count + 1)); }
section() { printf "\n${dim}== %s ==${reset}\n" "$*"; }

# Read a value from .env (best effort).
env_val() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-; }

# LAN address other machines use to reach this host (published ports bind 0.0.0.0).
detect_host_ip() {
  local ip
  ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
  [ -z "$ip" ] && ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "${ip:-localhost}"
}

svc_running() { [ "$($DC ps -q "$1" 2>/dev/null | wc -l)" -gt 0 ] && \
  [ -n "$($DC ps --status running -q "$1" 2>/dev/null)" ]; }

# Run a command inside the api container (has curl-free python + psql tools).
in_api() { $DC exec -T api "$@" 2>/dev/null; }

section "Host prerequisites"
if command -v docker >/dev/null 2>&1; then pass "docker present ($(docker --version | awk '{print $3}' | tr -d ,))"
else fail "docker not installed"; fi
if docker compose version >/dev/null 2>&1; then pass "docker compose v2 present"
else fail "docker compose v2 missing"; fi
if [ -f .env ]; then pass ".env present"
else warn ".env missing (using compose defaults / may fail on POSTGRES_PASSWORD)"; fi

ai_enabled="$(env_val FAMILY_CFO_AI_ENABLED)"; ai_enabled="${ai_enabled:-true}"

section "Containers"
for svc in db api worker web; do
  if svc_running "$svc"; then pass "$svc running"; else fail "$svc not running"; fi
done
if svc_running vllm; then pass "vllm running"
elif [ "$ai_enabled" = "true" ]; then warn "vllm not running but AI is enabled"
else pass "vllm off (AI disabled)"; fi

section "Endpoints"
# API health (from inside the api container, so no host port needed).
if in_api python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health').status==200 else 1)"; then
  pass "API /api/v1/health OK"
else fail "API health check failed"; fi

# DB reachable via pg_isready in the api image.
pg_user="$(env_val POSTGRES_USER)"; pg_user="${pg_user:-family_cfo}"
if in_api pg_isready -h db -p 5432 -U "$pg_user" >/dev/null; then pass "PostgreSQL accepting connections"
else fail "PostgreSQL not ready"; fi

# Web tier (HTTPS, self-signed — allow insecure).
web_tls_port="$(env_val WEB_TLS_PORT)"; web_tls_port="${web_tls_port:-8443}"
host_ip="$(detect_host_ip)"
if command -v curl >/dev/null 2>&1; then
  if curl -ksSf -o /dev/null "https://localhost:${web_tls_port}/"; then
    pass "Dashboard reachable at https://${host_ip}:${web_tls_port} (LAN) / https://localhost:${web_tls_port}"
  else warn "Dashboard not reachable on https://localhost:${web_tls_port} (check WEB_TLS_PORT / firewall)"; fi
else warn "curl not on host — skipped dashboard check"; fi

# vLLM model endpoint (only meaningful when AI is on).
if svc_running vllm; then
  if in_api python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://vllm:8000/v1/models').status==200 else 1)"; then
    model="$(in_api python -c "import urllib.request,json; print(json.load(urllib.request.urlopen('http://vllm:8000/v1/models'))['data'][0]['id'])" 2>/dev/null)"
    pass "vLLM serving${model:+ (model: $model)}"
  else warn "vLLM up but /v1/models not ready yet (model still loading?) — see: $DC logs -f vllm"; fi
fi

section "Resources"
avail_kb="$(df -Pk . | awk 'NR==2 {print $4}')"
avail_gb=$(( avail_kb / 1024 / 1024 ))
if [ "$avail_gb" -ge 50 ]; then pass "Disk free: ${avail_gb} GB"
elif [ "$avail_gb" -ge 20 ]; then warn "Disk free: ${avail_gb} GB (models can be tens of GB — consider more)"
else fail "Disk free: ${avail_gb} GB (low; model downloads may fail)"; fi

if command -v nvidia-smi >/dev/null 2>&1; then
  gpu="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)"
  [ -n "$gpu" ] && pass "GPU: $gpu" || warn "nvidia-smi present but no GPU reported"
elif [ "$ai_enabled" = "true" ]; then
  warn "No nvidia-smi on host — AI is enabled but a GPU/NVIDIA toolkit was not detected"
else pass "No GPU needed (AI disabled)"; fi

section "Summary"
if [ "$fail_count" -eq 0 ] && [ "$warn_count" -eq 0 ]; then
  printf "${green}All checks passed.${reset}\n"; exit 0
elif [ "$fail_count" -eq 0 ]; then
  printf "${yellow}%d warning(s), no failures.${reset}\n" "$warn_count"; exit 0
else
  printf "${red}%d failure(s), %d warning(s).${reset}\n" "$fail_count" "$warn_count"; exit 1
fi
