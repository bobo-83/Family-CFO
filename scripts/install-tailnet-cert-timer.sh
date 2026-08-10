#!/usr/bin/env bash
#
# Family CFO — install the systemd timer that renews the tailnet TLS certificate.
#
# WHY THIS SCRIPT EXISTS
#
# The certificate `tailscale cert` issues is good for 90 days, and the people who
# depend on it — a household the box is SHARED with — have no other working
# address: their device cannot resolve the short name (its MagicDNS search domain
# is their own tailnet) and iOS refuses a self-signed certificate on both a
# public FQDN and a CGNAT address. So a silent expiry locks out exactly the users
# the certificate exists for, 90 days after anyone last thought about it.
#
# Renewal therefore has to be automatic, and "automatic" has to be PROVEN, not
# assumed. Enabling a timer only schedules a job; it says nothing about whether
# the job succeeds. A service that fails every night looks identical to one that
# works until the day the certificate expires. (That was not hypothetical: this
# script's own subject used to escalate to `sudo` before trying unprivileged,
# which fails without a terminal — so every timer run would have died silently.)
#
# Hence the last step: run the service ONCE, now, and report what happened.
#
# Deliberately NOT part of scripts/patch.sh. A deploy rebuilds containers; this
# installs and enables a system service as root. That is a much larger blast
# radius, and it is a one-time setup step rather than something to repeat on
# every deploy. scripts/doctor.sh checks it stays healthy afterwards.
#
# Usage:
#   sudo scripts/install-tailnet-cert-timer.sh          # install, enable, verify
#   sudo scripts/install-tailnet-cert-timer.sh --check  # report only, change nothing
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR=/etc/systemd/system
SERVICE=family-cfo-tailnet-cert.service
TIMER=family-cfo-tailnet-cert.timer

green='\033[1;32m'; red='\033[1;31m'; yellow='\033[1;33m'; cyan='\033[1;36m'; reset='\033[0m'
log()  { printf "${cyan}==>${reset} %s\n" "$*"; }
pass() { printf "  ${green}✔${reset} %s\n" "$*"; }
warn() { printf "  ${yellow}!${reset} %s\n" "$*" >&2; }
die()  { printf "${red}Error:${reset} %s\n" "$*" >&2; exit 1; }

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

command -v systemctl >/dev/null 2>&1 ||
  die "systemctl not found — this host does not use systemd. Schedule
       scripts/tailnet-cert.sh with whatever this box does use, daily, and make
       sure a missed run catches up rather than waiting for the next window."

if [ "$CHECK_ONLY" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
  die "Run with sudo: this writes unit files to ${UNIT_DIR} and enables a service."
fi

# --- report the current state before touching anything ----------------------
log "Current state"
if systemctl list-unit-files "$TIMER" >/dev/null 2>&1 && [ -f "${UNIT_DIR}/${TIMER}" ]; then
  pass "timer unit installed"
  systemctl is-enabled --quiet "$TIMER" 2>/dev/null &&
    pass "timer enabled" || warn "timer installed but NOT enabled"
  # The distinction that matters: enabled says scheduled, not working.
  if systemctl is-failed --quiet "$SERVICE" 2>/dev/null; then
    warn "the renewal service is in a FAILED state — renewal is not happening"
  fi
else
  warn "timer not installed"
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  exit 0
fi

# --- install ----------------------------------------------------------------
# WorkingDirectory and ExecStart are written from THIS checkout's path, which is
# why the units are generated rather than committed: they differ per box, and a
# committed unit with a placeholder path is a unit that silently does nothing.
log "Writing ${UNIT_DIR}/${SERVICE}"
cat > "${UNIT_DIR}/${SERVICE}" <<EOF
[Unit]
Description=Renew the Family CFO tailnet TLS certificate
After=tailscaled.service docker.service
Wants=tailscaled.service

[Service]
Type=oneshot
WorkingDirectory=${REPO_ROOT}
ExecStart=${REPO_ROOT}/scripts/tailnet-cert.sh
# root: tailscaled's socket and the Docker socket both need it.
User=root
EOF

log "Writing ${UNIT_DIR}/${TIMER}"
cat > "${UNIT_DIR}/${TIMER}" <<'EOF'
[Unit]
Description=Daily renewal check for the Family CFO tailnet TLS certificate

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
# Catch up after the box has been off — a missed window must not cost 90 days.
Persistent=true

[Install]
WantedBy=timers.target
EOF

log "Reloading systemd and enabling the timer"
systemctl daemon-reload
systemctl enable --now "$TIMER" >/dev/null
pass "timer enabled and started"

# --- prove it actually runs -------------------------------------------------
# The whole point. An enabled timer whose service errors every night is
# indistinguishable from a working one until the certificate expires.
log "Running the renewal service once, to prove it works"
if systemctl start "$SERVICE"; then
  pass "renewal service completed successfully"
else
  warn "the renewal service FAILED. The timer is enabled but renewal will not
       happen. Diagnose with:  journalctl -u ${SERVICE} -n 50 --no-pager"
  exit 1
fi

log "Scheduled runs"
systemctl list-timers "$TIMER" --no-pager || true

printf "\n${green}Done.${reset} The certificate renews daily and a missed window catches up.\n"
printf "scripts/doctor.sh reports if the service ever starts failing.\n"
