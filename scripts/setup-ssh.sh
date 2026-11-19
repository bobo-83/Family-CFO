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

# shellcheck source=lib/deploy-env.sh
. "$REPO_ROOT/scripts/lib/deploy-env.sh"

ALIAS="${SSH_ALIAS:-family-cfo-box}"
KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH_CONFIG="$HOME/.ssh/config"
# Where the destination lives is NOT this script's decision — it asks the library
# that will later READ the file, so what setup writes is what deploy/patch load.
# Writing a fixed checkout path while the loader prefers ~/.family-cfo/ would
# produce a file nothing reads, and a patch at whatever the shared file says.
DEPLOY_ENV="$(deploy_env_file "$REPO_ROOT")"
# A fresh machine has neither file yet: create the machine-wide one, which is the
# documented home and correct from any directory and any clone.
[ -f "$DEPLOY_ENV" ] || DEPLOY_ENV="${DEPLOY_ENV:-$HOME/.family-cfo/.deploy.env}"

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
    ok "$(basename "$DEPLOY_ENV") → $(grep '^SSH_HOST=' "$DEPLOY_ENV")  ($DEPLOY_ENV)"
  else
    warn "no $(basename "$DEPLOY_ENV") at $DEPLOY_ENV (deploys won't know which box to go to)"
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

# Everything in the file that says WHICH BOX. These are replaced together, as one
# address, because they describe one login: SSH_USER, SSH_PORT and SSH_KEY are the
# OLD box's user, port and private key. Carrying them onto a new host would
# reproduce, by way of setup, the very destination this library exists to prevent
# — `old-user@new-box:2222` with the old box's key, which is not a box anyone
# meant to reach. Leaving them unset is the design working: ssh resolves user,
# port and key from ~/.ssh/config + ssh-agent (AGENTS.md), and the alias this
# script just installed is what makes that enough.
#
# Anything else in the file — IOS_TEAM_ID, ASC_KEY_ID, IOS_DEVICE, whatever the
# iOS and TestFlight scripts read — is not an address and is left exactly as is.
OWNED_KEYS="SSH_HOST SSH_USER SSH_PORT SSH_KEY REMOTE_DIR"

# Ask the LOADER what the scripts would actually use, rather than grepping the
# file for a literal key. Only the loader knows which of the two files wins, that
# the FIRST value of a key is the one it keeps, and how it normalises spacing — a
# `grep '^SSH_HOST='` can happily report the box we just wrote while the loader
# still resolves the padded ` SSH_HOST = old ` above it. Unset first, so an
# environment variable (which legitimately outranks the file) cannot mask what the
# FILE says, which is the thing being verified here.
effective_value() { # effective_value <KEY> → what load_deploy_env would give for it
  local key="$1"
  ( unset "$key"
    load_deploy_env "$REPO_ROOT" >/dev/null 2>&1
    printf '%s' "${!key-}" )
}

want_dir="${REMOTE_DIR:-~/family-cfo}"
if [ -f "$DEPLOY_ENV" ] && [ "$(effective_value SSH_HOST)" = "$ALIAS" ]; then
  ok "Already points at '${ALIAS}'  ($DEPLOY_ENV)"
