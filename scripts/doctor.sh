#!/usr/bin/env bash
#
# Family CFO — deployment doctor.
#
# A read-only health report for a running stack: checks Docker, each container,
# the API/DB/web/vLLM endpoints, disk space, the GPU, and — the "Setup" section
# — the configuration that is silently wrong long before anyone notices.
# Makes no changes. Exit code is non-zero if any REQUIRED check fails (AI checks
# are advisory unless FAMILY_CFO_AI_ENABLED=true).
#
# Usage:
#   scripts/doctor.sh                 # inspect the local stack
#   scripts/doctor.sh --setup-only    # only the Setup section (run by patch.sh)
#   COMPOSE_FILES="-f docker-compose.yml" scripts/doctor.sh
set -uo pipefail

SETUP_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --setup-only) SETUP_ONLY=1 ;;
    -h|--help) awk 'NR>1 { if (/^#/) { sub(/^# ?/, ""); print } else exit }' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) printf 'doctor: unknown option %s (try --help)\n' "$arg" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml}"
# shellcheck disable=SC2086
DC="docker compose $COMPOSE_FILES"

green='\033[1;32m'; red='\033[1;31m'; yellow='\033[1;33m'; dim='\033[2m'; reset='\033[0m'
fail_count=0
warn_count=0

pass() { printf "  ${green}✔${reset} %s\n" "$*"; }
fail() { printf "  ${red}x${reset} %s\n" "$*"; fail_count=$((fail_count + 1)); }
warn() { printf "  ${yellow}!${reset} %s\n" "$*"; warn_count=$((warn_count + 1)); }
section() { printf "\n${dim}== %s ==${reset}\n" "$*"; }
# Neither a pass nor a problem: a check that could not run here (no openssl, no
# systemd, no tailscale). doctor.sh is also run on a fresh box and by people who
# are not on the maintainer's setup — a missing tool must never look like a
# fault, and must never change the exit code.
note() { printf "  ${dim}·${reset} %s\n" "$*"; }

# Read a value from .env (best effort).
env_val() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-; }

# LAN address other machines use to reach this host (published ports bind 0.0.0.0).
detect_host_ip() {
  local ip
  ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
  [ -z "$ip" ] && ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "${ip:-localhost}"
}

svc_running() { [ "$($DC ps -q "$1" 2>/dev/null | wc -l)" -gt 0 ] && \
  [ -n "$($DC ps --status running -q "$1" 2>/dev/null)" ]; }

# Run a command inside the api container (has curl-free python + psql tools).
in_api() { $DC exec -T api "$@" 2>/dev/null; }

# ===========================================================================
# SETUP DRIFT
# ===========================================================================
# Everything below is a SILENT misconfiguration: the containers are up, the API
# answers, the smoke check is green — and something that only matters on a
# future day is already wrong. A certificate iOS will never accept. A renewal
# timer that has failed every night for two months. A migration the api never
# applied. A published phone bundle two versions behind the box. None of it is
# visible in the checks above, which is why each of these was found by a person
# reading files, at the worst possible moment.
#
# scripts/patch.sh runs this section after every deploy (`--setup-only`), so it
# is deliberately cheap: ONE exec into the web container, one into the database,
# and a few host commands.
#
# Every check degrades to note() rather than crashing when its tool is absent.

# The whole certificate + OTA probe, in one round trip. `docker compose exec`
# costs the better part of a second each; this runs on every deploy.
read -r -d '' WEB_PROBE_SCRIPT <<'PROBE' || true
set -u
CERT_DIR=/etc/nginx/certs
OTA_VERSION_FILE=/usr/share/nginx/html/ota/VERSION
if command -v openssl >/dev/null 2>&1; then
  echo "openssl=1"
  for n in tls tailnet; do
    c="$CERT_DIR/$n.crt"
    [ -f "$c" ] || continue
    echo "$n.present=1"
    echo "$n.notbefore=$(openssl x509 -in "$c" -noout -startdate 2>/dev/null | cut -d= -f2-)"
    echo "$n.notafter=$(openssl x509 -in "$c" -noout -enddate 2>/dev/null | cut -d= -f2-)"
    # -checkend is the verdict, not host-side date arithmetic: it is exact and
    # needs no date(1) that can parse openssl's format.
    if openssl x509 -in "$c" -noout -checkend 0 >/dev/null 2>&1; then
      echo "$n.expired=0"; else echo "$n.expired=1"; fi
    if openssl x509 -in "$c" -noout -checkend 2592000 >/dev/null 2>&1; then
      echo "$n.gt30d=1"; else echo "$n.gt30d=0"; fi
    # -ext needs OpenSSL >= 1.1.1; on anything older these come back empty and
    # the caller reports "could not read", never a false failure.
    echo "$n.san=$(openssl x509 -in "$c" -noout -ext subjectAltName 2>/dev/null | tail -n +2 | tr -d ' \n' | sed 's/IPAddress:/IP:/g')"
    echo "$n.bc=$(openssl x509 -in "$c" -noout -ext basicConstraints 2>/dev/null | tail -n +2 | tr -d ' \n')"
    echo "$n.eku=$(openssl x509 -in "$c" -noout -ext extendedKeyUsage 2>/dev/null | tail -n +2 | tr -d ' \n')"
  done
