#!/usr/bin/env bash
#
# Family CFO — install the iPhone app OVER THE VPN (over-the-air).
#
# Why this exists: Xcode's wireless deploy (scripts/deploy-ios.sh) finds the
# phone with Bonjour/mDNS. mDNS is multicast, and multicast does not cross a
# routed WireGuard tunnel — so away from home the phone shows as `unavailable`
# and cannot be deployed to, even though it reaches the box perfectly well over
# the VPN.
#
# So don't push to the phone: let the phone pull. This archives a signed build,
# publishes the .ipa + an OTA manifest on the box's HTTPS server, and prints a
# link. Open that link on the phone — over WiFi, over WireGuard, from anywhere
# the phone can reach the box — and iOS installs it.
#
# Requirements:
#   * A paid Apple Developer account (ad-hoc/"release-testing" export).
#   * The phone's UDID in the provisioning profile (it is, if Xcode has ever
#     deployed to it).
#   * The phone must TRUST the box's TLS certificate, or Safari refuses the
#     install. One-time; this script prints the steps and serves the cert.
#
# Usage:
#   scripts/deploy-ios-ota.sh              # archive, export, publish, print the link
#   scripts/deploy-ios-ota.sh --url-only   # reprint the install link for the last build
#
# Environment:
#   OTA_BASE_URL   HTTPS address the PHONE uses to reach the box
#                  (default: derived from .deploy.env / https://192.168.1.10:8443)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck source=lib/deploy-env.sh
. "$REPO_ROOT/scripts/lib/deploy-env.sh"
load_deploy_env "$REPO_ROOT"

IOS_PROJECT="apps/ios/FamilyCFO/FamilyCFO.xcodeproj"
IOS_SCHEME="FamilyCFO"
BUILD_DIR="$REPO_ROOT/apps/ios/.build/ota"
ARCHIVE="$BUILD_DIR/FamilyCFO.xcarchive"
EXPORT_DIR="$BUILD_DIR/export"
BUNDLE_ID="com.familycfo.ios"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "iOS builds need macOS + Xcode."
command -v xcodebuild >/dev/null 2>&1 || die "xcodebuild not found."

# The address the PHONE uses. Not the SSH alias: this goes into a manifest the
# phone fetches, so it must be an address the phone can actually resolve.
if [ -z "${OTA_BASE_URL:-}" ]; then
  OTA_BASE_URL="https://192.168.1.10:8443"
fi
OTA_BASE_URL="${OTA_BASE_URL%/}"
MANIFEST_URL="${OTA_BASE_URL}/ota/manifest.plist"

print_instructions() {
  printf '\n\033[1mInstall on the phone\033[0m (works over WiFi *or* WireGuard)\n\n'
  echo "  1. FIRST TIME ONLY — trust the box's certificate, or Safari will refuse:"
  echo "       a. Open  ${OTA_BASE_URL}/ota/box-cert.crt  in Safari on the phone"
  echo "       b. Settings → General → VPN & Device Management → install the profile"
  echo "       c. Settings → General → About → Certificate Trust Settings →"
  echo "          turn ON full trust for it"
  echo
  echo "  2. Open this link in Safari ON THE PHONE:"
  echo
  printf '       \033[1m%s/ota/\033[0m\n' "$OTA_BASE_URL"
  echo
  echo "     (that page has an Install button; it points at"
  echo "      itms-services://?action=download-manifest&url=${MANIFEST_URL})"
  echo
  echo "  The app is signed for THIS phone. Re-run this script to publish a new build;"
  echo "  the phone installs over the top, keeping its pairing and Keychain credential."
  echo
}

if [ "${1:-}" = "--url-only" ]; then
  print_instructions
  exit 0
fi

# --- Archive -----------------------------------------------------------------
log "Archiving ${IOS_SCHEME} (Release) for a real device…"
rm -rf "$ARCHIVE" "$EXPORT_DIR"
mkdir -p "$BUILD_DIR"
xcodebuild archive \
  -project "$IOS_PROJECT" \
  -scheme "$IOS_SCHEME" \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath "$ARCHIVE" \
  -allowProvisioningUpdates \
  -quiet \
  || die "Archive failed. Re-run without -quiet to see why."
ok "Archived."

