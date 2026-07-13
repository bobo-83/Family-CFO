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
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$KEY" -out "$CERT" -days 825 \
    -subj "/CN=${CN}" \
    -addext "subjectAltName=${san}" >/dev/null 2>&1
fi

exec nginx -g 'daemon off;'
