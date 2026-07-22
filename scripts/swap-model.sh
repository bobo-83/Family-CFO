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
    # Qwen3.5/3.6 and Qwen3-Coder emit XML-style tool calls; hermes silently
    # drops them (tool calls leak into the answer as <function=...> text).
    *Qwen3.6*|*Qwen3.5*|*Qwen3-Coder*|*qwen3.6*|*qwen3.5*|*qwen3-coder*) echo "qwen3_coder" ;;
    *) echo "hermes" ;;
  esac
}

# Optional model-specific vLLM flags (ADR 0060). Qwen3.6-A3B checkpoints ship
# a 1-layer MTP head -> lossless speculative decoding, ~1.35x decode. MUST be
# cleared for models without the head or vLLM crash-loops on startup.
extra_args_for() {
  case "$1" in
    *Qwen3.6-*A3B*|*qwen3.6-*a3b*)
      echo "--speculative-config '{\"method\":\"qwen3_5_mtp\",\"num_speculative_tokens\":2}'" ;;
    *) echo "" ;;
  esac
}

# A "VL" model sees photos itself — no separate describer needed.
is_vision_model() { case "$1" in *-VL-*|*vl-*) return 0 ;; *) return 1 ;; esac; }

# M55: estimate a model's weight footprint (GB) from its name — params count
# times a quantization factor (same heuristics as the API's fit planner).
# Prints 0 when the name carries no parameter count.
estimate_weights_gb() {
  python3 - "$1" <<'PY'
import re, sys
model = sys.argv[1]
name = model.rsplit("/", 1)[-1]
match = re.search(r"(\d+(?:\.\d+)?)\s*[bB](?:[-_.]|$)", name)
if not match:
    print(0)
    raise SystemExit
params = float(match.group(1))
lower = model.lower()
if any(m in lower for m in ("awq", "gptq", "int4", "4bit", "4-bit")):
    factor = 0.65
elif any(m in lower for m in ("fp8", "int8", "8bit", "8-bit")):
    factor = 1.0
else:
    factor = 2.1
print(round(params * factor))
PY
}

total_memory_gb() {
  awk '/MemTotal:/ {printf "%d", $2/1024/1024}' /proc/meminfo
}

# Fraction of total memory a model needs: weights + runtime reserve, with a
# little rounding headroom. Prints e.g. "0.72".
fraction_for() { # fraction_for WEIGHTS_GB RESERVE_GB TOTAL_GB
  python3 -c "import sys; w,r,t=map(float,sys.argv[1:4]); print(f'{min(0.95,(w+r)/t + 0.01):.2f}')" "$1" "$2" "$3"
}

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
set_env VLLM_EXTRA_ARGS "$(extra_args_for "$MAIN")"

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

# --- M55: automatic GPU-fraction budgeting -----------------------------------
# Manual fractions caused three crash-loops (a fraction sized for one model
# starves the next). Compute them from the applied models; refuse impossible
# combinations BEFORE touching containers.
TOTAL_GB="$(total_memory_gb)"
MAIN_WEIGHTS="$(estimate_weights_gb "$MAIN")"
MAIN_RESERVE=10   # 32k-context KV cache + runtime overhead
VISION_RESERVE=5  # 8k-context describer overhead

if [ "$TOTAL_GB" -gt 0 ] && [ "$MAIN_WEIGHTS" -gt 0 ]; then
  MAIN_FRACTION="$(fraction_for "$MAIN_WEIGHTS" "$MAIN_RESERVE" "$TOTAL_GB")"
  VISION_FRACTION="0.00"
  if [ -n "$VISION" ] && [ "$VISION" != "none" ] && ! is_vision_model "$MAIN"; then
    VISION_WEIGHTS="$(estimate_weights_gb "$VISION")"
    if [ "$VISION_WEIGHTS" -gt 0 ]; then
      VISION_FRACTION="$(fraction_for "$VISION_WEIGHTS" "$VISION_RESERVE" "$TOTAL_GB")"
    else
      log "Cannot size '$VISION' from its name — keeping the current vision fraction."
      VISION_FRACTION="$(grep -E '^VLLM_VISION_GPU_FRACTION=' .env | cut -d= -f2)"
      VISION_FRACTION="${VISION_FRACTION:-0.20}"
    fi
  fi
  COMBINED_OK="$(python3 -c "print(1 if float('$MAIN_FRACTION') + float('$VISION_FRACTION') <= 0.92 else 0)")"
  if [ "$COMBINED_OK" != "1" ]; then
    die "Won't fit: '$MAIN' (~${MAIN_WEIGHTS}GB + ${MAIN_RESERVE}GB reserve) plus the vision model need more than 92% of ${TOTAL_GB}GB. Pick a smaller photo model (e.g. Qwen/Qwen2.5-VL-7B-Instruct) or 'none'."
  fi
  log "Memory budget: main fraction ${MAIN_FRACTION} (~${MAIN_WEIGHTS}GB weights), vision fraction ${VISION_FRACTION} of ${TOTAL_GB}GB."
  set_env VLLM_GPU_FRACTION "$MAIN_FRACTION"
  if [ "$VISION_FRACTION" != "0.00" ]; then
    set_env VLLM_VISION_GPU_FRACTION "$VISION_FRACTION"
  fi
else
  log "Cannot size '$MAIN' from its name — keeping the current GPU fractions."
fi

log "Recreating runtime + app containers (new models download once; DB untouched)…"
# shellcheck disable=SC2086
docker compose $COMPOSE_FILES up -d "${scale_args[@]}" vllm vllm-vision api worker

log "Done. Track model loading with: docker compose $COMPOSE_FILES logs -f vllm"
echo "  Verify: scripts/doctor.sh   (or the chat page's status banner)"
