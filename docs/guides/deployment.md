# Home-Server Deployment Guide

Family CFO runs as a Docker Compose stack on a machine you control. This guide
takes you from a clean checkout to a running, TLS-served dashboard.

## Quick start: one-command deploy

`scripts/deploy.sh` stands the whole stack up (dashboard + API + worker + DB +
vLLM) on a **local** or **remote** host and prints the dashboard URL. It
generates a `.env` with random secrets on first run.

```bash
scripts/deploy.sh                 # interactive: choose local or remote (SSH)
TARGET=local scripts/deploy.sh    # non-interactive local
TARGET=remote SSH_HOST=my-box SSH_USER=me scripts/deploy.sh
```

For a remote host it prompts for SSH host/user/port/key, verifies Docker (and
the NVIDIA Container Toolkit, since the AI runtime is on by default), rsyncs the
repo, and runs Compose there. The manual steps below are the same thing done by
hand, plus the configuration reference.

## Prerequisites

- Docker Engine 24+ and the Compose plugin (`docker compose version`).
- A host you trust on your local network. Family CFO is single-tenant and
  self-hosted by design (ADR 0006); it is not built to be exposed raw to the
  public internet — see [Security](./security.md).

## 1. Configure

```bash
git clone <your-fork-or-clone-url> Family-CFO
cd Family-CFO
cp .env.example .env
```

Edit `.env` and set, at minimum:

- `POSTGRES_PASSWORD` — a strong password. The stack refuses to start without it.
- `FAMILY_CFO_BACKUP_ENCRYPTION_KEY` — required before you can take backups
  (it also encrypts linked-institution credentials, M27).
  Generate one:

  ```bash
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

  Store this key in your own secret manager. **Losing it makes existing backups
  permanently unrecoverable** (ADR 0008).

Optional: `WEB_TLS_PORT` (default 8443), `WEB_PORT` (default 8080, HTTP→HTTPS
redirect), `FAMILY_CFO_SESSION_TTL_HOURS` (default 12).

## 2. Start

```bash
docker compose up -d
```

This builds and starts the core stack: PostgreSQL, the API (which runs database
migrations on startup), the background worker, and the nginx-served dashboard.
Wait for the API to become healthy:

```bash
docker compose ps
```

The dashboard is at **`https://localhost:8443`** (or your `WEB_TLS_PORT`). On
first start the web container generates a self-signed certificate, so your
browser will warn about it — expected. See [Security](./security.md) to install
a real certificate.

## 3. Create your household (first run)

