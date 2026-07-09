#!/usr/bin/env bash
#
# Family CFO — swap the served AI model(s).
#
# Updates .env and recreates ONLY the runtime + app containers. The selection
# REPLACES the current models (vLLM serves one model per instance); previously
# downloaded weights stay in the model_cache volume. The database and existing
# cache are never touched.
#
# Usage:
#   scripts/swap-model.sh <main-model> [vision-model|none]
#
#   scripts/swap-model.sh Qwen/Qwen2.5-14B-Instruct
#   scripts/swap-model.sh Qwen/Qwen2.5-32B-Instruct Qwen/Qwen2.5-VL-3B-Instruct
#   scripts/swap-model.sh Qwen/Qwen2.5-VL-32B-Instruct        # vision-capable main
#   scripts/swap-model.sh Qwen/Qwen2.5-32B-Instruct none      # no photo analysis
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml}"

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$#" -ge 1 ] || die "usage: scripts/swap-model.sh <main-model> [vision-model|none]"
MAIN="$1"
VISION="${2:-}"

[ -f .env ] || die ".env not found — deploy first (scripts/deploy.sh)."

# Tool parser by model family (must match what vLLM expects).
parser_for() {
  case "$1" in
    *Llama*|*llama*) echo "llama3_json" ;;
    *) echo "hermes" ;;
  esac
}

# A "VL" model sees photos itself — no separate describer needed.
is_vision_model() { case "$1" in *-VL-*|*vl-*) return 0 ;; *) return 1 ;; esac; }

set_env() { # set_env KEY VALUE — update in place or append
  local key="$1" value="$2"
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

set_env VLLM_MODEL "$MAIN"
set_env VLLM_TOOL_PARSER "$(parser_for "$MAIN")"

scale_args=()
if is_vision_model "$MAIN"; then
  log "Main model '$MAIN' is vision-capable — it will describe photos itself."
  set_env FAMILY_CFO_AI_SUPPORTS_VISION "true"
  set_env FAMILY_CFO_AI_VISION_ENABLED "false"
  scale_args=(--scale vllm-vision=0)
  [ -n "$VISION" ] && [ "$VISION" != "none" ] && \
    die "Do not pass a vision model with a vision-capable main."
elif [ "$VISION" = "none" ]; then
  log "Photo analysis disabled — attached photos will get a 'not analyzed' warning."
  set_env FAMILY_CFO_AI_SUPPORTS_VISION "false"
  set_env FAMILY_CFO_AI_VISION_ENABLED "false"
  scale_args=(--scale vllm-vision=0)
else
  VISION="${VISION:-Qwen/Qwen2.5-VL-7B-Instruct}"
  log "Vision describer: $VISION"
  set_env FAMILY_CFO_AI_SUPPORTS_VISION "false"
  set_env FAMILY_CFO_AI_VISION_ENABLED "true"
  set_env VLLM_VISION_MODEL "$VISION"
fi

log "Recreating runtime + app containers (new models download once; DB untouched)…"
# shellcheck disable=SC2086
docker compose $COMPOSE_FILES up -d "${scale_args[@]}" vllm vllm-vision api worker

log "Done. Track model loading with: docker compose $COMPOSE_FILES logs -f vllm"
echo "  Verify: scripts/doctor.sh   (or the chat page's status banner)"