else
  echo "openssl=0"
fi
[ -f "$OTA_VERSION_FILE" ] && echo "ota=$(tr -d '[:space:]' < "$OTA_VERSION_FILE")"
exit 0
PROBE

WEB_PROBE=""
WEB_PROBE_DONE=0
web_probe() {
  if [ "$WEB_PROBE_DONE" -eq 0 ]; then
    WEB_PROBE_DONE=1
    WEB_PROBE="$($DC exec -T web sh -c "$WEB_PROBE_SCRIPT" 2>/dev/null || true)"
  fi
  printf '%s\n' "$WEB_PROBE"
}
probe_val() { printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -1; }

# GNU date parses openssl's "Aug 10 12:00:00 2026 GMT"; BSD date needs -j -f.
# BSD date also accepts -d as the DST flag and would silently return *now*, so
# the two are told apart by --version (GNU only) rather than by trial.
if date --version >/dev/null 2>&1; then DATE_FLAVOUR=gnu; else DATE_FLAVOUR=bsd; fi
epoch_of() { # epoch_of "<openssl date>" -> seconds, or empty
  case "$DATE_FLAVOUR" in
    gnu) date -u -d "$1" +%s 2>/dev/null ;;
    bsd) date -u -j -f '%b %e %T %Y %Z' "$1" +%s 2>/dev/null ;;
  esac
}
days_between() { # days_between <from-epoch> <to-epoch>
  echo $(( ($2 - $1) / 86400 ))
}

