#!/usr/bin/env bash
#
# Family CFO — patch a running deployment.
#
# Rebuilds and recreates ONLY the application containers (api, worker, web) on a
# local or remote host. It never touches the vllm or db services and never
# removes a volume, so the AI model (in the model_cache volume) is NOT
# re-downloaded and the database is left alone. The api container applies any
# new database migrations on startup (additive), so a schema change ships with
# an `api` patch automatically.
#
# `ios` is a target too — it builds and installs the iPhone app onto a paired
# device over WiFi (scripts/deploy-ios.sh). The Apple Watch app rides along:
# it is embedded in the iPhone app, and the paired watch installs it
# automatically (M-watch, ADR 0067). It is NOT in the default set: you
# patch the phone when you mean to. But it composes with the container targets,
# which matters because an iOS change that needs an API or web change must ship
# BOTH halves — a phone talking to a box that lacks its endpoint is the failure
# this composition exists to prevent:
#
#   scripts/patch.sh api web ios
#
# The two halves run in different places: containers are rebuilt wherever the
# stack lives (local or remote over SSH), while `ios` always builds on the Mac
# you are sitting at, because that is where Xcode is. So if the box is remote,
# run the container half against it and the `ios` half from the Mac.
#
# Usage:
#   scripts/patch.sh                 # rebuild api + worker + web (local)
#   scripts/patch.sh web             # only the web container
#   scripts/patch.sh api worker      # a subset
#   scripts/patch.sh ios             # only the iPhone app (over WiFi)
#   scripts/patch.sh web ios         # the box's web tier AND the phone
#   SSH_HOST=box scripts/patch.sh web            # a remote box (TARGET inferred)
#   SSH_HOST="box1 box2" scripts/patch.sh web    # several boxes, in order
#   API_IMAGE_TAG=0.157.4 WEB_IMAGE_TAG=0.157.2 scripts/patch.sh api worker web
#                                    # pull released images, don't build
#
# API_IMAGE_TAG / WEB_IMAGE_TAG — deploy a KNOWN artifact instead of building one:
#   Without it (the default, and what every existing invocation does) the source
#   is rebuilt in place: `up -d --build`. What runs is therefore whatever was in
#   the working tree at that moment, which is not something you can look up
#   afterwards.
#   With it, the images published for that release tag are PULLED from GHCR and
#   started with --no-build, so the running artifact is one that was built once,
#   on a tag, and can be identified later. Requires a release whose images were
#   published by .github/workflows/release.yml.
#   They are SEPARATE variables because api and web carry their own build
#   numbers (ADR 0074): once the two diverge, one shared tag cannot name both,
#   and would reach for a family-cfo-web image that was never published. Set
#   only the one you are pinning — an unset variable builds that service from
#   source, as before.
#   What this pins and what it does NOT: the IMAGE is fixed. The database, .env
#   and the compose file are not — see docs/guides/deployment.md.
#
# Choosing the destination — the two halves differ, deliberately:
#   * The SERVER is DECLARED, never discovered. TARGET=local is this machine;
#     SSH_HOST names a remote box (and setting it implies TARGET=remote, so you
#     can't silently rebuild containers on your laptop by forgetting TARGET).
#     SSH_HOST may list several hosts — they are patched one at a time, in
#     order, stopping at the first failure.
#   * The PHONE is DISCOVERED, and never guessed at: with exactly one connected
#     device it is used, with several the run refuses until you name one with
#     IOS_DEVICE. See scripts/deploy-ios.sh.
#
# Environment overrides (same as scripts/deploy.sh):
#   TARGET local|remote  SSH_HOST (one or more)  SSH_USER  SSH_PORT  SSH_KEY  REMOTE_DIR
#   COMPOSE_FILES (default: -f docker-compose.yml)
#   API_IMAGE_TAG / WEB_IMAGE_TAG (default: empty — build from source, as before)
#   iOS-specific: IOS_DEVICE  IOS_CONFIG  IOS_TEST  NO_LAUNCH  (see scripts/deploy-ios.sh)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# COMPOSE_FILES resolves AFTER load_deploy_env (below) so a persisted value in
# .deploy.env (e.g. the ADR 0071 cluster overlay) survives routine patches; a
# real environment variable still outranks the file, and the plain default
# applies only when neither says otherwise.

