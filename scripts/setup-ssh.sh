#!/usr/bin/env bash
#
# Family CFO — set up key-based SSH to the box, so deploys need no password.
#
# Gets you from "nothing configured" to `scripts/patch.sh api web` reaching the
# Linux box with no credential anywhere in the repo:
#
#   1. an ed25519 key (created if you have none)
#   2. that key authorised on the box, via ssh-copy-id
#   3. a ~/.ssh/config alias, so the scripts never need a username or key path
#   4. .deploy.env pointing at that alias
#
# THIS SCRIPT NEVER ASKS FOR, READS, OR STORES A PASSWORD (AGENTS.md,
# docs/specs/06-security-model.md). It asks for a HOST and a LOGIN NAME — neither
# is a secret. The one moment a password is typed is ssh-copy-id's own prompt,
# which reads straight from your terminal into ssh: this script never sees it,
# and it is never written anywhere.
#
# That guarantee only holds on a real terminal. Run without one (in CI, or from
# an AI agent's tool call), the script REFUSES to run ssh-copy-id and prints the
# command for you to run yourself — because a password prompt piped through some
# other program is exactly the disclosure the rule exists to prevent.
#
# Usage:
#   scripts/setup-ssh.sh                       # interactive
#   scripts/setup-ssh.sh 192.168.1.10        # host on the command line
#   SSH_LOGIN=alex scripts/setup-ssh.sh 192.168.1.10   # non-interactive-ish
#   scripts/setup-ssh.sh --check               # just report what's set up
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ALIAS="${SSH_ALIAS:-family-cfo-box}"
KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH_CONFIG="$HOME/.ssh/config"
DEPLOY_ENV="$REPO_ROOT/.deploy.env"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# A password must never arrive through the environment either — that puts it in
# the process listing and the shell history of whoever ran us.
for var in SSH_PASSWORD SSHPASS PASSWORD BOX_PASSWORD; do
  if [ -n "${!var:-}" ]; then
    die "Refusing to run: \$${var} is set. This project never handles passwords
       (AGENTS.md). Unset it — ssh-copy-id will ask you directly, and only on
       your own terminal, so the password reaches ssh and nothing else."
  fi
done

# Can key auth already reach the box as <target>? BatchMode so a password prompt
# counts as failure rather than hanging.
key_auth_works() { # key_auth_works <target>
  ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new \
    "$1" true 2>/dev/null
}

have_alias() {
  [ -f "$SSH_CONFIG" ] && grep -qiE "^[[:space:]]*Host[[:space:]]+${ALIAS}([[:space:]]|$)" "$SSH_CONFIG"
}

# --- --check -----------------------------------------------------------------
if [ "${1:-}" = "--check" ]; then
  step "Current SSH setup"
  [ -f "$KEY" ] && ok "key: $KEY" || warn "no key at $KEY"
  if have_alias; then
    ok "~/.ssh/config has a '${ALIAS}' block"
    if key_auth_works "$ALIAS"; then
      ok "key auth to '${ALIAS}' WORKS — deploys need no password"
    else
      warn "'${ALIAS}' is configured but key auth does not work yet"
    fi
  else
    warn "no '${ALIAS}' block in ~/.ssh/config"
  fi
  if [ -f "$DEPLOY_ENV" ] && grep -q "^SSH_HOST=" "$DEPLOY_ENV"; then
    ok ".deploy.env → $(grep '^SSH_HOST=' "$DEPLOY_ENV")"
  else
    warn "no .deploy.env (deploys won't know which box to go to)"
  fi
  exit 0
fi

step "Family CFO — SSH setup for password-free deploys"
echo "Nothing here is secret: this script asks for a host and a login name, never"
echo "a password. See AGENTS.md → 'Credentials — Never Ask, Never Handle'."

# --- 1. The box (a destination, not a secret) --------------------------------
HOST="${1:-${SSH_HOST:-}}"
if [ -z "$HOST" ]; then
  if [ -f "$DEPLOY_ENV" ]; then
    HOST="$(grep -E '^SSH_HOST=' "$DEPLOY_ENV" | head -1 | cut -d= -f2- || true)"
  fi
fi
if [ -z "$HOST" ] || [ "$HOST" = "$ALIAS" ]; then
  if [ -t 0 ]; then
    printf '\nBox hostname or IP [192.168.1.10]: '
    read -r reply || true
    HOST="${reply:-192.168.1.10}"
  else
    die "No host given. Pass it: scripts/setup-ssh.sh 192.168.1.10"
  fi
fi

# --- 2. The login name (also not a secret) -----------------------------------
LOGIN="${SSH_LOGIN:-}"
if [ -z "$LOGIN" ]; then
  if [ -t 0 ]; then
    printf 'Your login name ON THE BOX (not a password) [%s]: ' "${USER:-}"
    read -r reply || true
    LOGIN="${reply:-${USER:-}}"
  else
    die "No login name. Pass it: SSH_LOGIN=yourname scripts/setup-ssh.sh ${HOST}"
  fi
fi
[ -n "$LOGIN" ] || die "A login name is required."

log "Box: ${LOGIN}@${HOST}   alias: ${ALIAS}"

# --- 3. A key ----------------------------------------------------------------
step "1/4  SSH key"
if [ -f "$KEY" ]; then
  ok "Using the existing key: $KEY"
