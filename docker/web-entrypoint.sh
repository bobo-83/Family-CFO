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
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$KEY" -out "$CERT" -days 825 \
    -subj "/CN=${TLS_CERT_CN:-family-cfo.local}" >/dev/null 2>&1
fi

exec nginx -g 'daemon off;'