# --- certificates ----------------------------------------------------------
check_certificates() {
  local probe openssl_ok
  probe="$(web_probe)"
  openssl_ok="$(probe_val "$probe" openssl)"
  if [ -z "$openssl_ok" ]; then
    note "web container not reachable — certificate checks skipped"
    return 0
  fi
  if [ "$openssl_ok" != "1" ]; then
    note "no openssl in the web container — certificate checks skipped"
    return 0
  fi

  local now; now="$(date -u +%s)"
  local name label notafter expired gt30d end_epoch days
  for name in tls tailnet; do
    [ "$(probe_val "$probe" "$name.present")" = "1" ] || {
      # tailnet.crt is optional by design (only a tailnet-shared box has one);
      # tls.crt is generated by the web entrypoint, so its absence is real.
      if [ "$name" = "tls" ]; then
        fail "tls.crt is missing from the web_certs volume — the box is not serving its own certificate"
      fi
      continue
    }
    label="$name.crt"
    notafter="$(probe_val "$probe" "$name.notafter")"
    expired="$(probe_val "$probe" "$name.expired")"
    gt30d="$(probe_val "$probe" "$name.gt30d")"
    days=""
    end_epoch="$(epoch_of "$notafter")"
    [ -n "$end_epoch" ] && days="$(days_between "$now" "$end_epoch")"

    # FAIL when expired: TLS is broken for every client right now, and for the
    # tailnet certificate that means the shared household has no way in at all.
    if [ "$expired" = "1" ]; then
      fail "$label EXPIRED (notAfter: ${notafter:-unknown})${days:+ — ${days#-} days ago}$(
        [ "$name" = "tailnet" ] && printf '%s' " — run scripts/tailnet-cert.sh" || printf '%s' " — delete it from the web_certs volume and restart web to regenerate")"
    elif [ "$gt30d" = "0" ]; then
      # WARN under 30 days: it works today. This is the check that motivated the
      # section — a Let's Encrypt tailnet cert lives 90 days and the people who
      # depend on it have no other route in.
      warn "$label expires in ${days:-<30} days (${notafter:-unknown})$(
        [ "$name" = "tailnet" ] && printf '%s' " — renewal should be automatic; see the timer check below" || printf '%s' " — regenerating it re-pins every paired device, so plan it")"
    else
      pass "$label valid${days:+ for ${days} more days} (${notafter:-unknown})"
    fi
  done

  # --- fitness for iOS (self-signed tls.crt only) --------------------------
  # A certificate failing any of these is refused by iOS DURING THE HANDSHAKE
  # (NSURLErrorDomain -1200), below the layer where a URLSessionDelegate is
  # consulted — so no delegate, no pinning and no app change can rescue it, and
  # curl/openssl will accept it happily while the phone cannot connect.
  # FAIL, not warn: for any device that has not had this certificate installed
  # as a trusted profile — which includes every new device and the OTA install
  # flow — the app is unusable today. The profile exemption is real but is not
  # something the box can verify, so doctor reports the conservative truth.
  if [ "$(probe_val "$probe" tls.present)" = "1" ]; then
    local bc eku nb na vdays
    bc="$(probe_val "$probe" tls.bc)"
    eku="$(probe_val "$probe" tls.eku)"
    if [ -z "$bc" ] && [ -z "$eku" ]; then
      note "openssl in the web container is too old for -ext — iOS fitness checks skipped"
    else
      case "$bc" in
        *CA:FALSE*) pass "tls.crt basicConstraints CA:FALSE" ;;
        *) fail "tls.crt is a CA certificate (basicConstraints: ${bc:-absent}) — iOS refuses it as a server cert; regenerate with docker/web-entrypoint.sh" ;;
      esac
      case "$eku" in
        *ServerAuthentication*|*serverAuth*) pass "tls.crt extendedKeyUsage serverAuth" ;;
        *) fail "tls.crt has no serverAuth extendedKeyUsage (${eku:-absent}) — iOS refuses it during the handshake" ;;
      esac
    fi
    nb="$(probe_val "$probe" tls.notbefore)"; na="$(probe_val "$probe" tls.notafter)"
    local nb_e na_e
    nb_e="$(epoch_of "$nb")"; na_e="$(epoch_of "$na")"
    if [ -n "$nb_e" ] && [ -n "$na_e" ]; then
      vdays="$(days_between "$nb_e" "$na_e")"
      if [ "$vdays" -le 398 ]; then pass "tls.crt validity ${vdays} days (Apple's cap is 398)"
      else fail "tls.crt is valid for ${vdays} days — Apple caps TLS server certificates at 398; iOS refuses it during the handshake"; fi
    else
      note "could not parse the certificate dates with this date(1) — validity-period check skipped"
    fi
  fi
}

# --- TLS_CERT_SAN vs the addresses the box actually answers to -------------
# Modern clients validate the hostname against the SAN and ignore the CN, so an
# address missing from the SAN fails hostname validation however well the
# certificate is trusted. WARN, not fail: the missing address may be one nobody
# uses, and the fix (regenerate the certificate) re-pins every paired device —
# not something to declare broken, and never something to block a deploy on.
check_cert_san() {
  local probe san addr
  probe="$(web_probe)"
  [ "$(probe_val "$probe" tls.present)" = "1" ] || return 0
  san="$(probe_val "$probe" tls.san)"
  if [ -z "$san" ]; then
    note "could not read the certificate SAN — coverage check skipped"
    return 0
  fi

  local addrs="" host_ip ts_ip
  host_ip="$(detect_host_ip)"
  [ "$host_ip" != "localhost" ] && addrs="$host_ip"

  # The tailnet address only exists if tailscaled is running here. `tailscale
  # ip` may need root for the socket; an unreadable answer is a skip, not a
  # fault.
  if command -v tailscale >/dev/null 2>&1; then
    ts_ip="$(tailscale ip -4 2>/dev/null | head -1)"
    if [ -n "$ts_ip" ]; then addrs="$addrs $ts_ip"
    else note "tailscale present but its state is not readable here (needs root?) — tailnet address not checked"; fi
  fi

  if [ -z "${addrs// /}" ]; then
    note "could not determine this host's addresses — SAN coverage check skipped"
    return 0
  fi

  local missing=""
  for addr in $addrs; do
    case ",$san," in
      *",IP:$addr,"*) ;;
      *) missing="$missing $addr" ;;
    esac
  done
  if [ -z "${missing// /}" ]; then
    pass "certificate SAN covers every address this box answers to (${addrs// /, })"
  else
    warn "certificate SAN does not cover:$missing — connections to those addresses fail hostname validation. Add them to TLS_CERT_SAN in .env, delete tls.crt/tls.key from the web_certs volume and restart web (this re-pins paired devices)."
  fi
}