Open the dashboard (`https://localhost:8443/`, or your box's address). On a fresh
instance the login page offers a **“create a new household”** link → the
**Create your household** screen. Enter the owner's name, email, and password
plus the household name and base currency; it creates the household, signs you
in, and lands on the Overview.

There is no *public* sign-up: once the first household exists, that screen is
closed unless `FAMILY_CFO_ALLOW_MULTIPLE_HOUSEHOLDS=true` is set (multi-tenant is
off by default). For a scripted / headless first run you can call the same
endpoint the screen uses:

```bash
curl -sk -X POST https://localhost:8443/api/v1/households \
  -H 'content-type: application/json' \
  -d '{
    "display_name": "Our Household",
    "base_currency": "USD",
    "owner_email": "you@example.com",
    "owner_password": "choose-a-strong-password",
    "owner_display_name": "Your Name"
  }'
```

Once signed in: add adult/viewer/child members from the **Users** page (pair
phones from **Devices**); enter your financial data from **Accounts**,
**Transactions**, **Bills**, **Loans**, and **Income & Tax** (or import a CSV
from **Imports**).

## 4. AI runtime and optional services

The local vLLM AI runtime is **on by default** (M17) — `docker compose up -d`
already started it, and every household uses it automatically. It needs a
GPU-capable host with the NVIDIA Container Toolkit. To run **without** AI (no
GPU), set `FAMILY_CFO_AI_ENABLED=false` in `.env` and start with:

```bash
docker compose up -d --scale vllm=0      # no AI; deterministic answers only
```

The vector store stays off (no consumer yet — scaffolding):

```bash
docker compose --profile vector up -d    # Qdrant
```

For choosing/swapping the model and confirming the agentic advisor engaged, see
the [AI Advisor guide](./ai-advisor.md).

## 5. Updates

The fast path — patch only the app containers, leaving the AI model and database
untouched:

```bash
git pull
scripts/patch.sh                 # rebuild api + worker + web
scripts/patch.sh web             # or just one service
scripts/patch.sh ios             # build + install the iPhone app over WiFi
scripts/patch.sh api web ios     # ship both halves together
TARGET=remote SSH_HOST=box scripts/patch.sh   # patch a remote host over SSH
```

`patch.sh` never rebuilds `vllm` or `db` and never removes a volume, so the
multi-GB model in `model_cache` is **not** re-downloaded. The full
`docker compose up -d --build` still works if you want to rebuild everything.

## SSH setup (once per machine)

Deploys to the box authenticate with an SSH **key**. No password is ever stored,
prompted for by our scripts, or committed — see
[the credential rule](../specs/06-security-model.md#credential-handling-humans-and-ai-agents-alike).

### The scripted way

```sh
scripts/setup-ssh.sh           # key → authorise on the box → ~/.ssh/config → .deploy.env
scripts/setup-ssh.sh --check   # report what is and isn't set up
```

It asks for a **hostname** and your **login name on the box**. Neither is a
secret. The one moment a password is typed is `ssh-copy-id`'s own prompt, which
reads from your terminal straight into `ssh` — the script never sees it and
never writes it anywhere.

**It refuses to run `ssh-copy-id` without a real terminal** (a CI job, an AI
agent's tool call) and prints the command for you to run instead. A password
prompt piped through another program is precisely the disclosure the rule exists
to prevent.

### The manual way

Four steps. Do these in your own terminal; the scripted way just automates them.

**1. Make a key, if you don't have one.** The passphrase is yours, protects the
key on this machine, and is never sent anywhere. Empty is allowed; a passphrase
plus `ssh-agent` is better.

```sh
ls ~/.ssh/id_ed25519 2>/dev/null || ssh-keygen -t ed25519 -C "family-cfo $(hostname -s)"
```

**2. Authorise the key on the box.** This is the *only* time you type your box
password, and it goes directly to `ssh`:

```sh
ssh-copy-id -i ~/.ssh/id_ed25519.pub YOUR-LOGIN@192.168.1.10
```

If it fails, check on the box that `~/.ssh` is `700` and `~/.ssh/authorized_keys`
is `600` — sshd silently ignores them otherwise.

**3. Add an alias to `~/.ssh/config`** so nothing else ever needs your username
or key path:

```
Host family-cfo-box
    HostName 192.168.1.10
    User YOUR-LOGIN
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

```sh
chmod 700 ~/.ssh && chmod 600 ~/.ssh/config
```

**4. Point the deploy config at the alias.** Create `.deploy.env` (gitignored):

```sh
SSH_HOST=family-cfo-box
REMOTE_DIR=~/family-cfo
```

**Verify** — this must succeed with no password:

```sh
ssh family-cfo-box true && echo "key auth works"
scripts/patch.sh api web
```

### Why the scripts don't ask for a username or key

`SSH_USER`, `SSH_PORT` and `SSH_KEY` are left unset on purpose, so `ssh` resolves
them from `~/.ssh/config`. An earlier version prompted for them and built
`user@host` itself — which overrode the very mechanism that made a password
unnecessary. They remain as escape hatches for a host you have no config entry
for.

## Remembering the destination (`.deploy.env`)

Copy `.deploy.env.example` to `.deploy.env` (gitignored) and set the box once:

```sh
SSH_HOST=192.168.1.10
SSH_USER=you
REMOTE_DIR=~/family-cfo
```

Both `deploy.sh` and `patch.sh` load it, so `scripts/patch.sh web` goes to the
right place without retyping anything. A real environment variable still wins
(`SSH_HOST=other-box scripts/patch.sh web`), so the file is a memory, not a cage.

**There is deliberately no `TARGET` in it.** Local-vs-remote is derived from
*where you are*: if `SSH_HOST` is this machine you're on the box, so the stack is
patched locally; if it isn't, you're on the MacBook, so it goes over SSH. The
same file is correct on both machines, and a stale `TARGET` can't send a patch
somewhere you didn't mean.

## What have I deployed, and is it still running?

Every successful deploy/patch records where it went (`.deploy.history`).

```sh
scripts/deployments.sh            # list everything, then offer to act on one
scripts/deployments.sh list       # just list — never prompts
scripts/deployments.sh stop 1     # containers down, data kept, restartable
scripts/deployments.sh remove 1   # containers removed, VOLUMES KEPT (db + model survive)
scripts/deployments.sh destroy 1  # containers AND volumes — DELETES THE DATABASE
scripts/deployments.sh uninstall 2  # iOS entries: remove the app from the phone
scripts/deployments.sh forget 1   # drop from the list only; touches nothing
```

The listing probes each place for live containers, and an unreachable box says
`unreachable` rather than `0 running` — "I can't tell" and "nothing is running"
are different answers, and only one of them is safe to act on.

`destroy` makes you type the host name, because it deletes the PostgreSQL volume
— every account, transaction and conversation in that household — and the model
cache, which is a multi-GB re-download. Nothing is backed up for you.

## Choosing what gets patched

The server and the phone are chosen by opposite mechanisms, on purpose:

| | Server | iPhone |
|---|---|---|
| How it's chosen | **Declared** — `TARGET=local`, or `SSH_HOST` names a box | **Discovered** — `devicectl` enumerates paired devices |
| Several available | `SSH_HOST="box1 box2"` patches each in turn, stopping at the first failure | **Refuses** and prints each UDID; name one with `IOS_DEVICE` |
| Wrong-target risk | Setting `SSH_HOST` implies `TARGET=remote`, so forgetting `TARGET` can't silently rebuild containers on your laptop | Unreachable/asleep devices are never candidates |

A server is never guessed at because it's never enumerated; a phone is never
guessed at because installing a debug build onto the wrong family member's phone
is exactly the kind of helpful default that ruins an afternoon.

## Patching the iPhone app

`ios` is a patch target like any other, but it is not in the default set — you
ship the phone when you mean to:

```bash
scripts/patch.sh ios                     # the one connected phone
scripts/deploy-ios.sh --list             # which devices are paired?
IOS_TEST=1 scripts/patch.sh ios          # run the unit tests first
IOS_DEVICE="Alex's iPhone" scripts/patch.sh ios
```

Two things worth knowing:

- **It runs on the Mac, not the box.** Xcode only exists on macOS, so if the
  stack is remote, patch the containers against the box and run the `ios` half
  from your Mac. The script says so rather than failing obscurely.
- **The phone is always deployed last.** When an iOS change needs an API or web
  change, `scripts/patch.sh api web ios` ships the server first, so the phone
  never comes up against a box that lacks the endpoint it was built to call.

The device must have been paired with Xcode for network debugging once, over a
cable (Xcode → Window → Devices and Simulators → tick **Connect via network**).
After that it deploys over WiFi indefinitely; no script can do that first
pairing for you.

The API applies any new migrations on startup (so a schema change ships with an
`api` patch). Migrations are additive; a rollback path is
`docker compose run --rm api python -m alembic -c alembic.ini downgrade <rev>`.

## Operating the stack

- Health: `scripts/doctor.sh` — a read-only report on containers, the API/DB/
  web/vLLM endpoints, disk, and GPU. Run it any time to answer "is it working?".
- Smoke test a build: `scripts/e2e-deploy-test.sh` — builds images and boots an
  isolated core stack (no vLLM), logs in, exercises chat, and tears down.
- Logs: `docker compose logs -f api` (or `worker`, `web`, `db`).
- Stop: `docker compose down` (keeps data) / `docker compose down -v` (**deletes
  all data volumes** — only for a full reset).
- Data lives in named volumes: `postgres_data`, `import_staging`, `backups`.
  Back these up at the volume level in addition to the app's own encrypted
  backups (see [Backup and Restore](./backup-and-restore.md)).

See [Troubleshooting](./troubleshooting.md) if the stack doesn't come up.

## Installing the iPhone app over the VPN (over-the-air)

`scripts/patch.sh ios` pushes a build from the Mac to the phone — but only when
both are on the same local network. Xcode discovers the device with Bonjour/mDNS,
which is **multicast**, and multicast does not cross a routed WireGuard tunnel. So
away from home the phone shows as `unavailable` and cannot be deployed to, even
though it reaches the box perfectly well over the VPN.

### What remote (over-the-VPN) iOS patching requires

All of these must hold. If one is missing the install silently does nothing —
which is why the script verifies what it can and this list exists.

| # | Requirement | How to check / get it |
|---|---|---|
| 1 | **The Mac can reach the box over SSH** — it builds the app and publishes it | `scripts/setup-ssh.sh --check` |
| 2 | **The phone can reach the box over HTTPS** — WiFi, WireGuard, or any tunnel that routes to it | Open `https://<box>:8443` in Safari on the phone |
| 3 | **The phone's UDID is in the provisioning profile** | True automatically if Xcode has ever deployed to it over a cable. A new phone must be plugged into the Mac once |
| 4 | **An Apple signing identity on the Mac** (`Apple Development` is enough — an Apple *Distribution* certificate is **not** needed) | `security find-identity -v -p codesigning` |
| 5 | **Developer Mode is ON on the phone** — required for development-signed apps | Settings → Privacy & Security → Developer Mode |
| 6 | **The phone trusts the box's TLS certificate** — iOS refuses an OTA manifest over an untrusted HTTPS cert. One-time | See the trust steps below; the script publishes the certificate for you |
| 7 | **The box's web tier serves `/ota/`** | Shipped in `docker/web-nginx.conf` + the `ios_ota` volume; `scripts/patch.sh web` if it's an older box |

**Account tier caveat.** The app is signed with a *development* identity, so how
long it keeps working depends on the Apple account behind it: a **paid** Developer
Program membership gives a 1-year provisioning profile, while a **free** personal
team gives 7 days — after which the app refuses to launch until you republish and
reinstall. Nothing else changes: the flow below is identical either way. Check
what you have with:

```sh
security cms -D -i ~/Library/Developer/Xcode/UserData/Provisioning\ Profiles/*.mobileprovision \
  | plutil -extract ExpirationDate raw -   # ~1 year = paid, ~7 days = free
```

Note the box does **not** need to be exposed to the internet, and no Apple service
is involved in the install — the phone downloads the app from your own hardware.

### Using it

The fix is to stop pushing and let the phone **pull**:

```sh
scripts/deploy-ios-ota.sh            # archive, sign, publish to the box, print the link
scripts/deploy-ios-ota.sh --url-only # reprint the link
```

It archives a Release build, exports a signed `.ipa` (method `debugging` — the
development certificate and the team profile, which already lists the phone's
UDID; ad-hoc would need an Apple *Distribution* certificate and buys nothing
here), and publishes the `.ipa`, an OTA manifest and a small install page into the
box's nginx at `/ota/`. Then open `https://<box>:8443/ota/` in Safari **on the
phone** — over WiFi or WireGuard — and tap Install.

**One-time:** iOS refuses an OTA install unless the manifest is served over HTTPS
with a *trusted* certificate, and the box's certificate is self-signed. The script
publishes it at `/ota/box-cert.crt`; install it on the phone and enable it under
Settings → General → About → Certificate Trust Settings.

Two nginx details that will silently break the install if you touch that config:
an nginx `types` block **replaces** the defaults rather than adding to them (so
`text/html` must be re-declared, or Safari downloads the install page instead of
rendering it), and `/ota/` must not fall through to the dashboard's SPA
`index.html` — a missing build has to 404.

| | Same WiFi | Over WireGuard |
|---|---|---|
| `scripts/patch.sh ios` (push) | ✅ | ❌ Bonjour can't cross the tunnel |
| `scripts/deploy-ios-ota.sh` (pull) | ✅ | ✅ |
