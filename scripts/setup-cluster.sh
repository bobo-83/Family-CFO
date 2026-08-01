#!/usr/bin/env bash
#
# Family CFO — enroll a second DGX Spark as a tensor-parallel worker
# (ADR 0071: two-Spark cluster over the ConnectX QSFP 200GbE link).
#
# One-time. Run ON THE HEAD BOX — the machine where the stack and its .env
# live — because it edits that .env in place (the swap-model.sh set_env
# pattern). From the head it:
#
#   1. verifies key-based SSH to the peer works (and never asks for a password)
#   2. ships docker-compose.worker.yml + a minimal generated .env to
#      ~/family-cfo-worker/ on the peer and starts the worker stack there
#      (ray-worker + node-exporter — the peer runs ONLY that file)
#   3. records the cluster in this repo's .env: CLUSTER_PEER_HOST,
#      CLUSTER_PEER_PORT, CLUSTER_NCCL_IFNAME, CLUSTER_HEAD_ADDR
#
# THIS SCRIPT NEVER ASKS FOR, READS, OR STORES A PASSWORD (AGENTS.md,
# docs/specs/06-security-model.md). If key auth to the peer is missing, the
# one moment a password may be typed is ssh-copy-id's own prompt, straight
# from your terminal into ssh. That only holds on a real terminal: run
# without one (CI, an AI agent's tool call) and the script REFUSES to run
# ssh-copy-id and prints the command for you to run yourself.
#
# The link addresses are DECLARED, never discovered: --link-ip / --head-ip
# name the static addresses you gave the QSFP interfaces, and --ifname names
# that interface on BOTH nodes. Nothing here probes the fabric to guess them.
#
# Usage:
#   scripts/setup-cluster.sh spark2
#   scripts/setup-cluster.sh spark2 --link-ip 10.0.0.2 --head-ip 10.0.0.1 --ifname enp1s0f0
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

usage() {
  cat <<'EOF'
usage: scripts/setup-cluster.sh <peer-host-or-alias> [options]

  --link-ip IP    peer's address on the QSFP link  (default 10.0.0.2)
  --head-ip IP    this box's address on the QSFP link  (default 10.0.0.1)
  --ifname NAME   the QSFP interface name, same on BOTH nodes  (default enp1s0f0)

Run on the head box (where the stack's .env lives). Example:
  scripts/setup-cluster.sh spark2 --link-ip 10.0.0.2 --head-ip 10.0.0.1 --ifname enp1s0f0
EOF
}

# A password must never arrive through the environment either — that puts it
# in the process listing and the shell history of whoever ran us.
for var in SSH_PASSWORD SSHPASS PASSWORD BOX_PASSWORD; do
  if [ -n "${!var:-}" ]; then
    die "Refusing to run: \$${var} is set. This project never handles passwords
       (AGENTS.md). Unset it — ssh-copy-id will ask you directly, and only on
       your own terminal, so the password reaches ssh and nothing else."
  fi
done

# --- Arguments ---------------------------------------------------------------
PEER=""
LINK_IP="10.0.0.2"
HEAD_IP="10.0.0.1"
IFNAME="enp1s0f0"
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --link-ip) [ "$#" -ge 2 ] || die "--link-ip needs a value"; LINK_IP="$2"; shift 2 ;;
    --head-ip) [ "$#" -ge 2 ] || die "--head-ip needs a value"; HEAD_IP="$2"; shift 2 ;;
    --ifname)  [ "$#" -ge 2 ] || die "--ifname needs a value";  IFNAME="$2"; shift 2 ;;
    -*) die "Unknown option '$1' (see --help)" ;;
    *) [ -z "$PEER" ] || die "Only one peer host, please ('$PEER' already given)."; PEER="$1"; shift ;;
  esac
done
[ -n "$PEER" ] || { usage >&2; exit 1; }

# Run where the stack lives: this script edits the stack's .env in place.
[ -f .env ] || die ".env not found — run this ON THE HEAD BOX, in the directory the
       stack is deployed from (it records the cluster in that .env)."

step "Family CFO — enroll '${PEER}' as a cluster worker (ADR 0071)"
log "QSFP link: head ${HEAD_IP} <-> peer ${LINK_IP} on interface '${IFNAME}' (both nodes)"