# --- Export a signed .ipa + manifest -----------------------------------------
# `debugging` is Xcode 15+'s name for a development export: signed with the Apple
# DEVELOPMENT certificate and the team provisioning profile, which already lists
# this phone's UDID. That is deliberate — `release-testing` (ad-hoc) would need an
# Apple DISTRIBUTION certificate, which this machine doesn't have and which isn't
# necessary to install onto a device the profile already provisions.
#
# Supplying `manifest` makes xcodebuild emit the OTA manifest.plist itself, so its
# URLs and bundle metadata cannot drift from the binary it just signed.
cat > "$BUILD_DIR/ExportOptions.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>method</key><string>debugging</string>
  <key>signingStyle</key><string>automatic</string>
  <key>teamID</key><string>${IOS_TEAM_ID:-YOUR_TEAM_ID}</string>
  <key>destination</key><string>export</string>
  <key>compileBitcode</key><false/>
  <key>thinning</key><string>&lt;none&gt;</string>
  <key>manifest</key>
  <dict>
    <key>appURL</key><string>${OTA_BASE_URL}/ota/FamilyCFO.ipa</string>
    <key>displayImageURL</key><string>${OTA_BASE_URL}/ota/icon-57.png</string>
    <key>fullSizeImageURL</key><string>${OTA_BASE_URL}/ota/icon-512.png</string>
  </dict>
</dict>
</plist>
PLIST

log "Exporting a signed .ipa…"
xcodebuild -exportArchive \
  -archivePath "$ARCHIVE" \
  -exportPath "$EXPORT_DIR" \
  -exportOptionsPlist "$BUILD_DIR/ExportOptions.plist" \
  -allowProvisioningUpdates \
  -quiet \
  || die "Export failed. The build is signed for the devices in the team
       provisioning profile — if this phone was added recently, open the project in
       Xcode once so it can refresh the profile."

IPA="$(find "$EXPORT_DIR" -name '*.ipa' | head -1)"
[ -n "$IPA" ] || die "No .ipa was produced."
MANIFEST="$(find "$EXPORT_DIR" -name 'manifest.plist' | head -1)"
[ -n "$MANIFEST" ] || die "xcodebuild did not emit a manifest.plist."
ok "Exported $(basename "$IPA") ($(du -h "$IPA" | cut -f1))"

# --- Icons (iOS shows these while installing) --------------------------------
ICON_SRC="$REPO_ROOT/shared/brand/icon.svg"
if [ -f "$ICON_SRC" ] && command -v qlmanage >/dev/null 2>&1; then
  qlmanage -t -s 512 -o "$BUILD_DIR" "$ICON_SRC" >/dev/null 2>&1 || true
  RASTER="$BUILD_DIR/$(basename "$ICON_SRC").png"
  if [ -f "$RASTER" ]; then
    sips -z 512 512 "$RASTER" --out "$EXPORT_DIR/icon-512.png" >/dev/null 2>&1 || true
    sips -z 57 57 "$RASTER" --out "$EXPORT_DIR/icon-57.png" >/dev/null 2>&1 || true
  fi
fi
[ -f "$EXPORT_DIR/icon-512.png" ] || warn "No icon rendered — iOS will show a placeholder while installing."