# Services this script is allowed to rebuild. vllm and db are intentionally
# excluded: rebuilding vllm would reload the model, and db must never be
# recreated by a routine patch.
DEFAULT_SERVICES=(api worker web)
PROTECTED_SERVICES="vllm db"

# Empty = build from source, which is the behaviour every existing caller gets
# and the one this script has always had. Set = pull that published tag instead.
# The two paths are kept strictly separate below rather than parameterising the
# existing command, so an unset tag cannot change what runs.
API_IMAGE_TAG="${API_IMAGE_TAG:-}"
WEB_IMAGE_TAG="${WEB_IMAGE_TAG:-}"

# IMAGE_TAG named both images when there was one version for the whole repo. It
# cannot survive per-component builds (ADR 0074), and silently pinning only the
# api would be worse than refusing: you would think you deployed a release and
# have a web tier built from the working tree.
if [ -n "${IMAGE_TAG:-}" ]; then
  printf '\033[1;31mError:\033[0m IMAGE_TAG no longer exists — api and web ship separate builds now.\n' >&2
  printf '  Use API_IMAGE_TAG=%s and/or WEB_IMAGE_TAG=<tag> instead (ADR 0074).\n' "$IMAGE_TAG" >&2
  exit 1
fi

# Requested targets = positional args, or the safe default set.
if [ "$#" -gt 0 ]; then
  REQUESTED=("$@")
else
  REQUESTED=("${DEFAULT_SERVICES[@]}")
fi

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# The persisted destination (.deploy.env), so `scripts/patch.sh web` knows which
# box it means instead of quietly defaulting to this machine. An explicit
# environment variable still overrides it.
# shellcheck source=lib/deploy-env.sh
. "$REPO_ROOT/scripts/lib/deploy-env.sh"
load_deploy_env "$REPO_ROOT"

# docker-compose.yml used to consume one FAMILY_CFO_IMAGE_TAG for both images.
# It now consumes separate tags, so leaving the old value in either the process
# environment, .deploy.env, or Compose's .env would otherwise be a silent no-op.
legacy_compose_tag="${FAMILY_CFO_IMAGE_TAG:-}"
if [ -z "$legacy_compose_tag" ] && [ -f "$REPO_ROOT/.env" ]; then
  legacy_compose_tag="$(grep -E '^FAMILY_CFO_IMAGE_TAG=' "$REPO_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)"
fi
if [ -n "$legacy_compose_tag" ]; then
  die "FAMILY_CFO_IMAGE_TAG is obsolete and is now ignored by Compose.
       Remove it, then use API_IMAGE_TAG and/or WEB_IMAGE_TAG when invoking
       patch.sh (ADR 0074: api and web ship separate builds)."
fi

# The version scheme (ADR 0074): the contract in /VERSION plus each component's
# own BUILD. Deployables are compared by CONTRACT, so a backend-only patch does
# not make an unchanged app look stale.
# shellcheck source=lib/version.sh
. "$REPO_ROOT/scripts/lib/version.sh"
COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml}"

# How a target is routed: `ios` is the one reserved word and means the phone
# (scripts/deploy-ios.sh, built here on the Mac). Everything else must name a
# real service in the compose file, and is rebuilt on whichever host the stack
# lives on. Anything else is a typo, and is rejected below rather than being
# handed to Docker to fail on later.
PATCH_IOS=0
SERVICES=()
for target in "${REQUESTED[@]}"; do
  if [ "$target" = "ios" ]; then
    PATCH_IOS=1
  else
    SERVICES+=("$target")
  fi
done

detect_host_ip() {
  local ip
  ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
  [ -z "$ip" ] && ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "${ip:-localhost}"
}

ask() {
  local __var="$1" __prompt="$2" __default="${3:-}" __reply
  if [ -n "${!__var:-}" ]; then return; fi
  if [ -n "$__default" ]; then
    read -r -p "$__prompt [$__default]: " __reply || true
    printf -v "$__var" '%s' "${__reply:-$__default}"
  else
    read -r -p "$__prompt: " __reply || true
    printf -v "$__var" '%s' "$__reply"
  fi
}

# A deploy that leaves the box broken must not report success. Compose returning
# 0 means the containers were created, not that the app works: the API can start
# and then fail its migrations, and the web tier can serve a stale bundle. So
# ask the box what it is actually running and compare it to what was shipped.
run_local() { "$@"; }

