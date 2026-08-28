#!/usr/bin/env bash
# Compile a current client against the oldest API fixture on its contract.
# The committed generated client is restored byte-for-byte before exit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTRACT="$(tr -d '[:space:]' < "$ROOT/VERSION")"
FIXTURE="$ROOT/shared/openapi/compatibility/$CONTRACT.yaml"
TARGET="${1:-}"

[ -f "$FIXTURE" ] || {
  echo "Missing oldest-API fixture for contract $CONTRACT: $FIXTURE" >&2
  exit 1
}

case "$TARGET" in
  web)
    OUTPUT="$ROOT/apps/web/src/app/api-client"
    ;;
  ios)
    OUTPUT="$ROOT/apps/ios/FamilyCFO/FamilyCFOShared/APIClient/Generated"
    ;;
  *)
    echo "usage: scripts/check-client-compatibility.sh web|ios" >&2
    exit 2
    ;;
esac

BACKUP="$(mktemp -d)"
restore_generated_client() {
  rm -rf "$OUTPUT"
  if [ -d "$BACKUP/generated" ]; then
    mkdir -p "$(dirname "$OUTPUT")"
    cp -R "$BACKUP/generated" "$OUTPUT"
  fi
  rm -rf "$BACKUP"
}
trap restore_generated_client EXIT INT TERM

[ ! -d "$OUTPUT" ] || cp -R "$OUTPUT" "$BACKUP/generated"
rm -rf "$OUTPUT"
mkdir -p "$OUTPUT"

if [ "$TARGET" = web ]; then
  (
    cd "$ROOT/apps/web"
    FAMILY_CFO_OPENAPI_CONTRACT="$FIXTURE" npm run generate:client
    npm run build
  )
else
  FAMILY_CFO_OPENAPI_CONTRACT="$FIXTURE" "$ROOT/scripts/generate-swift-client.sh"
  (
    cd "$ROOT/apps/ios/FamilyCFO"
    xcodebuild build \
      -project FamilyCFO.xcodeproj \
      -scheme FamilyCFO \
      -destination 'generic/platform=iOS Simulator' \
      CODE_SIGNING_ALLOWED=NO
  )
fi

echo "$TARGET client is compatible with the oldest API on contract $CONTRACT."
