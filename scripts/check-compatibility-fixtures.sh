#!/bin/sh
# Verify the current contract has an oldest-API fixture and that fixtures already
# merged on the base branch remain immutable (ADR 0074).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTRACT="$(tr -d '[:space:]' < "$ROOT/VERSION")"
FIXTURE_DIR="shared/openapi/compatibility"
FIXTURE="$FIXTURE_DIR/$CONTRACT.yaml"

if [ ! -f "$ROOT/$FIXTURE" ]; then
  echo "compatibility-fixtures: missing $FIXTURE for contract $CONTRACT" >&2
  exit 1
fi

# GitHub checks out a synthetic PR merge commit, so its first parent is the
# exact base. BASE_REF remains available for local/other CI callers.
BASE="${BASE_REF:-}"
if [ -z "$BASE" ] && git -C "$ROOT" rev-parse --verify HEAD^1 >/dev/null 2>&1; then
  BASE=HEAD^1
fi

if [ -n "$BASE" ]; then
  git -C "$ROOT" ls-tree -r --name-only "$BASE" -- "$FIXTURE_DIR" |
    while IFS= read -r previous; do
      [ -n "$previous" ] || continue
      case "$previous" in *.yaml) ;; *) continue ;; esac
      if [ ! -f "$ROOT/$previous" ] || ! git -C "$ROOT" diff --quiet "$BASE" -- "$previous"; then
        echo "compatibility-fixtures: $previous is immutable;" >&2
        echo "add a fixture for a new contract instead" >&2
        exit 1
      fi
    done
fi

echo "compatibility-fixtures: OK ($FIXTURE)"
