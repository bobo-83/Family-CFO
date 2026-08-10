#!/bin/sh
# Render (or remove) the optional tailnet HTTPS server block.
#
# WHY a second block at all: the box answers to several names on one certificate
# today, and iOS will not accept a self-signed certificate for all of them. The
# relaxed local-network trust policy — the one an app's URLSessionDelegate may
# opt into — is granted to single-label hostnames and RFC1918 addresses ONLY. A
# public FQDN (a tailnet MagicDNS name is one) or a CGNAT 100.64/10 address gets
# no such grace: the client sends ClientHello, reads the certificate, and drops
# the connection without finishing the handshake. The error is
# NSURLErrorDomain -1200 (handshake), not -1202 (trust), because the refusal
# happens below the layer where the delegate is consulted — so no amount of
# pinning or delegate code can rescue it.
#
# That leaves a shared Tailscale user (a second household) with no working
# address at all: the short name does not resolve for them (their MagicDNS
# search domain is their own tailnet), and both the FQDN and the CGNAT IP are
# refused. The fix is a genuinely CA-signed certificate for the MagicDNS name
# (`tailscale cert`, run on the host — see scripts/tailnet-cert.sh), served to
# that name only, selected by SNI.
#
# It cannot simply REPLACE the self-signed certificate: `tailscale cert` issues
# for the MagicDNS name and nothing else — its SAN carries that single DNS name
# — so installing it alone would break the LAN address, the WireGuard address
# and the short name.
#
# Contract: when TLS_TAILNET_NAME is unset, malformed, or the certificate files
# are absent, this writes NO config and the container behaves exactly as it did
# before this script existed. Removal is as important as creation — the
# container's writable layer survives a restart, so a stale block left behind
# after the variable is cleared would keep nginx pointing at files that may no
# longer be there.
set -eu

CERT_DIR="${TLS_CERT_DIR:-/etc/nginx/certs}"
CONF=/etc/nginx/conf.d/tailnet.conf
CERT="$CERT_DIR/tailnet.crt"
KEY="$CERT_DIR/tailnet.key"
NAME="${TLS_TAILNET_NAME:-}"

# Always start from a clean slate so this script is idempotent and reversible.
rm -f "$CONF"

if [ -z "$NAME" ]; then
  exit 0
fi

# The name is interpolated into a config file, so it is validated rather than
# trusted: hostname characters only, no leading/trailing separator. A rejected
# value must not take the web tier down with it — warn and serve the default.
if ! printf '%s' "$NAME" | grep -qE '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'; then
  echo "TLS_TAILNET_NAME is not a valid hostname; ignoring it." >&2
  exit 0
fi

if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
  echo "TLS_TAILNET_NAME is set but $CERT / $KEY are missing;" >&2
  echo "  serving the self-signed certificate only. Run scripts/tailnet-cert.sh" >&2
  echo "  on the HOST to issue and install the pair." >&2
  exit 0
fi

cat >"$CONF" <<EOF
# GENERATED at container start by web-render-tailnet-conf.sh — do not edit.
# Matched by SNI only; deliberately NOT default_server, so a connection by IP
# (which sends no SNI) still lands on the self-signed default block.
server {
    listen 443 ssl;
    server_name ${NAME};

    ssl_certificate     ${CERT};
    ssl_certificate_key ${KEY};

    include /etc/nginx/family-cfo/server-common.conf;
}
EOF

echo "Tailnet HTTPS block enabled for the configured MagicDNS name."
