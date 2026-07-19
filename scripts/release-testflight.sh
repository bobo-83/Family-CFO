#!/usr/bin/env bash
#
# Family CFO — archive the iPhone app and upload it to TestFlight.
#
# Unlike scripts/deploy-ios.sh (a dev build straight onto YOUR phone) and
# deploy-ios-ota.sh (ad-hoc install from the box), this makes an App Store
# distribution build and pushes it to App Store Connect, so household members
# install from a TestFlight link with no cable and get updates automatically.
#
#   scripts/release-testflight.sh
#
# Requires (all set once in the gitignored .deploy.env — ADR 0030 keeps them
# out of the repo):
#   IOS_TEAM_ID       your Apple Developer team id
#   ASC_KEY_ID        App Store Connect API key id
#   ASC_ISSUER_ID     App Store Connect API issuer id
#   ASC_KEY_PATH      path to the downloaded AuthKey_XXXX.p8 (stays on disk,
#                     never committed; the script only references it by path)
#
# The App Store Connect app record for bundle id com.familycfo.ios must already
# exist (create it once at appstoreconnect.apple.com — see the guide).
#
# FIRST upload for a team must be done ONCE through Xcode's GUI (Organizer ->
# Distribute App -> App Store Connect, automatic signing) to create the Apple
# Distribution certificate: the API key can authenticate the upload but cannot
# create signing certificates ("Cloud signing permission error"). After that one
# GUI upload the cert lives in the keychain and this script runs headless. See
# docs/adr/0035-testflight-release.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

IOS_PROJECT="apps/ios/FamilyCFO/FamilyCFO.xcodeproj"
IOS_SCHEME="FamilyCFO"
BUILD_DIR="${BUILD_DIR:-$REPO_ROOT/apps/ios/.build/release}"
ARCHIVE_PATH="$BUILD_DIR/FamilyCFO.xcarchive"
EXPORT_DIR="$BUILD_DIR/export"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "TestFlight releases build on the Mac (Xcode)."
command -v xcodebuild >/dev/null 2>&1 || die "xcodebuild not found. Install Xcode."

# Load persisted config (IOS_TEAM_ID, ASC_*); a real env var still wins.
# shellcheck source=lib/deploy-env.sh
. "$REPO_ROOT/scripts/lib/deploy-env.sh"
load_deploy_env "$REPO_ROOT"

: "${IOS_TEAM_ID:?set IOS_TEAM_ID in .deploy.env}"
: "${ASC_KEY_ID:?set ASC_KEY_ID in .deploy.env (App Store Connect API key id)}"
: "${ASC_ISSUER_ID:?set ASC_ISSUER_ID in .deploy.env (App Store Connect issuer id)}"
: "${ASC_KEY_PATH:?set ASC_KEY_PATH in .deploy.env (path to AuthKey_XXXX.p8)}"
ASC_KEY_PATH="${ASC_KEY_PATH/#\~/$HOME}"  # the env loader keeps ~ literal
[ -f "$ASC_KEY_PATH" ] || die "ASC_KEY_PATH does not point at a file: $ASC_KEY_PATH"

# One monorepo version (ADR 0029); build number from the clock so every upload
# is strictly newer than the last (App Store Connect rejects a reused build no.).
APP_VERSION="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION")"
BUILD_NUMBER="$(date -u +%Y%m%d%H%M)"

AUTH=(
  -allowProvisioningUpdates
  -authenticationKeyPath "$ASC_KEY_PATH"
  -authenticationKeyID "$ASC_KEY_ID"
  -authenticationKeyIssuerID "$ASC_ISSUER_ID"
)

rm -rf "$ARCHIVE_PATH" "$EXPORT_DIR"
mkdir -p "$BUILD_DIR"

log "Archiving ${IOS_SCHEME} v${APP_VERSION} (${BUILD_NUMBER}) for the App Store…"
xcodebuild archive \
  -project "$IOS_PROJECT" \
  -scheme "$IOS_SCHEME" \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath "$ARCHIVE_PATH" \
  "${AUTH[@]}" \
  DEVELOPMENT_TEAM="$IOS_TEAM_ID" \
  MARKETING_VERSION="$APP_VERSION" \
  CURRENT_PROJECT_VERSION="$BUILD_NUMBER" \
  INFOPLIST_KEY_ITSAppUsesNonExemptEncryption=NO \
  -quiet \
  || die "Archive failed. Re-run without -quiet for the full log."

# Export a signed .ipa (destination: export, NOT upload). Cloud signing — the
# API key creating the distribution cert/profile — works only when the key has
# ADMIN access; a lower role fails with "Cloud signing permission error".
# manageAppVersionAndBuildNumber stays false so our stamped values win.
EXPORT_PLIST="$BUILD_DIR/exportOptions.plist"
cat > "$EXPORT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>export</string>
  <key>teamID</key><string>${IOS_TEAM_ID}</string>
  <key>signingStyle</key><string>automatic</string>
  <key>manageAppVersionAndBuildNumber</key><false/>
</dict>
</plist>
PLIST

log "Exporting a signed .ipa…"
xcodebuild -exportArchive \
  -archivePath "$ARCHIVE_PATH" \
  -exportPath "$EXPORT_DIR" \
  -exportOptionsPlist "$EXPORT_PLIST" \
  "${AUTH[@]}" \
  || die "Export/signing failed. If it says 'Cloud signing permission error',
       the App Store Connect API key needs ADMIN access (ADR 0035). A 401 /
       'No Accounts' means ASC_KEY_ID and the .p8 file don't match."

IPA="$(ls "$EXPORT_DIR"/*.ipa 2>/dev/null | head -1)"
[ -f "$IPA" ] || die "Export produced no .ipa in $EXPORT_DIR."

# altool finds the key by id in a private_keys search dir — place the .p8 there
# (it stays on disk, never committed) rather than passing it inline.
KEY_DIR="$HOME/.appstoreconnect/private_keys"
mkdir -p "$KEY_DIR"
cp "$ASC_KEY_PATH" "$KEY_DIR/AuthKey_${ASC_KEY_ID}.p8"

log "Uploading $(basename "$IPA") to App Store Connect / TestFlight…"
xcrun altool --upload-app --type ios --file "$IPA" \
  --apiKey "$ASC_KEY_ID" --apiIssuer "$ASC_ISSUER_ID" \
  || die "Upload failed (a reused build number or a missing App Store Connect
       app record for com.familycfo.ios are common)."

log "Uploaded v${APP_VERSION} (${BUILD_NUMBER}) to TestFlight."
echo "  It appears under TestFlight in App Store Connect after processing"
echo "  (usually 5–30 min). Add testers or a public link there, then share it."