# --- The landing page the phone actually opens -------------------------------
# Safari will not follow an itms-services:// URL typed into the address bar; it
# has to be a link on a page. This is that page.
VERSION="$(date -u +%Y-%m-%d\ %H:%M) UTC"
cat > "$EXPORT_DIR/index.html" <<HTML
<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Install Family CFO</title>
<style>
  body { font: -apple-system-body, system-ui, sans-serif; margin: 0; padding: 2rem 1.5rem;
         background: #f2f2f7; color: #1c1c1e; text-align: center; }
  @media (prefers-color-scheme: dark) { body { background: #000; color: #f2f2f7; } }
  h1 { font-size: 1.5rem; margin: 1rem 0 0.25rem; }
  p  { color: #8e8e93; margin: 0.25rem 0 1.5rem; }
  a.btn { display: block; background: #0a84ff; color: #fff; text-decoration: none;
          padding: 1rem; border-radius: 14px; font-weight: 600; font-size: 1.05rem; }
  small { display: block; margin-top: 2rem; color: #8e8e93; line-height: 1.5; }
  #installed { display: none; margin-top: 1.5rem; padding: 1rem; border-radius: 14px;
               background: rgba(52,199,89,0.12); color: #248a3d; line-height: 1.5; text-align: left; }
  @media (prefers-color-scheme: dark) { #installed { color: #30d158; } }
  #installed b { display: block; margin-bottom: 0.35rem; }
</style>
<h1>Family CFO</h1>
<p>Built ${VERSION}</p>
<a class="btn" id="installBtn" href="itms-services://?action=download-manifest&amp;url=${MANIFEST_URL}">Install on this iPhone</a>

<div id="installed">
  <b>✓ Installing…</b>
  Look for the <strong>Family CFO</strong> icon on your Home Screen — it appears with a
  progress ring while it downloads, then it's ready. <strong>You can close this page.</strong>
  <br><br>
  <b>First launch after a box update?</b>
  If the app says it can't reach your box, open the dashboard → <strong>Devices</strong>,
  generate a pairing code, and scan it once. That re-links the app to the box.
  <br><br>
  Tapping Install again just reinstalls this same build over the top — harmless, and it
  keeps your data and pairing.
</div>

<small>
  Installs straight from your own box — over WiFi or the VPN. Nothing leaves your network.<br><br>
  Nothing happening when you tap Install? The box's certificate isn't trusted on this phone yet:
  open <a href="/ota/box-cert.crt">the certificate</a>, install the profile from the top of
  Settings, then enable it under Settings → General → About → Certificate Trust Settings.
</small>

<script>
  // A web page cannot see whether an app installed (iOS forbids it), so the best
  // we can do is reveal the "what now" guidance the moment Install is tapped —
  // rather than leave the user on an unchanged page wondering if it worked.
  document.getElementById('installBtn').addEventListener('click', function () {
    setTimeout(function () {
      document.getElementById('installed').style.display = 'block';
      document.getElementById('installed').scrollIntoView({ behavior: 'smooth' });
    }, 600);
  });
</script>
HTML

# --- Publish to the box ------------------------------------------------------
[ -n "${SSH_HOST:-}" ] || die "No SSH_HOST — run scripts/setup-ssh.sh first."
REMOTE_DIR="${REMOTE_DIR:-~/Projects/Family-CFO}"

log "Publishing to ${SSH_HOST}…"
STAGE="/tmp/family-cfo-ota"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_HOST" "rm -rf ${STAGE} && mkdir -p ${STAGE}"
scp -q -o BatchMode=yes "$IPA" "$SSH_HOST:${STAGE}/FamilyCFO.ipa"
scp -q -o BatchMode=yes "$MANIFEST" "$SSH_HOST:${STAGE}/manifest.plist"
scp -q -o BatchMode=yes "$EXPORT_DIR/index.html" "$SSH_HOST:${STAGE}/index.html"
for icon in icon-57.png icon-512.png; do
  [ -f "$EXPORT_DIR/$icon" ] && scp -q -o BatchMode=yes "$EXPORT_DIR/$icon" "$SSH_HOST:${STAGE}/$icon"
done

# The box's own TLS certificate, so the phone can be told to trust it. This is a
# PUBLIC certificate, not a key — nothing secret is published here.
ssh -o BatchMode=yes "$SSH_HOST" "cd ${REMOTE_DIR} && \
  docker compose exec -T web cat /etc/nginx/certs/tls.crt > ${STAGE}/box-cert.crt" \
  || warn "Couldn't export the box certificate (the phone may already trust it)."

ssh -o BatchMode=yes "$SSH_HOST" "cd ${REMOTE_DIR} && \
  docker compose exec -T web mkdir -p /usr/share/nginx/html/ota && \
  for f in ${STAGE}/*; do docker compose cp \"\$f\" web:/usr/share/nginx/html/ota/ >/dev/null; done && \
  rm -rf ${STAGE}" \
  || die "Failed to publish into the web container."

ok "Published to ${OTA_BASE_URL}/ota/"

# --- Verify the phone will actually be served the right thing ----------------
log "Verifying over HTTPS…"
for f in manifest.plist FamilyCFO.ipa index.html; do
  code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 15 "${OTA_BASE_URL}/ota/${f}")"
  type="$(curl -sk -o /dev/null -w '%{content_type}' --max-time 15 "${OTA_BASE_URL}/ota/${f}")"
  [ "$code" = "200" ] || die "${f} is not being served (HTTP ${code})."
  printf '   %-16s %s  %s\n' "$f" "$code" "$type"
done
ok "The box is serving the build."

print_instructions
