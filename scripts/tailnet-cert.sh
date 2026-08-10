#!/usr/bin/env bash
#
# Family CFO — issue/renew the box's tailnet TLS certificate and install it.
#
# WHY this exists
#
# The box serves ONE self-signed certificate to every address it answers to,
# and iOS will not take that certificate on every address. The relaxed
# local-network trust policy — the one where an app's URLSessionDelegate may
# accept a self-signed certificate — is granted to single-label hostnames and
# RFC1918 addresses only. A public FQDN (a MagicDNS name is one) or a CGNAT
# 100.64/10 address is refused during the HANDSHAKE, below the layer where the
# delegate is consulted: NSURLErrorDomain -1200, not -1202. So a household you
# SHARE the node with has no working address — their device cannot resolve the
# short name (their MagicDNS search domain is their own tailnet) and both the
# FQDN and the tailnet IP are rejected.
#
# `tailscale cert` issues a genuine Let's Encrypt certificate for the MagicDNS
# name. nginx serves it for that name only, chosen by SNI; every other address
# keeps the self-signed certificate (docker/web-render-tailnet-conf.sh).
#
# WHY IT MUST RUN ON A TIMER
#
# The certificate is good for 90 days and the people who depend on it have no
# other way in — a silent expiry locks out exactly the users the feature exists
# for. See docs/guides/second-household-access.md for the systemd unit + timer.
#
# Runs ON THE HOST, because that is where tailscaled is; it does not exist
# inside the compose stack (and deliberately so — see the guide). Re-running is
# idempotent: `tailscale cert` reuses a cached certificate until it is near
# expiry, and an unchanged certificate is not copied and does not reload nginx.
#
# Usage:
#   scripts/tailnet-cert.sh                 # name from .env / TLS_TAILNET_NAME,
#                                           # else asks tailscaled
#   scripts/tailnet-cert.sh <magicdns-name>
#   FORCE=1 scripts/tailnet-cert.sh         # install + reload even if unchanged
#
# Exit codes: 0 installed or already current; non-zero means the tailnet address
# is (or will soon be) broken for shared households — worth alerting on.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml}"
WEB_SERVICE="${WEB_SERVICE:-web}"
CERT_DIR_IN_CONTAINER=/etc/nginx/certs

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

compose() { docker compose $COMPOSE_FILES "$@"; }

# tailscaled's socket is root-owned unless an operator has been set; a systemd
# timer already runs as root, an interactive run usually has not.
ts() {
  if [ "$(id -u)" -eq 0 ]; then
    tailscale "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -n tailscale "$@" 2>/dev/null || sudo tailscale "$@"
  else
    tailscale "$@"
  fi
}

command -v tailscale >/dev/null 2>&1 ||
  die "tailscale not found — this script runs on the HOST, where tailscaled is."
command -v docker >/dev/null 2>&1 || die "docker not found."
command -v openssl >/dev/null 2>&1 || die "openssl not found (needed to read the certificate)."

# --- 1. Which name? ---------------------------------------------------------
# Precedence: argument > environment > .env > ask tailscaled. ADR 0030 keeps the
# real name out of the repo, so there is no default to fall back on.
NAME="${1:-${TLS_TAILNET_NAME:-}}"

if [ -z "$NAME" ] && [ -f .env ]; then
  NAME="$(sed -n 's/^[[:space:]]*TLS_TAILNET_NAME[[:space:]]*=[[:space:]]*//p' .env | tail -n1)"
  NAME="${NAME%\"}"; NAME="${NAME#\"}"
  NAME="${NAME%\'}"; NAME="${NAME#\'}"
fi

if [ -z "$NAME" ]; then
  log "No TLS_TAILNET_NAME set — asking tailscaled for this node's DNS name."
  NAME="$(ts status --json | tr ',' '\n' | sed -n 's/.*"DNSName"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
  NAME="${NAME%.}"   # MagicDNS reports a fully-qualified name with a trailing dot
fi

[ -n "$NAME" ] || die "could not determine the MagicDNS name; pass it as an argument."
[[ "$NAME" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] ||
  die "'$NAME' is not a valid hostname."

# The whole point is a name a public CA can vouch for; a single-label name has
# no CA path and would fail issuance with a confusing error.
case "$NAME" in
  *.*) ;;
  *) die "'$NAME' is single-label — expected the full MagicDNS name (name.tailnet.ts.net)." ;;