# Can key auth already reach the peer? BatchMode so a password prompt counts
# as failure rather than hanging. (Same pattern as scripts/setup-ssh.sh.)
key_auth_works() { # key_auth_works <target>
  ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new \
    "$1" true 2>/dev/null
}
peer() { ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$PEER" "$@"; }

# --- 1. Key auth to the peer -------------------------------------------------
step "1/4  SSH to the peer"
if key_auth_works "$PEER"; then
  ok "Key auth to '${PEER}' works — no password needed."
else
  echo "Key auth to '${PEER}' doesn't work yet, so ssh-copy-id must send a key."
  echo "Its password prompt is ssh-copy-id's own: the password goes straight into"
  echo "ssh. This script never sees or stores it."
  echo
  if [ ! -t 0 ] || [ ! -t 1 ]; then
    warn "No terminal attached — REFUSING to run ssh-copy-id here."
    echo
    echo "  A password prompt piped through another program (a CI job, an AI"
    echo "  agent's tool call) is exactly the disclosure this project forbids."
    echo "  Run this ONE command in your own terminal, then re-run this script:"
    echo
    printf '      \033[1mssh-copy-id -i %s.pub %s\033[0m\n' "$KEY" "$PEER"
    echo
    echo "  (No key at ${KEY}? scripts/setup-ssh.sh explains creating one, or:"
    echo "   ssh-keygen -t ed25519 -f ${KEY})"
    exit 2
  fi
  [ -f "$KEY" ] || die "No key at ${KEY}. Create one first (ssh-keygen -t ed25519 -f ${KEY}),
       or run scripts/setup-ssh.sh which walks through it."
  ssh-copy-id -i "${KEY}.pub" "$PEER" \
    || die "ssh-copy-id failed. Check the host/alias and that '${PEER}' allows password auth."
  key_auth_works "$PEER" \
    || die "The key was copied but key auth still fails. On the peer, ~/.ssh must be 700 and ~/.ssh/authorized_keys 600."
  ok "Key authorised on '${PEER}'."
fi

peer 'command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1' \
  || die "'${PEER}' is missing Docker Engine + Compose v2 — install them there first."
ok "Docker + Compose v2 present on '${PEER}'."

# --- 2. Ship the worker stack ------------------------------------------------
step "2/4  Deploy the worker stack to ${PEER}:~/family-cfo-worker/"
command -v rsync >/dev/null 2>&1 || die "rsync is required."

WORKER_DIR="family-cfo-worker"
staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
cp "$REPO_ROOT/docker-compose.worker.yml" "$staging/"
# The worker builds the same vllm+Ray image the head uses (upstream ships no
# Ray); the Dockerfile rides along so `docker compose build` works there.
mkdir -p "$staging/vllm-cluster"
cp "$REPO_ROOT/docker/vllm-cluster/Dockerfile" "$staging/vllm-cluster/"
{
  printf '# Family CFO worker node — written by scripts/setup-cluster.sh (ADR 0071).\n'
  printf '# Addresses are DECLARED on the head box, not discovered here.\n'
  printf 'CLUSTER_HEAD_ADDR=%s\n' "$HEAD_IP"
  printf 'CLUSTER_NCCL_IFNAME=%s\n' "$IFNAME"
} > "$staging/.env"

peer "mkdir -p ~/${WORKER_DIR}"
rsync -az "$staging/" "${PEER}:${WORKER_DIR}/"
ok "Synced docker-compose.worker.yml + .env (CLUSTER_HEAD_ADDR=${HEAD_IP})."

log "Starting ray-worker + node-exporter on '${PEER}'…"
peer "cd ~/${WORKER_DIR} && docker compose -f docker-compose.worker.yml up -d" \
  || die "docker compose up failed on '${PEER}' — inspect with:
       ssh ${PEER} 'cd ~/${WORKER_DIR} && docker compose -f docker-compose.worker.yml logs'"
ok "Worker stack is up on '${PEER}'."

# Dependency-free probe of the peer's node-exporter, from the peer itself —
# this is the endpoint the head's API watches for cluster health.
if peer 'timeout 2 bash -c "exec 3<>/dev/tcp/127.0.0.1/9100" 2>/dev/null'; then
  ok "node-exporter answering on ${PEER}:9100."
else
  warn "node-exporter not answering on ${PEER}:9100 yet — check:
     ssh ${PEER} 'cd ~/${WORKER_DIR} && docker compose -f docker-compose.worker.yml ps'"
fi

# --- 3. Record the cluster in this stack's .env ------------------------------
step "3/4  Record the cluster in .env"
set_env() { # set_env KEY VALUE — update in place or append (as swap-model.sh)
  local key="$1" value="$2"
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}
# The PEER_HOST is the LINK IP, not the LAN alias: health then rides the same
# cable the tensor traffic does, so "peer healthy" means "cluster can serve".
set_env CLUSTER_PEER_HOST "$LINK_IP"
set_env CLUSTER_PEER_PORT "9100"
set_env CLUSTER_NCCL_IFNAME "$IFNAME"
set_env CLUSTER_HEAD_ADDR "$HEAD_IP"
ok "Wrote CLUSTER_PEER_HOST=${LINK_IP}, CLUSTER_PEER_PORT=9100, CLUSTER_NCCL_IFNAME=${IFNAME}, CLUSTER_HEAD_ADDR=${HEAD_IP}"

# --- 4. What this script could and could not verify --------------------------
step "4/4  Link check (advisory)"
if ping -c 1 -W 2 "$LINK_IP" >/dev/null 2>&1; then
  ok "Peer answers on the link address ${LINK_IP}."
else
  warn "No answer from ${LINK_IP} — the QSFP link is not up yet (cable unplugged,
     or the '${IFNAME}' interfaces aren't configured with these addresses).
     Enrollment is still recorded; scripts/doctor.sh will keep checking."
fi

printf '\n\033[1;32mEnrolled.\033[0m What is verified: SSH, Docker, the worker containers, node-exporter.\n'
echo "What is NOT verifiable until the cable is up and a model loads: NCCL over"
echo "the QSFP link, and that both nodes run the SAME vllm/vllm-openai image"
echo "(a version skew fails only at model load). Next steps:"
echo
echo "  # restart the stack with the cluster overlay:"
echo "  COMPOSE_FILES=\"-f docker-compose.yml -f docker-compose.cluster.yml\" scripts/patch.sh api"
echo
echo "  # then pick a cluster-tier (min_nodes: 2) model, e.g.:"
echo "  COMPOSE_FILES=\"-f docker-compose.yml -f docker-compose.cluster.yml\" \\"
echo "    scripts/swap-model.sh unsloth/Qwen3-235B-A22B-Instruct-2507-NVFP4"
echo
echo "  scripts/doctor.sh   # now includes an advisory Cluster section"
