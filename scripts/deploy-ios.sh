#!/usr/bin/env bash
#
# Family CFO — build and install the iPhone app onto a paired device over WiFi.
#
# The counterpart to scripts/deploy.sh + scripts/patch.sh, which ship the box's
# containers: this ships the phone half. It signs, builds, installs and launches
# the app on a physical iPhone over the local network — no cable, no Xcode UI.
#
# The device must already be paired with Xcode for network debugging. That
# pairing is a ONE-TIME step that needs a USB connection (Xcode → Window →
# Devices and Simulators → connect the phone → tick "Connect via network"),
# after which the phone is reachable over WiFi forever. This script cannot do
# that first pairing for you, and says so plainly if the phone isn't there.
#
# Must run ON THE MAC. The box is Linux and has no Xcode — see the note in
# scripts/patch.sh about which half runs where.
#
# Usage:
#   scripts/deploy-ios.sh                 # build + install + launch on the one connected phone
#   scripts/deploy-ios.sh --list          # show paired devices and exit
#   IOS_DEVICE="Alex's iPhone" scripts/deploy-ios.sh    # pick a device by name or UDID
#   IOS_TEST=1 scripts/deploy-ios.sh      # run the unit tests on a simulator first
#   NO_LAUNCH=1 scripts/deploy-ios.sh     # install but don't launch
#   IOS_CONFIG=Release scripts/deploy-ios.sh
#
# Environment overrides:
#   IOS_DEVICE   device name (substring ok) or UDID   (default: the only connected one)
#   IOS_CONFIG   Debug | Release                      (default: Debug)
#   IOS_SIM      simulator name for IOS_TEST          (default: iPhone 17 Pro)
#   IOS_TEST=1   run unit tests before deploying      (default: off)
#   NO_LAUNCH=1  skip launching after install
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

IOS_PROJECT="apps/ios/FamilyCFO/FamilyCFO.xcodeproj"
IOS_SCHEME="FamilyCFO"
IOS_CONFIG="${IOS_CONFIG:-Debug}"
IOS_SIM="${IOS_SIM:-iPhone 17 Pro}"
DERIVED="${DERIVED:-$REPO_ROOT/apps/ios/.build/deploy}"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# --- Preflight ---------------------------------------------------------------
[ "$(uname -s)" = "Darwin" ] \
  || die "iOS deploys must run on the Mac — this host is $(uname -s), and Xcode only exists on macOS.
       The box's containers are patched with scripts/patch.sh; the phone is patched from your Mac."

command -v xcodebuild >/dev/null 2>&1 \
  || die "xcodebuild not found. Install Xcode, then: sudo xcode-select -s /Applications/Xcode.app"
command -v xcrun >/dev/null 2>&1 || die "xcrun not found (Xcode command line tools)."
[ -d "$IOS_PROJECT" ] || die "Xcode project not found at $IOS_PROJECT"

# --- Device discovery --------------------------------------------------------
# `xcrun devicectl list devices` prints a table. The identifier is the only
# UUID-shaped column, so anchor on it: the fields before it are the (space-
# containing) name plus the hostname, and the field after it is the state.
list_devices() {
  xcrun devicectl list devices 2>/dev/null | awk '
    {
      udid = ""; ui = 0
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$/) {
          udid = $i; ui = i
        }
      }
      if (udid == "") next
      state = $(ui + 1)
      name = ""
      for (i = 1; i <= ui - 2; i++) name = name (i > 1 ? " " : "") $i
      printf "%s\t%s\t%s\n", udid, name, state
    }'
}

if [ "${1:-}" = "--list" ]; then
  log "Paired devices:"
  devices="$(list_devices)"
  [ -n "$devices" ] || { echo "  (none)"; exit 0; }
  printf '%s\n' "$devices" | while IFS=$'\t' read -r udid name state; do
    printf '  %-38s %-24s %s\n' "$udid" "$name" "$state"
  done
  exit 0
fi

all_devices="$(list_devices)"
[ -n "$all_devices" ] || die "No paired iPhone found.
       Pair once over USB: Xcode → Window → Devices and Simulators → select the
       phone → tick 'Connect via network'. After that it deploys over WiFi."

# 'connected' means reachable right now (over USB or, once paired for network
# debugging, over WiFi). Anything else can't be installed to.
connected="$(printf '%s\n' "$all_devices" | awk -F'\t' '$3 == "connected"')"

if [ -n "${IOS_DEVICE:-}" ]; then
  match="$(printf '%s\n' "$connected" | grep -i -- "$IOS_DEVICE" || true)"
  [ -n "$match" ] || die "No CONNECTED device matches IOS_DEVICE='$IOS_DEVICE'.
       Seen: $(printf '%s\n' "$all_devices" | awk -F'\t' '{printf "%s (%s)  ", $2, $3}')
       Run: scripts/deploy-ios.sh --list"
  [ "$(printf '%s\n' "$match" | wc -l | tr -d ' ')" -eq 1 ] \
    || die "IOS_DEVICE='$IOS_DEVICE' matches more than one device — use the UDID."
  selected="$match"
else
  count="$(printf '%s\n' "$connected" | grep -c . || true)"
  [ "$count" -ne 0 ] || die "No iPhone is currently connected.
       Paired but unreachable devices: $(printf '%s\n' "$all_devices" | awk -F'\t' '{printf "%s (%s)  ", $2, $3}')
       Check: the phone is unlocked, on the same WiFi as this Mac, and 'Connect
       via network' is ticked for it in Xcode → Devices and Simulators."
  [ "$count" -eq 1 ] || die "More than one iPhone is connected — pick one with IOS_DEVICE=...