# Pull mode is on when either component is pinned. They are independent, so
# `API_IMAGE_TAG=… patch.sh api worker` is a perfectly good half-deploy.
pull_mode() { [ -n "$API_IMAGE_TAG" ] || [ -n "$WEB_IMAGE_TAG" ]; }

# Tags are interpolated into a remote shell command as well as Docker image
# references. Accept only the release workflow's version shape: this both keeps
# the expected runtime version derivable and prevents shell metacharacters from
# becoming commands on a remote host.
validate_image_tags() {
  local name value
  for name in API_IMAGE_TAG WEB_IMAGE_TAG; do
    value="${!name:-}"
    [ -n "$value" ] || continue
    if ! printf '%s' "$value" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9][A-Za-z0-9.-]*)?$'; then
      die "${name}='${value}' is not a release tag (expected X.Y.Z or X.Y.Z-suffix)."
    fi
  done
}
validate_image_tags

# In pull mode every requested container must have a tag. Without this check the
# unpinned one resolves to `:dev`, which nothing publishes, and the run dies in
# `pull` with a registry error that does not say what you actually got wrong.
require_tags_for_services() {
  local svc
  for svc in "$@"; do
    case "$svc" in
      api|worker)
        [ -n "$API_IMAGE_TAG" ] || die "Patching '${svc}' in pull mode needs API_IMAGE_TAG (ADR 0074: api and web ship separate builds)." ;;
      web)
        [ -n "$WEB_IMAGE_TAG" ] || die "Patching 'web' in pull mode needs WEB_IMAGE_TAG (ADR 0074: api and web ship separate builds)." ;;
    esac
  done
}

# The tags compose passes through. Both are always exported: an empty one falls
# back to `dev` in docker-compose.yml, and require_tags_for_services has already
# refused any case where that would matter.
tag_env() {
  printf 'FAMILY_CFO_API_IMAGE_TAG=%s FAMILY_CFO_WEB_IMAGE_TAG=%s' \
    "${API_IMAGE_TAG:-dev}" "${WEB_IMAGE_TAG:-dev}"
}

# What to print so the log says which artifact went out.
tag_label() {
  local parts=""
  [ -n "$API_IMAGE_TAG" ] && parts="api ${API_IMAGE_TAG}"
  [ -n "$WEB_IMAGE_TAG" ] && parts="${parts}${parts:+, }web ${WEB_IMAGE_TAG}"
  printf '%s' "$parts"
}

requested_service() {
  local wanted="$1" svc
  for svc in ${SERVICES[@]+"${SERVICES[@]}"}; do
    [ "$svc" = "$wanted" ] && return 0
  done
  return 1
}

expected_patched_api_version() {
  requested_service api || return 0
  if [ -n "$API_IMAGE_TAG" ]; then
    # Pre-release tags name the same baked base version (0.157.4-rc1 reports
    # 0.157.4 from /app/VERSION).
    printf '%s' "${API_IMAGE_TAG%%-*}"
  else
    component_version "$REPO_ROOT" api
  fi
}

SMOKE_API_VERSION=""

smoke_check() {
  # $1: name of a function that runs its arguments ON the target
  #     (`run_local` here, the per-host `remote` in the remote path)
  # $2: the https base URL to probe, as seen FROM the target
  # $3: expected API version, or empty when this patch did not replace the API
  runner="$1"; base="$2"; expected="${3:-}"
  attempt=0
  while [ "$attempt" -lt 30 ]; do
    body="$("$runner" curl -sk --max-time 5 "${base}/api/v1/health" 2>/dev/null || true)"
    case "$body" in
      *'"status":"ok"'*)
        SMOKE_API_VERSION="$(printf '%s' "$body" | sed -n 's/.*"version":"\([^"]*\)".*/\1/p')"
        if [ -z "$expected" ]; then
          log "Verified: API healthy${SMOKE_API_VERSION:+ and reporting ${SMOKE_API_VERSION}} (API was not patched)."
          return 0
        fi
        case "$body" in
          *"\"version\":\"${expected}\""*)
            log "Verified: API healthy and reporting ${expected}."
            return 0
            ;;
          *)
            # Reachable but serving something else — a half-finished deploy, or
            # an image that never rebuilt. Worth failing on: the usual symptom
            # is a phone talking to endpoints the box does not have.
            warn "API is up but reports $(printf '%s' "$body" | tr -d '\n') — expected ${expected}."
            return 1
            ;;
        esac
        ;;
    esac
    attempt=$((attempt + 1))
    sleep 2
  done
  warn "API did not become healthy at ${base} within 60s."
  return 1
}

