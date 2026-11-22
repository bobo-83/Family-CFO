#!/usr/bin/env bash
#
# Family CFO — swap the served AI model(s).
#
# Thin delegation (issue #170): argument compatibility + the #167 lock
# entry, then the shared Python engine (services/model-manager) owns
# validation, planning, staging, launch, verification, and recovery. The
# selection REPLACES the current models (vLLM serves one model per
# instance); previously downloaded weights stay in the model_cache volume.
# The database and existing cache are never touched.
#
# Usage:
#   scripts/swap-model.sh [--cluster] <main-model> [vision-model|none]
#
#   scripts/swap-model.sh Qwen/Qwen2.5-14B-Instruct
#   scripts/swap-model.sh Qwen/Qwen2.5-32B-Instruct Qwen/Qwen2.5-VL-3B-Instruct
#   scripts/swap-model.sh Qwen/Qwen2.5-VL-32B-Instruct        # vision-capable main
#   scripts/swap-model.sh Qwen/Qwen2.5-32B-Instruct none      # no photo analysis
#   scripts/swap-model.sh --cluster Qwen/Qwen3-235B-A22B-Instruct-2507-NVFP4
#
# The swap is a journaled apply transaction: the new runtime is verified
# (identity, readiness, synthetic probes) before anything is committed, and
# the previous runtime is restored and verified on any failure. Interrupting
# before the commit stops the swap and restores; after the commit it only
# detaches. Progress lives in the durable journal (.runtime-swap/v2).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -f .env ]; then
  printf '\033[1;31mError:\033[0m .env not found — deploy first (scripts/deploy.sh).\n' >&2
  exit 1
fi

# ADR 0013 / #167: status is not ownership. A standalone invocation execs a
# supervisor that opens O_RDONLY|O_DIRECTORY, flocks, and re-enters this shell
# with the retained fd. It also stops/reaps the entire child group on interrupt.
# A manager invocation already owns the same open file description: validate
# its directory identity and re-flock (a no-op), never trust the marker alone.
if [ "${FAMILY_CFO_SWAP_LOCK_FD+x}" = "x" ]; then
  python3 "$REPO_ROOT/scripts/lib/swap_lock.py" --check "$REPO_ROOT" "$FAMILY_CFO_SWAP_LOCK_FD"
else
  exec python3 "$REPO_ROOT/scripts/lib/swap_lock.py" "$REPO_ROOT" "$@"
fi

# Delegate to the shared engine host-side (ADR 0081 S10): the package source
# path is ABSOLUTE so the separately supervised executor never depends on an
# inherited relative PYTHONPATH, and nothing here imports FastAPI/Pydantic.
export PYTHONPATH="$REPO_ROOT/services/model-manager/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m family_cfo_model_manager.cli "$@"
