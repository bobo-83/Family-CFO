#!/usr/bin/env bash
# Regenerate the committed Swift API client from the shared OpenAPI contract
# (contract-first, same discipline as the Angular client — ADR 0005 / M83).
#
#   scripts/generate-swift-client.sh          # regenerate in place
#   scripts/generate-swift-client.sh --check  # fail if the committed client is stale
#
# Requires a Swift toolchain (Xcode or swift.org). The generator version is
# pinned in apps/ios/openapi-generator/Package.swift.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL_DIR="$REPO_ROOT/apps/ios/openapi-generator"
CONTRACT="$REPO_ROOT/shared/openapi/family-cfo.v1.yaml"
OUTPUT_DIR="$REPO_ROOT/apps/ios/FamilyCFO/FamilyCFOShared/APIClient/Generated"

swift build --package-path "$TOOL_DIR" -c release --product swift-openapi-generator >/dev/null

"$TOOL_DIR/.build/release/swift-openapi-generator" generate \
  --config "$TOOL_DIR/openapi-generator-config.yaml" \
  --output-directory "$OUTPUT_DIR" \
  "$CONTRACT"

if [[ "${1:-}" == "--check" ]]; then
  if ! git -C "$REPO_ROOT" diff --exit-code -- "$OUTPUT_DIR"; then
    echo "Generated Swift client is stale — run scripts/generate-swift-client.sh and commit." >&2
    exit 1
  fi
  echo "Swift client is up to date with the OpenAPI contract."
fi
