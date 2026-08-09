#!/bin/sh
# Web entrypoint: ensure a TLS certificate exists, then start nginx.
#
# If no certificate is mounted at /etc/nginx/certs, generate a self-signed one
# so `docker compose up -d` yields working HTTPS out of the box (with the
# expected first-run browser warning). For a real deployment, mount your own
# cert/key over /etc/nginx/certs (tls.crt / tls.key) or front the stack with an
# external TLS reverse proxy — see docker/README.md and ADR 0008.
set -e

CERT_DIR=/etc/nginx/certs
CERT="$CERT_DIR/tls.crt"
KEY="$CERT_DIR/tls.key"

mkdir -p "$CERT_DIR"

if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
  echo "No TLS certificate found; generating a self-signed one (see ADR 0008)..."

  CN="${TLS_CERT_CN:-family-cfo.local}"

  # Subject Alternative Names. Modern clients — iOS/Safari especially — IGNORE
  # the CN and validate the hostname against the SAN only, so a cert with no SAN
  # is "Not Secure" however thoroughly it is trusted, and iOS then refuses to
  # fetch an over-the-air install manifest from it. Every name/IP the box is
  # reached by therefore has to be listed here.
  #
  # TLS_CERT_SAN is a comma-separated list of the extra hostnames and IPs this
  # box answers to — set it to your LAN IP and any DNS aliases (e.g.
  # "192.168.1.10,family-cfo-box"). The CN and loopback are always included.
  san="DNS:${CN},DNS:localhost,IP:127.0.0.1"
  OLD_IFS="$IFS"; IFS=','
  for entry in $TLS_CERT_SAN; do
    entry="$(printf '%s' "$entry" | tr -d ' ')"
    [ -z "$entry" ] && continue
    # Classify by shape: digits-and-dots (v4) or a colon (v6) is an IP, else DNS.
    if printf '%s' "$entry" | grep -qE '^[0-9.]+$' || printf '%s' "$entry" | grep -q ':'; then
      san="${san},IP:${entry}"
    else
      san="${san},DNS:${entry}"
    fi
  done
  IFS="$OLD_IFS"

  echo "  subjectAltName=${san}"
  # 397 days, NOT the 825 this used to be. Apple caps TLS server certificates
  # at 398 days: over that, iOS refuses the connection during the HANDSHAKE
  # (NSURLErrorDomain -1200), before trust evaluation — so an app's
  # certificate-accepting delegate never gets a say and pinning cannot help.
  # Safari still works, because a human can tap through a warning and
  # URLSession cannot, which makes this fail in the app while looking fine in
  # a browser. 825 was the pre-2020 limit and is simply stale.
  #
  # A certificate installed and trusted as a profile on the device is exempt
  # (the limit applies to system trust anchors), which is why a box whose cert
  # predates this can keep working until the cert is regenerated.
  #
  # The cost is real: the certificate now expires yearly, and regenerating it
  # changes the SHA-256 that paired devices pin, so every device re-pairs.
  # 397 rather than 398 leaves room for clock skew.
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$KEY" -out "$CERT" -days 397 \
    -subj "/CN=${CN}" \
    -addext "subjectAltName=${san}" >/dev/null 2>&1
fi

exec nginx -g 'daemon off;'