# --- tailnet certificate renewal -------------------------------------------
# The tailnet certificate is a real Let's Encrypt one: 90 days, and the shared
# household it exists for has no other address that iOS will accept. An enabled
# timer whose service fails every night is therefore the failure mode that hides
# for three months and then locks someone out.
#   not installed / disabled -> WARN: a setup step not taken yet.
#   last run failed          -> FAIL: it IS set up, it IS erroring, and nothing
#                               else in this stack will ever tell anyone.
check_tailnet_timer() {
  local tailnet_name probe unit_timer unit_service
  tailnet_name="$(env_val TLS_TAILNET_NAME)"
  [ -n "$tailnet_name" ] || return 0

  # "we could not look" and "it is not there" are different answers, and only
  # one of them is a problem. An unreachable web container must not be reported
  # as a missing certificate.
  probe="$(web_probe)"
  if [ "$(probe_val "$probe" openssl)" != "1" ]; then
    note "could not inspect the web container — tailnet certificate presence not checked"
  elif [ "$(probe_val "$probe" tailnet.present)" != "1" ]; then
    warn "TLS_TAILNET_NAME is set but no tailnet.crt is installed — the tailnet name serves the self-signed certificate, which iOS refuses for a public FQDN. Run scripts/tailnet-cert.sh on the host."
    return 0
  fi

  unit_timer=family-cfo-tailnet-cert.timer
  unit_service=family-cfo-tailnet-cert.service
  if ! command -v systemctl >/dev/null 2>&1; then
    note "no systemctl on this host — tailnet renewal timer not checked; make sure $unit_timer (or your scheduler's equivalent) runs scripts/tailnet-cert.sh daily"
    return 0
  fi

  local enabled
  enabled="$(systemctl is-enabled "$unit_timer" 2>/dev/null)"
  case "$enabled" in
    enabled|enabled-runtime|static)
      local next
      next="$(systemctl list-timers --all "$unit_timer" 2>/dev/null | awk 'NR==2 {print $1, $2, $3}')"
      pass "$unit_timer enabled${next:+ (next run: $next)}"
      ;;
    *)
      warn "$unit_timer is ${enabled:-not installed} — the tailnet certificate expires in 90 days and nothing is renewing it. See docs/guides/second-household-access.md."
      ;;
  esac

  # is-failed exits 0 when the unit is in the failed state. A healthy oneshot is
  # "inactive" after a successful run, which exits non-zero — the inverse of the
  # usual convention, hence the explicit test.
  if systemctl is-failed --quiet "$unit_service" 2>/dev/null; then
    fail "$unit_service is in the FAILED state — the tailnet certificate is not being renewed. journalctl -u $unit_service -n 50"
    return 0
  fi
  # `show -p Result --value` needs systemd >= 230; older versions print
  # "Result=success", so the prefix is stripped rather than assumed absent.
  local result
  result="$(systemctl show -p Result "$unit_service" 2>/dev/null | tail -1)"
  result="${result#Result=}"
  case "$result" in
    ""|success) pass "$unit_service last run: ${result:-no record yet}" ;;
    *) fail "$unit_service last run: $result — renewal is failing. journalctl -u $unit_service -n 50" ;;
  esac
}