# The smoke check answers "is the code I just shipped running?". It cannot
# answer "is this box still set up correctly?" — and almost every incident this
# project has had was the second question: a certificate iOS would never accept,
# a renewal timer that had been failing nightly, a migration that never applied,
# an OTA bundle two versions behind. All of them were found by a person reading
# files, none by a deploy.
#
# So doctor's Setup section runs after every deploy. It is ADVISORY IN BOTH
# DIRECTIONS and never changes the outcome:
#   * a warning (certificate expiring in three weeks) must not block shipping a
#     fix — that would be the tail wagging the dog;
#   * a FAILURE is printed loudly but still does not roll back, because a
#     rollback would not repair it. The deploy is fine; the box's SETUP is not,
#     and reverting the code leaves the certificate just as expired.
# It runs on the TARGET, not here: it is that host's certificates, timers and
# database that are being inspected.
report_setup_drift() { # report_setup_drift <runner-taking-a-shell-string> <dir> <label>
  local runner="$1" dir="$2" label="$3"
  log "Setup checks on ${label} (advisory — they never fail a deploy)…"
  if "$runner" "cd '${dir}' && COMPOSE_FILES='${COMPOSE_FILES}' bash scripts/doctor.sh --setup-only"; then
    return 0
  fi
  warn "SETUP CHECKS FAILED on ${label} — see the report above.
   The deploy STANDS: the code shipped correctly and a rollback would not fix
   any of this. Something about the box's setup needs a human.
   Full report:  bash scripts/doctor.sh"
}

# run_local runs an argv; report_setup_drift hands its runner a shell command
# string (the remote `remote` helper already works that way, since ssh joins and
# re-parses). This is the local equivalent.
run_local_sh() { bash -c "$1"; }

# Refuse to patch a protected service (the whole point is to leave them alone).
for svc in ${SERVICES[@]+"${SERVICES[@]}"}; do
  for protected in $PROTECTED_SERVICES; do
    if [ "$svc" = "$protected" ]; then
      die "Refusing to rebuild '$svc' — it is protected (would reload the model / recreate the database). Restart it manually if you really need to."
    fi
  done
done

# #57: the guard above checks the list you PASS. It never stopped compose from
# acting on services it pulls in behind them: `api` declares
# depends_on: [db, vllm], so `up -d api` put vllm in the operation set and
# recreated it whenever its container no longer matched — reloading the model
# and taking chat down for minutes during a deploy that changed nothing about
# the AI runtime. The protection was real but sat one layer too high.
#
# Every `up` below therefore passes --no-deps: this script touches exactly the
# services it was asked to and nothing behind them.
#
# The cost of --no-deps is that dependencies are no longer started implicitly,
# so a stack that is DOWN is no longer brought up as a side effect. That is the
# right split — patch.sh patches a running stack, deploy.sh stands one up — but
# it must be said out loud rather than discovered, hence require_running_deps.
require_running_deps() {
  # $1: newline-separated "service<TAB>health" for every running service.
  #
  # HEALTH, not merely running. `api` declares
  # depends_on: db: condition: service_healthy, and --no-deps skips dependency
  # handling INCLUDING that wait — verified: with the dependency unhealthy,
  # plain `up -d app` refuses with "dependency failed to start: container is
  # unhealthy", while `up -d --no-deps app` starts it regardless. So the wait
  # compose used to perform has to be performed here, or this fix would trade a
  # vllm restart for starting the API against a database that is not ready.
  running="$1"
  # PRESENT and HEALTH are separate questions: compose prints an empty health
  # column both for a service that is absent and for one with no healthcheck.
  # Conflating them would report a running-but-uninstrumented database as down.
  if ! printf '%s\n' "$running" | awk '{print $1}' | grep -qx db; then
    die "The database is not running, and --no-deps means this script will
       not start it (#57). This patches a RUNNING stack; use scripts/deploy.sh
       to stand one up, or 'docker compose up -d db' first."
  fi
  db_health="$(printf '%s\n' "$running" | awk '$1 == "db" { print $2; exit }')"
  case "$db_health" in
    healthy|"")
      # Empty = no healthcheck defined; running is then the best signal there is.
      ;;
    *)
      die "The database is running but '${db_health}'. Compose would have waited
       for it (depends_on: condition: service_healthy); --no-deps does not, so
       the API would start against a database that is not ready. Wait for it to
       become healthy, or investigate with scripts/doctor.sh."
      ;;
  esac
  # vllm is deliberately NOT required: running without the local AI runtime is
  # a supported configuration (FAMILY_CFO_AI_ENABLED=false, or --scale vllm=0),
  # and demanding it would break those deployments for no benefit.
  case "$running" in
    *vllm*) ;;
    *) log "Note: vllm is not running; it will be left alone (--no-deps)." ;;
  esac
}

