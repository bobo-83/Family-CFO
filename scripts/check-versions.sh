#!/bin/sh
# Version scheme guard (ADR 0074): fail if /VERSION or a component BUILD is
# malformed. Run in CI on every PR.
#
# This exists because the only version check the repo had ran at TAG time, in
# .github/workflows/release.yml — long after a bad value had merged, shipped,
# and started being reported at /health. The shapes below are load-bearing:
#
#   /VERSION            MAJOR.MINOR, and nothing else. A third field here would
#                       be read as a build by some callers and as part of the
#                       contract by others, and `_version_tuple` in
#                       backup_processing does not normalize length, so the two
#                       readings compare unequal in a way nothing surfaces.
#   apps/*/BUILD        a bare non-negative integer. Composition is a literal
#                       "contract.build" string join, so anything else produces
#                       a version that is never equal to itself.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPONENTS="api web ios"

fail=0
note() { printf 'check-versions: %s\n' "$1" >&2; fail=1; }

version_file="$ROOT/VERSION"
if [ ! -f "$version_file" ]; then
  note "no /VERSION file"
else
  contract="$(tr -d '[:space:]' < "$version_file")"
  if ! printf '%s' "$contract" | grep -qE '^[0-9]+\.[0-9]+$'; then
    note "/VERSION is '${contract}' — must be exactly MAJOR.MINOR (the contract), with no build field"
  fi
fi

for component in $COMPONENTS; do
  build_file="$ROOT/apps/$component/BUILD"
  if [ ! -f "$build_file" ]; then
    note "no apps/${component}/BUILD file"
    continue
  fi
  build="$(tr -d '[:space:]' < "$build_file")"
  if ! printf '%s' "$build" | grep -qE '^[0-9]+$'; then
    note "apps/${component}/BUILD is '${build}' — must be a bare non-negative integer"
  fi
done

if [ "$fail" -ne 0 ]; then
  printf 'check-versions: FAILED — see docs/adr/0074-per-component-build-numbers.md\n' >&2
  exit 1
fi
printf 'check-versions: OK\n'