else
  mkdir -p -- "$(dirname -- "$DEPLOY_ENV")"
  # UPDATE, never rewrite. This file is now the machine-wide config, so it can
  # hold an Apple team id, TestFlight key ids, an IOS_DEVICE — facts that are
  # expensive to recreate and have nothing to do with which box is targeted.
  # Only the keys in OWNED_KEYS are replaced; everything else survives verbatim.
  #
  # The read is checked, for the same reason the registry's is (see
  # lib/deploy-env.sh): an unreadable file here would otherwise leave the writer
  # holding nothing, and the rewrite would install a two-line file over whatever
  # the operator had. Empty and unreadable look identical to a writer.
  #
  # Which lines are "ours" is decided by deploy_env_key_of_line — the loader's own
  # rule, not a `grep '^SSH_HOST='` — because the loader strips comments and ALL
  # whitespace from a key and keeps its first value. Match literal text instead
  # and ` SSH_HOST = old ` survives above the line appended below, keeps winning,
  # and setup reports a destination the deploy never uses.
  kept="" cleared="" fresh=1
  if [ -f "$DEPLOY_ENV" ]; then
    fresh=0
    if ! lines="$(cat -- "$DEPLOY_ENV")"; then
      die "Could not read $DEPLOY_ENV — refusing to rewrite it, because everything in it besides the destination would be lost. Fix its permissions and run this again."
    fi
    while IFS= read -r line || [ -n "$line" ]; do
      key="$(deploy_env_key_of_line "$line")"
      is_owned=0
      for owned in $OWNED_KEYS; do
        # Explicit comparison, not `case` on a pattern: a key can contain '*' or
        # '?', and a glob there would drop lines that are not ours.
        [ "$key" = "$owned" ] && { is_owned=1; break; }
      done
      if [ "$is_owned" = 1 ]; then
        # Say what was dropped: silently changing how ssh authenticates to a box
        # is the sort of surprise an operator should not have to discover by
        # debugging. SSH_HOST/REMOTE_DIR are being replaced with stated values.
        case "$key" in
          SSH_HOST|REMOTE_DIR) : ;;
          *) value="$(deploy_env_value_of_line "$line")"
             [ -n "$value" ] && cleared="${cleared}${cleared:+ }${key}=${value}" ;;
        esac
        continue
      fi
      kept="${kept}${kept:+$'\n'}${line}"
    done <<< "$lines"
  fi
  tmp="$(mktemp "${DEPLOY_ENV}.XXXXXX")" ||
    die "Could not create a temporary file beside $DEPLOY_ENV — check its directory's permissions."
  {
    # `if` rather than `[ -n "$kept" ] && printf` for symmetry with the branch
    # below, not because the short form could abort anything: the group's status
    # is tested by the `||` under it, which puts the whole group in a condition
    # context where errexit is off. What that means in practice is written down
    # because it is the load-bearing part: the two destination lines are the LAST
    # commands here, so if they fail the group is non-zero and the `||` removes
    # the temp file instead of installing a truncated destination.
    if [ -n "$kept" ]; then printf '%s\n' "$kept"; fi
    if [ "$fresh" = 1 ]; then
      printf '# Family CFO deploy destination — written by scripts/setup-ssh.sh\n'
      printf '# Holds a DESTINATION, never a credential. Authentication lives in\n'
      printf '# ~/.ssh/config + ssh-agent (see AGENTS.md).\n'
      printf '#\n'
      printf '# No TARGET on purpose: local-vs-remote is derived from where you are.\n'
      printf '# On the box itself this patches locally; from the Mac it goes over SSH.\n'
    fi
    printf 'SSH_HOST=%s\n' "$ALIAS"
    printf 'REMOTE_DIR=%s\n' "$want_dir"
  } > "$tmp" || { rm -f -- "$tmp"; die "Could not write the new $DEPLOY_ENV."; }
  mv -f -- "$tmp" "$DEPLOY_ENV"
  # mktemp creates 0600 and mv preserves it, so the mode does not depend on this
  # line — it is here so the intent survives someone swapping the temp strategy.
  # No `--`: BSD chmod rejects it ("chmod: --: No such file or directory"). Same
  # form as the ~/.ssh/config write above.
  chmod 600 "$DEPLOY_ENV"

  # BELIEVE NOTHING BUT THE LOADER. The file is written; what matters is the
  # destination the next `scripts/patch.sh` will resolve, and only the loader
  # gets to say. If it disagrees, something else is in charge (a stale checkout
  # file, a duplicated key the operator added by hand) and saying "wrote
  # new-box" would be the lie that wastes the operator's afternoon.
  got_host="$(effective_value SSH_HOST)"
  got_dir="$(effective_value REMOTE_DIR)"
  if [ "$got_host" != "$ALIAS" ] || [ "$got_dir" != "$want_dir" ]; then
    die "Wrote $DEPLOY_ENV but the scripts still resolve '${got_host:-<unset>}' / '${got_dir:-<unset>}'. Something else is setting the destination — check for a second .deploy.env ('$REPO_ROOT/.deploy.env' is ignored only while $DEPLOY_ENV exists) and for duplicated SSH_HOST/REMOTE_DIR lines in it."
  fi
  ok "Wrote $DEPLOY_ENV → SSH_HOST=${ALIAS} REMOTE_DIR=${want_dir}  (verified: this is what deploy/patch load)"
  if [ -n "$cleared" ]; then
    warn "Cleared the previous box's login settings from it: ${cleared}"
    warn "They described the OLD host; ssh resolves user, port and key from"
    warn "~/.ssh/config + ssh-agent now. Put them back by hand only if the new box"
    warn "genuinely uses the same ones."
  fi
  if [ "$DEPLOY_ENV" != "$REPO_ROOT/.deploy.env" ] && [ -f "$REPO_ROOT/.deploy.env" ]; then
    warn "$REPO_ROOT/.deploy.env also exists but is NOT read while $DEPLOY_ENV is."
    warn "Move anything you still need into it and delete the other, or deploys will"
    warn "use a file you are not editing."
  fi
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