esac

log "Certificate name: ${NAME%%.*}.<tailnet>"   # never print the full name (ADR 0030)

# --- 2. Issue (or reuse the cached) certificate -----------------------------
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
chmod 700 "$TMP"

log "Requesting certificate from Let's Encrypt via tailscaled..."
# tailscale cert is a no-op when a valid cached certificate exists and is not
# near expiry, which is what makes a daily timer cheap and rate-limit-safe.
ts cert --cert-file "$TMP/tailnet.crt" --key-file "$TMP/tailnet.key" "$NAME" ||
  die "tailscale cert failed. Check that HTTPS certificates are enabled for the
  tailnet (admin console → DNS → HTTPS Certificates) and that MagicDNS is on."

[ -s "$TMP/tailnet.crt" ] && [ -s "$TMP/tailnet.key" ] ||
  die "tailscale cert produced an empty file."

NOT_AFTER="$(openssl x509 -in "$TMP/tailnet.crt" -noout -enddate 2>/dev/null | cut -d= -f2 || true)"
[ -n "$NOT_AFTER" ] && log "Valid until: $NOT_AFTER"

# --- 3. Install into the web_certs volume -----------------------------------
# `compose ps` exits 0 for a stopped service and just prints nothing, so the
# output is what has to be tested — everything below needs a live container.
[ -n "$(compose ps --status running --format '{{.Name}}' "$WEB_SERVICE" 2>/dev/null)" ] ||
  die "the '$WEB_SERVICE' service is not running — 'docker compose up -d $WEB_SERVICE' first."

new_fp="$(openssl x509 -in "$TMP/tailnet.crt" -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2)"
old_fp="$(compose exec -T "$WEB_SERVICE" sh -c \
  "openssl x509 -in $CERT_DIR_IN_CONTAINER/tailnet.crt -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2" \
  2>/dev/null | tr -d '\r' || true)"

if [ -n "$old_fp" ] && [ "$new_fp" = "$old_fp" ] && [ "${FORCE:-0}" != "1" ]; then
  log "Installed certificate is already current — nothing to do."
  # Still make sure the server block exists: the certificate can be in place
  # while the config is not, e.g. the first run after TLS_TAILNET_NAME was set.
  if ! compose exec -T "$WEB_SERVICE" test -f /etc/nginx/conf.d/tailnet.conf 2>/dev/null; then
    warn "certificate present but no tailnet server block — rendering it now."
  else
    exit 0
  fi
else
  log "Installing into the web_certs volume..."
  compose cp "$TMP/tailnet.crt" "$WEB_SERVICE:$CERT_DIR_IN_CONTAINER/tailnet.crt"
  compose cp "$TMP/tailnet.key" "$WEB_SERVICE:$CERT_DIR_IN_CONTAINER/tailnet.key"
  compose exec -T "$WEB_SERVICE" chmod 600 "$CERT_DIR_IN_CONTAINER/tailnet.key"
  compose exec -T "$WEB_SERVICE" chmod 644 "$CERT_DIR_IN_CONTAINER/tailnet.crt"
fi

# --- 4. Render the server block, validate, reload ---------------------------
# The renderer reads TLS_TAILNET_NAME from the CONTAINER's environment (compose
# passes it through from .env), so a name given only as an argument here would
# render nothing — say so rather than exit 0 on a box that is still broken.
log "Rendering the tailnet server block..."
compose exec -T "$WEB_SERVICE" /usr/local/bin/web-render-tailnet-conf.sh

if ! compose exec -T "$WEB_SERVICE" test -f /etc/nginx/conf.d/tailnet.conf; then
  die "no tailnet server block was rendered — set TLS_TAILNET_NAME in .env and
  run 'docker compose up -d web' so the container sees it."
fi

# nginx -t BEFORE the reload: a reload with a bad config leaves the old workers
# running, which looks fine right up until the next restart serves nothing.
log "Validating nginx configuration..."
compose exec -T "$WEB_SERVICE" nginx -t

log "Reloading nginx..."
compose exec -T "$WEB_SERVICE" nginx -s reload

log "Done. The tailnet name now serves a CA-signed certificate; every other"
log "address still serves the self-signed one that paired devices pin."