else
  log "No key at $KEY — creating one."
  echo "    ssh-keygen will offer to set a PASSPHRASE. That passphrase is yours;"
  echo "    it protects the key on this Mac and is never sent anywhere. Leave it"
  echo "    empty for no passphrase, or set one and let ssh-agent hold it."
  [ -t 0 ] || die "Creating a key needs a terminal (ssh-keygen asks for a passphrase).
       Run this script yourself, or: ssh-keygen -t ed25519 -f $KEY"
  ssh-keygen -t ed25519 -f "$KEY" -C "family-cfo $(hostname -s 2>/dev/null || echo mac)"
  ok "Created $KEY"
fi

# --- 4. Authorise it on the box ----------------------------------------------
step "2/4  Authorise the key on the box"
if key_auth_works "${LOGIN}@${HOST}"; then
  ok "Key auth already works — nothing to copy."
else
  echo "The key isn't authorised on ${HOST} yet, so ssh-copy-id must send it."
  echo "It will ask for your password ON THE BOX. That prompt is ssh-copy-id's own:"
  echo "the password goes straight into ssh. This script never sees or stores it."
  echo

  if [ ! -t 0 ] || [ ! -t 1 ]; then
    warn "No terminal attached — REFUSING to run ssh-copy-id here."
    echo
    echo "  A password prompt piped through another program (a CI job, an AI"
    echo "  agent's tool call) is exactly the disclosure this project forbids."
    echo "  Run this ONE command in your own terminal, then re-run this script:"
    echo
    printf '      \033[1mssh-copy-id -i %s.pub %s@%s\033[0m\n' "$KEY" "$LOGIN" "$HOST"
    echo
    echo "  Everything after that step is automatic."
    exit 2
  fi

  ssh-copy-id -i "${KEY}.pub" "${LOGIN}@${HOST}" \
    || die "ssh-copy-id failed. Check the login name and that the box allows password auth."
  key_auth_works "${LOGIN}@${HOST}" \
    || die "The key was copied but key auth still fails. Check permissions on the box: ~/.ssh must be 700 and ~/.ssh/authorized_keys 600."
  ok "Key authorised — password auth is no longer needed."
fi

# --- 5. ~/.ssh/config alias --------------------------------------------------
step "3/4  ~/.ssh/config alias '${ALIAS}'"
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
if have_alias; then
  ok "Already present — leaving it alone."
else
  [ -f "$SSH_CONFIG" ] && cp "$SSH_CONFIG" "${SSH_CONFIG}.familycfo.bak" \
    && log "Backed up ~/.ssh/config → ~/.ssh/config.familycfo.bak"
  {
    printf '\n# Family CFO box — added by scripts/setup-ssh.sh\n'
    printf 'Host %s\n' "$ALIAS"
    printf '    HostName %s\n' "$HOST"
    printf '    User %s\n' "$LOGIN"
    printf '    IdentityFile %s\n' "$KEY"
    printf '    IdentitiesOnly yes\n'
  } >> "$SSH_CONFIG"
  chmod 600 "$SSH_CONFIG"
  ok "Added the '${ALIAS}' block."
fi

# --- 6. .deploy.env ----------------------------------------------------------
step "4/4  .deploy.env"
if [ -f "$DEPLOY_ENV" ] && grep -qE "^SSH_HOST=${ALIAS}$" "$DEPLOY_ENV"; then
  ok "Already points at '${ALIAS}'."
else
  {
    printf '# Family CFO deploy destination — written by scripts/setup-ssh.sh\n'
    printf '# Holds a DESTINATION, never a credential. Authentication lives in\n'
    printf '# ~/.ssh/config + ssh-agent (see AGENTS.md).\n'
    printf '#\n'
    printf '# No TARGET on purpose: local-vs-remote is derived from where you are.\n'
    printf '# On the box itself this patches locally; from the Mac it goes over SSH.\n'
    printf 'SSH_HOST=%s\n' "$ALIAS"
    printf 'REMOTE_DIR=%s\n' "${REMOTE_DIR:-~/family-cfo}"
  } > "$DEPLOY_ENV"
  ok "Wrote .deploy.env → SSH_HOST=${ALIAS}  (gitignored)"
fi

# --- 7. Prove it ------------------------------------------------------------
step "Verifying"
key_auth_works "$ALIAS" || die "Cannot reach '${ALIAS}' with key auth. Check ~/.ssh/config."
ok "ssh ${ALIAS} works with no password."

if ssh -o BatchMode=yes -o ConnectTimeout=8 "$ALIAS" \
     'command -v docker >/dev/null && docker compose version >/dev/null 2>&1' 2>/dev/null; then
  ok "Docker + Compose v2 present on the box."
else
  warn "Reached the box, but Docker Engine + Compose v2 wasn't found — deploys need it."
fi

printf '\n\033[1;32mDone.\033[0m No password is stored anywhere; deploys authenticate with your key.\n\n'
echo "  scripts/patch.sh api web      # patch the box (goes to ${ALIAS})"
echo "  scripts/patch.sh api web ios  # box first, then the iPhone"
echo "  scripts/deployments.sh        # what's deployed, and is it still running?"
echo
echo "  Run this from the box itself and the same .deploy.env patches locally —"
echo "  the target is derived from where you are, not from a flag."