# Reject a name that is neither `ios` nor a real compose service, so a typo
# fails here with something readable instead of surfacing as a Docker error
# three steps later (or, worse, as a no-op that looks like success).
validate_services() { # validate_services <known-services...>
  local known="$*" svc
  for svc in ${SERVICES[@]+"${SERVICES[@]}"}; do
    case " $known " in
      *" $svc "*) ;;
      *) die "Unknown target '$svc'. Valid targets: ios (the iPhone app) or a compose service — ${known}." ;;
    esac
  done
  log "Patching services: ${SERVICES[*]}  (vllm + db left running, no volumes removed)"
}

patch_ios() {
  log "Patching the iPhone app over WiFi…"
  bash "$REPO_ROOT/scripts/deploy-ios.sh"
}

# Phone-only: nothing to do with Docker, so don't demand it (this may well be a
# Mac with no stack on it at all).
if [ "$PATCH_IOS" = "1" ] && [ "${#SERVICES[@]}" -eq 0 ]; then
  patch_ios
  exit 0
fi

# Local or remote is derived from WHERE YOU ARE (see resolve_target): on the box
# itself the stack is patched locally; from the MacBook it goes over SSH. The
# same .deploy.env is therefore correct on both machines, and forgetting TARGET
# can no longer rebuild containers on the laptop you happen to be sitting at.
resolve_target
[ "$TARGET" = "local" ] || [ "$TARGET" = "remote" ] || die "TARGET must be 'local' or 'remote'."

# =============================================================================
# LOCAL
# =============================================================================
if [ "$TARGET" = "local" ]; then
  command -v docker >/dev/null 2>&1 || die "docker is not installed."
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is required."
  [ -f .env ] || die ".env not found — is this deployment set up? Use scripts/deploy.sh first."

  # shellcheck disable=SC2086
  validate_services "$(docker compose $COMPOSE_FILES config --services 2>/dev/null | tr '\n' ' ')"

  # shellcheck disable=SC2086
  require_running_deps "$(docker compose $COMPOSE_FILES ps --format '{{.Service}}\t{{.Health}}' 2>/dev/null)"

  if pull_mode; then
    require_tags_for_services "${SERVICES[@]}"
    log "Pulling published images ($(tag_label)) and recreating…"
    # Both splits are deliberate: COMPOSE_FILES is a flag list, and tag_env
    # emits two VAR=value words for `env` to consume.
    # shellcheck disable=SC2086,SC2046
    env $(tag_env) docker compose $COMPOSE_FILES pull "${SERVICES[@]}"
    # An unpublished tag already failed the `pull` above (it exits non-zero,
    # and set -e stops here). --no-build is the second lock: without it compose
    # falls back to BUILDING a missing image from the synced source, which is
    # precisely the silent "you got the working tree, not the release" this
    # mode exists to prevent.
    # shellcheck disable=SC2086,SC2046
    env $(tag_env) docker compose $COMPOSE_FILES up -d --no-build --no-deps "${SERVICES[@]}"
  else
    log "Rebuilding and recreating…"
    # shellcheck disable=SC2086
    docker compose $COMPOSE_FILES up -d --build --no-deps "${SERVICES[@]}"
  fi

  web_tls_port="$(grep -E '^WEB_TLS_PORT=' .env | cut -d= -f2)"; web_tls_port="${web_tls_port:-8443}"
  record_deployment "$REPO_ROOT" local "$(detect_host_ip)" "" "" "$REPO_ROOT" "$COMPOSE_FILES"
  log "Patched. Dashboard: https://$(detect_host_ip):${web_tls_port}"
  expected_api_version="$(expected_patched_api_version)" \
    || die "Cannot derive the expected API version from the selected artifact."
  if ! smoke_check run_local "https://localhost:${web_tls_port}" "$expected_api_version"; then
    die "The patch completed but the box is not serving the expected version.
       Investigate with scripts/doctor.sh before trusting this deploy."
  fi
  report_setup_drift run_local_sh "$REPO_ROOT" "this host"
  # The phone goes last: it must never come up against a box that doesn't yet
  # have the endpoint it was built to call.
  [ "$PATCH_IOS" = "1" ] && patch_ios
  exit 0