$(printf '%s\n' "$connected" | awk -F'\t' '{printf "       %s  %s\n", $1, $2}')"
  selected="$connected"
fi

DEVICE_UDID="$(printf '%s' "$selected" | cut -f1)"
DEVICE_NAME="$(printf '%s' "$selected" | cut -f2)"
log "Target device: ${DEVICE_NAME}  (${DEVICE_UDID})"

# --- Signing -----------------------------------------------------------------
# Automatic signing still needs a real identity in the keychain; without one the
# build fails deep inside codesign with a much worse message than this.
security find-identity -v -p codesigning 2>/dev/null | grep -q "Apple Development" \
  || die "No 'Apple Development' signing identity in the keychain.
       Open Xcode → Settings → Accounts, add your Apple ID, and let it create one."

# --- Optional test gate ------------------------------------------------------
if [ "${IOS_TEST:-0}" = "1" ]; then
  log "Running unit tests on the '${IOS_SIM}' simulator…"
  xcodebuild test \
    -project "$IOS_PROJECT" \
    -scheme "$IOS_SCHEME" \
    -destination "platform=iOS Simulator,name=${IOS_SIM}" \
    -quiet \
    || die "Tests failed — not deploying a broken build to the phone."
  log "Tests passed."
fi

# --- Build -------------------------------------------------------------------
# M120 (ADR 0029): the monorepo ships ONE version. The app's marketing version is
# stamped from the repo VERSION file, and the build number from the clock (always
# increasing, so over-the-top installs never fight a stale build number).
APP_VERSION="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION")"
BUILD_NUMBER="$(date -u +%Y%m%d%H%M)"
# M121: the Apple Developer team is NOT committed (the project ships with an
# empty DEVELOPMENT_TEAM); a device build supplies your own from IOS_TEAM_ID
# (set it in .deploy.env). Simulator test runs don't need it.
: "${IOS_TEAM_ID:?set IOS_TEAM_ID to your Apple Developer team id (see .deploy.env.example)}"
log "Building ${IOS_SCHEME} (${IOS_CONFIG}) v${APP_VERSION} (${BUILD_NUMBER}) for the device…"
xcodebuild build \
  -project "$IOS_PROJECT" \
  -scheme "$IOS_SCHEME" \
  -configuration "$IOS_CONFIG" \
  -destination "id=${DEVICE_UDID}" \
  -derivedDataPath "$DERIVED" \
  -allowProvisioningUpdates \
  DEVELOPMENT_TEAM="$IOS_TEAM_ID" \
  MARKETING_VERSION="$APP_VERSION" \
  CURRENT_PROJECT_VERSION="$BUILD_NUMBER" \
  -quiet \
  || die "Build failed. Run without -quiet for the full log:
       xcodebuild build -project $IOS_PROJECT -scheme $IOS_SCHEME -destination 'id=${DEVICE_UDID}' -allowProvisioningUpdates"

# Ask xcodebuild where it actually put things rather than guessing a path that
# silently rots when the configuration or product name changes.
settings="$(xcodebuild -showBuildSettings \
  -project "$IOS_PROJECT" -scheme "$IOS_SCHEME" -configuration "$IOS_CONFIG" \
  -destination "id=${DEVICE_UDID}" -derivedDataPath "$DERIVED" 2>/dev/null)"
products_dir="$(printf '%s\n' "$settings" | awk -F' = ' '/ BUILT_PRODUCTS_DIR = /{print $2; exit}')"
product_name="$(printf '%s\n' "$settings" | awk -F' = ' '/ FULL_PRODUCT_NAME = /{print $2; exit}')"
BUNDLE_ID="$(printf '%s\n' "$settings" | awk -F' = ' '/ PRODUCT_BUNDLE_IDENTIFIER = /{print $2; exit}')"
APP_PATH="${products_dir}/${product_name}"

[ -d "$APP_PATH" ] || die "Built app not found at ${APP_PATH}"
[ -n "$BUNDLE_ID" ] || die "Could not determine the bundle identifier from the build settings."

# --- Install + launch --------------------------------------------------------
log "Installing onto ${DEVICE_NAME} over the network…"
xcrun devicectl device install app --device "$DEVICE_UDID" "$APP_PATH" >/dev/null \
  || die "Install failed. Is the phone unlocked and on the same network?"

if [ "${NO_LAUNCH:-0}" = "1" ]; then
  log "Installed ${BUNDLE_ID} (not launched)."
  exit 0
fi

log "Launching ${BUNDLE_ID}…"
xcrun devicectl device process launch --device "$DEVICE_UDID" "$BUNDLE_ID" >/dev/null \
  || die "Installed, but the launch failed — open the app on the phone.
       A brand-new signing identity needs to be trusted once on the device:
       Settings → General → VPN & Device Management → trust the developer."

# Record the phone alongside the servers, so `scripts/deployments.sh` can show
# where the app went and offer to remove it again.
# shellcheck source=lib/deploy-env.sh
. "$REPO_ROOT/scripts/lib/deploy-env.sh"
record_deployment "$REPO_ROOT" ios "$DEVICE_NAME" "$DEVICE_UDID" "" "$IOS_CONFIG" "$BUNDLE_ID"

log "Deployed to ${DEVICE_NAME}."
echo "  The app talks to the box it was paired with — re-pair from the dashboard's"
echo "  Devices page if you moved the server."