# --- migration state -------------------------------------------------------
# The api applies migrations on startup (docker/entrypoint-api.sh). When the
# revision the database reports is not the newest one in the tree, the api that
# is running is not the code that was shipped — usually an image that never
# rebuilt. WARN: the stack is serving and the data is intact; what is wrong is
# that a change silently did not land, which is a thing to look at, not a
# reason to call the box down.
check_migrations() {
  local dir=database/migrations/versions
  [ -d "$dir" ] || { note "no $dir — migration check skipped"; return 0; }

  local revs downs heads
  revs="$(sed -n 's/^revision[^=]*= *"\([^"]*\)".*/\1/p' "$dir"/*.py 2>/dev/null | sort -u)"
  downs="$(sed -n 's/^down_revision[^=]*= *"\([^"]*\)".*/\1/p' "$dir"/*.py 2>/dev/null | sort -u | grep -v '^$')"
  # The head is the revision nothing else points back to. Reading the newest
  # FILENAME instead would be wrong the moment a migration is added out of order.
  if [ -z "$revs" ]; then note "no migrations found in $dir — check skipped"; return 0; fi
  if [ -z "$downs" ]; then heads="$revs"
  else heads="$(printf '%s\n' "$revs" | grep -vxF "$downs")"; fi
  local head_count; head_count="$(printf '%s\n' "$heads" | grep -c .)"
  if [ "$head_count" -ne 1 ]; then
    warn "migrations have ${head_count} heads in $dir — alembic cannot upgrade a branched history"
    return 0
  fi

  local pg_user pg_db db_rev
  pg_user="$(env_val POSTGRES_USER)"; pg_user="${pg_user:-family_cfo}"
  pg_db="$(env_val POSTGRES_DB)"; pg_db="${pg_db:-family_cfo}"
  db_rev="$($DC exec -T db psql -U "$pg_user" -d "$pg_db" -tAc \
    'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]\r' | head -1)"

  if [ -z "$db_rev" ]; then
    note "could not read alembic_version from the database — migration check skipped"
  elif [ "$db_rev" = "$heads" ]; then
    pass "schema at $db_rev (newest migration in the tree)"
  else
    warn "schema is at $db_rev but the newest migration is $heads — the api did not apply something. Check: $DC logs api | grep -i alembic"
  fi
}

# --- OTA bundle vs VERSION -------------------------------------------------
# One monorepo version (ADR 0029). patch.sh reports this after a remote deploy;
# doctor reports it too, so a box inspected on any other day says the same
# thing. WARN: the phone runs old code against a new backend, which is a real
# problem, but the box itself is serving correctly.
check_ota_bundle() {
  local repo_version ota_version
  repo_version="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION" 2>/dev/null)"
  [ -n "$repo_version" ] || { note "no VERSION file — OTA check skipped"; return 0; }
  ota_version="$(probe_val "$(web_probe)" ota)"
  if [ -z "$(web_probe)" ]; then
    note "web container not reachable — OTA bundle check skipped"
  elif [ -z "$ota_version" ]; then
    warn "no OTA bundle published (box runs v${repo_version}) — run scripts/deploy-ios-ota.sh so the phone can install it"
  elif [ "$ota_version" != "$repo_version" ]; then
    warn "OTA bundle is v${ota_version} but this box runs v${repo_version} — the published app is STALE. Run scripts/deploy-ios-ota.sh."
  else
    pass "OTA bundle matches the box (v${ota_version})"
  fi
}

# --- secrets left lying around ---------------------------------------------
# A copy of .env is a copy of the master key. One sat on the box for a day after
# a hand-edit, in plaintext, world-readable. WARN: nothing is broken, but it is
# a secret at rest that nobody meant to leave there.
check_env_copies() {
  local strays="" f
  for f in .env.* .env-* .env~; do
    [ -e "$f" ] || continue
    case "$f" in .env.example) continue ;; esac
    strays="$strays $f"
  done
  strays="${strays# }"
  if [ -n "${strays// /}" ]; then
    warn "copies of .env in the deploy directory: ${strays% } — each is a plaintext copy of the master key. Shred them (shred -u) once you are done."
  else
    pass "no stray .env copies in the deploy directory"
  fi
}

setup_checks() {
  section "Setup"
  check_certificates
  check_cert_san
  check_tailnet_timer
  check_migrations
  check_ota_bundle
  check_env_copies
}

print_summary() {
  section "Summary"
  if [ "$fail_count" -eq 0 ] && [ "$warn_count" -eq 0 ]; then
    printf "${green}All checks passed.${reset}\n"; exit 0
  elif [ "$fail_count" -eq 0 ]; then
    printf "${yellow}%d warning(s), no failures.${reset}\n" "$warn_count"; exit 0
  else
    printf "${red}%d failure(s), %d warning(s).${reset}\n" "$fail_count" "$warn_count"; exit 1
  fi
}

if [ "$SETUP_ONLY" -eq 1 ]; then
  setup_checks
  print_summary
fi

section "Host prerequisites"
if command -v docker >/dev/null 2>&1; then pass "docker present ($(docker --version | awk '{print $3}' | tr -d ,))"
else fail "docker not installed"; fi
if docker compose version >/dev/null 2>&1; then pass "docker compose v2 present"
else fail "docker compose v2 missing"; fi
if [ -f .env ]; then pass ".env present"
else warn ".env missing (using compose defaults / may fail on POSTGRES_PASSWORD)"; fi

ai_enabled="$(env_val FAMILY_CFO_AI_ENABLED)"; ai_enabled="${ai_enabled:-true}"

section "Containers"
for svc in db api worker web; do
  if svc_running "$svc"; then pass "$svc running"; else fail "$svc not running"; fi
done
if svc_running vllm; then pass "vllm running"
elif [ "$ai_enabled" = "true" ]; then warn "vllm not running but AI is enabled"
else pass "vllm off (AI disabled)"; fi

section "Endpoints"
# API health (from inside the api container, so no host port needed).
if in_api python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health').status==200 else 1)"; then
  pass "API /api/v1/health OK"
else fail "API health check failed"; fi

# DB reachable via pg_isready in the api image.
pg_user="$(env_val POSTGRES_USER)"; pg_user="${pg_user:-family_cfo}"
if in_api pg_isready -h db -p 5432 -U "$pg_user" >/dev/null; then pass "PostgreSQL accepting connections"
else fail "PostgreSQL not ready"; fi

# Web tier (HTTPS, self-signed — allow insecure).
web_tls_port="$(env_val WEB_TLS_PORT)"; web_tls_port="${web_tls_port:-8443}"
host_ip="$(detect_host_ip)"
if command -v curl >/dev/null 2>&1; then
  if curl -ksSf -o /dev/null "https://localhost:${web_tls_port}/"; then
    pass "Dashboard reachable at https://${host_ip}:${web_tls_port} (LAN) / https://localhost:${web_tls_port}"
  else warn "Dashboard not reachable on https://localhost:${web_tls_port} (check WEB_TLS_PORT / firewall)"; fi
else warn "curl not on host — skipped dashboard check"; fi

# vLLM model endpoint (only meaningful when AI is on).
if svc_running vllm; then
  if in_api python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://vllm:8000/v1/models').status==200 else 1)"; then
    model="$(in_api python -c "import urllib.request,json; print(json.load(urllib.request.urlopen('http://vllm:8000/v1/models'))['data'][0]['id'])" 2>/dev/null)"
    pass "vLLM serving${model:+ (model: $model)}"
  else warn "vLLM up but /v1/models not ready yet (model still loading?) — see: $DC logs -f vllm"; fi
fi

section "Resources"
avail_kb="$(df -Pk . | awk 'NR==2 {print $4}')"
avail_gb=$(( avail_kb / 1024 / 1024 ))
if [ "$avail_gb" -ge 50 ]; then pass "Disk free: ${avail_gb} GB"
elif [ "$avail_gb" -ge 20 ]; then warn "Disk free: ${avail_gb} GB (models can be tens of GB — consider more)"
else fail "Disk free: ${avail_gb} GB (low; model downloads may fail)"; fi

if command -v nvidia-smi >/dev/null 2>&1; then
  gpu="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)"
  [ -n "$gpu" ] && pass "GPU: $gpu" || warn "nvidia-smi present but no GPU reported"
elif [ "$ai_enabled" = "true" ]; then
  warn "No nvidia-smi on host — AI is enabled but a GPU/NVIDIA toolkit was not detected"
else pass "No GPU needed (AI disabled)"; fi

# Two-Spark cluster (ADR 0071) — advisory, and silent unless a peer has been
# enrolled (scripts/setup-cluster.sh writes CLUSTER_PEER_HOST to .env). Probes
# the peer's node-exporter over the QSFP link: the same endpoint the API's
# hardware profile watches before offering min_nodes:2 models.
cluster_peer_host="$(env_val CLUSTER_PEER_HOST)"
if [ -n "$cluster_peer_host" ]; then
  section "Cluster"
  cluster_peer_port="$(env_val CLUSTER_PEER_PORT)"; cluster_peer_port="${cluster_peer_port:-9100}"
  if command -v nc >/dev/null 2>&1; then
    peer_reachable() { nc -z -w 2 "$cluster_peer_host" "$cluster_peer_port" >/dev/null 2>&1; }
  else
    peer_reachable() { timeout 2 bash -c "exec 3<>/dev/tcp/${cluster_peer_host}/${cluster_peer_port}" 2>/dev/null; }
  fi
  if peer_reachable; then
    pass "peer worker reachable (${cluster_peer_host}:${cluster_peer_port} node-exporter) — cluster models can be offered"
  else
    warn "peer worker NOT reachable on ${cluster_peer_host}:${cluster_peer_port} — QSFP cable/link down, or the worker stack is stopped (on the peer: docker compose -f docker-compose.worker.yml up -d)"
  fi
fi

setup_checks

print_summary