fi

# =============================================================================
# REMOTE
# =============================================================================
command -v rsync >/dev/null 2>&1 || die "rsync is required for remote patches."
ask SSH_HOST "Remote host(s) — name or IP, space- or comma-separated for several"
[ -n "${SSH_HOST:-}" ] || die "SSH_HOST is required for a remote patch."
ask REMOTE_DIR "Remote directory" "~/family-cfo"

# SSH_USER / SSH_PORT / SSH_KEY are all OPTIONAL and are NOT prompted for.
#
# Left unset, ssh resolves them itself from ~/.ssh/config — which is the point:
# put a `Host` block there (User, Port, IdentityFile) and SSH_HOST can be a bare
# alias, so no username, no key path and above all NO PASSWORD ever needs to be
# typed into a script, stored in .deploy.env, or committed. Authentication stays
# between you, ssh-agent and the box.
#
# Setting them still works for a one-off against a host you have no config for.
SSH_USER="${SSH_USER:-}"
SSH_PORT="${SSH_PORT:-}"
SSH_KEY="${SSH_KEY:-}"

# SSH_HOST may name several boxes: SSH_HOST="box1 box2" or "box1,box2". They are
# patched one at a time, in order, and the run STOPS at the first failure — a
# half-patched fleet is easier to reason about than one that kept going after a
# box refused, and you can re-run for the rest.
IFS=', ' read -r -a SSH_HOSTS <<< "$SSH_HOST"
[ "${#SSH_HOSTS[@]}" -gt 0 ] || die "SSH_HOST is required for a remote patch."

patch_remote_host() { # patch_remote_host <host>
  local host="$1"
  # Only pass what was explicitly set: an empty -p or a forced user@ would
  # override the ~/.ssh/config block that is doing the authentication for us.
  # ConnectTimeout so an unreachable box fails in seconds instead of hanging.
  local ssh_opts=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  [ -n "${SSH_PORT:-}" ] && ssh_opts+=(-p "$SSH_PORT")
  [ -n "${SSH_KEY:-}" ] && ssh_opts+=(-i "$SSH_KEY")
  local ssh_target="$host"
  [ -n "${SSH_USER:-}" ] && ssh_target="${SSH_USER}@${host}"
  local rsh="ssh ${ssh_opts[*]}"
  remote() { ssh "${ssh_opts[@]}" "$ssh_target" "$@"; }

  log "── ${ssh_target} ─────────────────────────────────────────"
  log "Checking SSH + Docker on ${ssh_target}…"
  remote true || die "Cannot SSH to ${ssh_target}.
       If key-based SSH isn't set up yet, this does it (and never asks for a
       password):  scripts/setup-ssh.sh
       Check what's configured:      scripts/setup-ssh.sh --check"
  remote 'command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1' \
    || die "${ssh_target} is missing Docker Engine + Compose v2."

  local remote_abs
  remote_abs="$(remote "cd ${REMOTE_DIR} 2>/dev/null && pwd")" \
    || die "Remote directory ${REMOTE_DIR} not found on ${ssh_target} — deploy there first with scripts/deploy.sh."

  validate_services "$(remote "cd ${remote_abs} && docker compose ${COMPOSE_FILES} config --services 2>/dev/null" | tr '\n' ' ')"

  require_running_deps "$(remote "cd ${remote_abs} && docker compose ${COMPOSE_FILES} ps --format '{{.Service}}\t{{.Health}}' 2>/dev/null" || true)"

  # The sync happens in BOTH modes. In pull mode the synced source is not
  # what runs — the pulled image is — but docker-compose.yml itself has to be
  # present and current on the box for `pull` to know which images to fetch.
  # This is the honest limit of the reproducibility: the image is pinned to a
  # release, the compose file is still whatever this working tree holds.
  log "Syncing code to ${ssh_target}:${remote_abs} (excluding .env, data, build artifacts)…"
  rsync -az --delete \
    --exclude '.git' --exclude 'node_modules' --exclude '.venv' \
    --exclude '__pycache__' --exclude 'dist' --exclude '.angular' \
    --exclude 'data' --exclude '*.db' --exclude '.env' \
    -e "$rsh" "$REPO_ROOT/" "${ssh_target}:${remote_abs}/"

  if pull_mode; then
    require_tags_for_services "${SERVICES[@]}"
    log "Pulling published images ($(tag_label)) on ${ssh_target} and recreating…"
    # `pull` exits non-zero on a tag that was never published, so the && stops
    # before anything is recreated. --no-build then stops compose rebuilding a
    # missing image from the source it just synced.
    remote "cd ${remote_abs} && $(tag_env) docker compose ${COMPOSE_FILES} pull ${SERVICES[*]} && $(tag_env) docker compose ${COMPOSE_FILES} up -d --no-build --no-deps ${SERVICES[*]}"
  else
    log "Rebuilding and recreating on ${ssh_target}…"
    remote "cd ${remote_abs} && docker compose ${COMPOSE_FILES} up -d --build --no-deps ${SERVICES[*]}"
  fi

  local port
  port="$(remote "grep -E '^WEB_TLS_PORT=' ${remote_abs}/.env | cut -d= -f2" || true)"
  port="${port:-8443}"
  record_deployment "$REPO_ROOT" remote "$host" "$SSH_USER" "$SSH_PORT" "$remote_abs" "$COMPOSE_FILES"
  log "Patched ${host}. Dashboard: https://${host}:${port}"
  # Probe from the box itself: the dashboard host may not resolve from here,
  # and what matters is whether the stack is serving, not whether this laptop
  # can reach it.
  local expected_api_version
  expected_api_version="$(expected_patched_api_version)" \
    || die "Cannot derive the expected API version from the selected artifact."
  if ! smoke_check remote "https://localhost:${port}" "$expected_api_version"; then
    die "The patch completed on ${host} but it is not serving the expected version.
       Investigate: ssh ${ssh_target} 'cd ${remote_abs} && bash scripts/doctor.sh'"
  fi

  # ADR 0074 (amending 0029): after patching the box, check the published OTA
  # bundle is still COMPATIBLE — same contract. A silent contract drift here is
  # exactly how the phone ends up calling endpoints the box does not have. A
  # differing build number is not drift: that is the point of the scheme.
  local api_version ota_version
  api_version="$SMOKE_API_VERSION"
  ota_version="$(remote "cd ${remote_abs} && docker compose ${COMPOSE_FILES} exec -T web cat /usr/share/nginx/html/ota/VERSION 2>/dev/null" | tr -d '[:space:]' || true)"
  if [ -z "$ota_version" ]; then
    warn "No OTA bundle published yet (or it predates versioning) — run scripts/deploy-ios-ota.sh so the phone can install v${api_version}."
  elif ! versions_compatible "$ota_version" "$api_version"; then
    warn "OTA bundle is v${ota_version} but the box now runs v${api_version} — different contract, so the published app is STALE. Run scripts/deploy-ios-ota.sh."
  else
    log "OTA bundle is compatible (app v${ota_version}, box v${api_version})."
  fi

  # Last, so its report is the freshest thing on screen. `remote` already takes
  # a shell command string, which is exactly report_setup_drift's contract.
  report_setup_drift remote "$remote_abs" "$host"
}

log "Remote hosts to patch: ${SSH_HOSTS[*]}"
for host in "${SSH_HOSTS[@]}"; do
  patch_remote_host "$host"
done

# The phone goes last, and builds here on the Mac — not on any remote box, which
# has no Xcode. It is built ONCE regardless of how many servers were patched;
# the phone talks to whichever box it was paired with.
[ "$PATCH_IOS" = "1" ] && patch_ios
