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

Building the iPhone app (`patch.sh ios`, or `scripts/deploy-ios*.sh`) needs your
Apple Developer team — the project ships with an empty `DEVELOPMENT_TEAM`, so set
`IOS_TEAM_ID` (in `.deploy.env`) and the scripts inject it at build time (ADR
0030; simulator test runs need none).

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

## Two-Spark cluster (ADR 0071)

One DGX Spark (GB10) tops out around 120 GB of usable unified memory — enough
for the 70B-class models, not for the 235B-class ones. A second Spark connected
back-to-back over the ConnectX QSFP port (200 GbE, no switch needed) lets vLLM
serve one model **tensor-parallel across both boxes**: each node holds half the
weights, and every token's activations cross the QSFP link.

```
   head box (Spark 1)                       worker box (Spark 2)
   ┌──────────────────────────┐             ┌──────────────────────────┐
   │ full stack               │  QSFP link  │ docker-compose.worker.yml │
   │ (docker-compose.yml      │ 10.0.0.1 ←→ │ ONLY:                     │
   │  + docker-compose        │   10.0.0.2  │   ray-worker  (joins head)│
   │    .cluster.yml overlay) │  200 GbE    │   node-exporter  :9100    │
   │ vllm = Ray head, TP=2    │             │                           │
   └──────────────────────────┘             └──────────────────────────┘
```

The head runs the whole application; the worker contributes exactly two things:
a Ray worker the head's vLLM schedules shards onto, and a node-exporter on
`:9100` that answers "is the second node alive?".

### Requirements — all of them

- **The ConnectX QSFP link, cabled and configured.** Give the QSFP interface a
  static address on each node (e.g. `10.0.0.1` head, `10.0.0.2` worker). The
  LAN is *not* enough: tensor-parallel serving pushes activations across nodes
  on every token, and only the 200GbE link makes that usable.
- **The same `vllm/vllm-openai` image on both nodes.** The worker compose file
  uses the identical image reference so Ray/vLLM/NCCL versions match. A skew
  fails only at model load, with an opaque collective-ops error — after
  pulling a new image on one box, pull it on the other.
- **Key-based SSH from the head to the worker** (`scripts/setup-cluster.sh`
  checks; no password is ever handled — same rule as `scripts/setup-ssh.sh`).

### Enrollment (once, on the head box)

```sh
scripts/setup-cluster.sh spark2 --link-ip 10.0.0.2 --head-ip 10.0.0.1 --ifname enp1s0f0
```

This ships `docker-compose.worker.yml` plus a minimal `.env` to
`~/family-cfo-worker/` on the peer, starts the worker stack there, and records
the cluster in the head's `.env` (`CLUSTER_PEER_HOST`, `CLUSTER_PEER_PORT`,
`CLUSTER_NCCL_IFNAME`, `CLUSTER_HEAD_ADDR`). It runs on the head box because
that `.env` is what it edits. It is honest about limits: SSH, Docker, the
worker containers and node-exporter are verified; NCCL over the QSFP link is
not verifiable until the cable is up and a model actually loads.

Then restart the head stack with the cluster overlay and pick a cluster model:

```sh
COMPOSE_FILES="-f docker-compose.yml -f docker-compose.cluster.yml" scripts/patch.sh api
COMPOSE_FILES="-f docker-compose.yml -f docker-compose.cluster.yml" \
  scripts/swap-model.sh unsloth/Qwen3-235B-A22B-Instruct-2507-NVFP4
```

The overlay moves `vllm` onto the **host network** (Ray and NCCL negotiate
dynamic ports between nodes; a bridge NAT breaks them), points api/worker at
`http://host.docker.internal:8000`, and passes the peer's declared address
through to the API.

### How detection and the toggle behave

Detection is **declared, never discovered**: nothing scans the network. The
API's hardware profile reads `FAMILY_CFO_CLUSTER_PEER_HOST` / `_PORT` (set by
the overlay from `.env`) and probes the peer's node-exporter. When the peer
answers, catalog models with `min_nodes: 2` become offerable; when it doesn't
(cable out, worker stopped), they are withheld — the single-node catalog is
unaffected either way. `scripts/doctor.sh` mirrors the same probe in its
advisory **Cluster** section (silent unless `CLUSTER_PEER_HOST` is set), and
`scripts/swap-model.sh` refuses a cluster model outright until
`scripts/setup-cluster.sh` has run.

The cluster-tier models and their tool parsers:

| Model | Tool parser |
|---|---|
| `unsloth/Qwen3-235B-A22B-Instruct-2507-NVFP4` | `hermes` |
| `zai-org/GLM-4.5-Air-FP8` | `glm45` |
| `RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic` | `llama3_json` |

All three get `VLLM_EXTRA_ARGS=--tensor-parallel-size=2
--distributed-executor-backend=ray` (the one `.env` variable allowed to carry
spaces — see the `vllm` command comment in `docker-compose.yml`).

### Troubleshooting

- **`NCCL_SOCKET_IFNAME` must name the QSFP interface on BOTH nodes.** This is
  the classic failure: left to autodetect, NCCL/Gloo bind the LAN NIC — loads
  then time out or tokens crawl. `setup-cluster.sh --ifname` writes it to both
  `.env` files; if the interface name differs between the boxes, edit the
  worker's `~/family-cfo-worker/.env` by hand.
- **Model load hangs at "waiting for resources"** — the Ray worker never
  joined. On the worker: `docker compose -f docker-compose.worker.yml logs
  ray-worker`. Usual causes: `CLUSTER_HEAD_ADDR` wrong, the QSFP link down, or
  the head not yet restarted with the cluster overlay (its Ray head listens on
  `:6379` only on the host network).
- **Collective-ops errors at load** (`NCCL error`, mismatched protocol) —
  image version skew. `docker compose pull` on both boxes, restart both.
- **Peer shows unreachable in doctor/API but SSH works** — the probe uses the
  QSFP link IP on purpose (health rides the cable the tensors ride), so a
  working LAN with a dead link is still "not clustered".

> **Cluster overlay gotcha — saved runtime configs.** Households that saved an
> AI runtime config store the base URL (`http://vllm:8000`). Switching to the
> cluster overlay moves vLLM to host networking, so those rows must be
> repointed to `http://host.docker.internal:8000` (one UPDATE on
> `ai_runtime_configs`, or re-save from the AI runtime page) — otherwise chat
> reports the runtime unreachable while the deployment default works fine.

## Cutting a release

`/VERSION` is the source of truth (ADR 0029). The sequence:

1. Bump `/VERSION` in a `chore(release): X.Y.Z` PR and merge it.
2. Tag the merge commit and push the tag:

   ```
   git checkout main && git pull
   git tag "v$(cat VERSION)" && git push origin "v$(cat VERSION)"
   ```

3. `.github/workflows/release.yml` fires on the tag and creates the GitHub
   Release, with notes generated from the PRs merged since the previous tag.
   It **refuses** a tag that disagrees with `/VERSION` — that mismatch means
   the bump never merged or the tag went on the wrong commit, and it would
   otherwise produce release notes describing code the release does not
   contain (`/health` reports `/VERSION`, so the box would claim a version no
   release matches).
4. The same workflow builds and pushes the container images (see below), so by
   the time the Release appears the artifacts to deploy already exist.
5. Deploy the box from the published images:
   `IMAGE_TAG="$(cat VERSION)" SSH_HOST=<host> scripts/patch.sh api worker web`.
6. `scripts/release-testflight.sh` — uploads to TestFlight and refreshes the
   over-VPN OTA bundle on the box in the same run (`SKIP_OTA=1` to skip).

The Release carries notes and the source archives GitHub attaches
automatically. The signed `.ipa` deliberately stays on the box: publishing an
app binary to a public repo invites inspection of the certificate-pinning and
endpoint logic, and the OTA manifest embeds the box's own address.

### Container images

The tag also publishes the images the box runs, to GHCR:

| Image | Services | Built from |
| --- | --- | --- |
| `ghcr.io/bobo-83/family-cfo-api` | `api` **and** `worker` | `docker/api.Dockerfile` |
| `ghcr.io/bobo-83/family-cfo-web` | `web` | `docker/web.Dockerfile` |

Two images, three services: `api` and `worker` are the same image run with
different commands (`entrypoint-api.sh` vs `entrypoint-worker.sh`), so
publishing a third would push identical bytes under a name that could drift.

Each is pushed twice — once as the version (`0.148.0`) and once as the commit
SHA. The version is what you deploy and what `/health` reports; the SHA is the
immutable one, so it is the tag to cite when you need to prove exactly what ran.

Images are **arm64 only**, built natively on GitHub's `ubuntu-24.04-arm` runner
(free for public repos). This is not a preference: the box is arm64, and images
built on the default x86_64 runners would not run on it at all. Multi-arch via
QEMU was rejected — the Angular build under emulation is punishingly slow, and
no other architecture has to run.

### Deploying a known artifact

```
IMAGE_TAG=0.148.0 SSH_HOST=<host> scripts/patch.sh api worker web
```

With `IMAGE_TAG` set, `patch.sh` **pulls** those images and starts them with
`--no-build`. Without it, the script behaves exactly as it always has —
`up -d --build`, rebuilding from the synced working tree. That remains the
fallback and is the right thing for iterating on an unreleased change.

`--no-build` is deliberate: a tag that was never published fails loudly instead
of quietly rebuilding local source, which is the whole failure this mode exists
to prevent.

If the GHCR packages are private, the box needs `docker login ghcr.io` with a
`read:packages` token once. Making the packages public avoids that.

**What this does and does not pin.** Reproducibility here is narrower than it
sounds, and it is worth being precise:

* **Fixed** — the application image. Byte-for-byte the artifact built once on
  the tag, identifiable afterwards by version or SHA. This is the part that
  used to be unknowable: a source rebuild captured whatever happened to be in
  the working tree at that moment, which nothing recorded.
* **NOT fixed — the database.** Migrations still run on `api` startup and are
  applied forward. Pulling an older tag does **not** roll the schema back, so
  moving backwards across a migration is not a supported undo.
* **NOT fixed — `.env`.** It is never synced and never part of an image. The
  same image tag on two boxes with different `.env` files behaves differently.
* **NOT fixed — `docker-compose.yml` and the rest of the tree.** `patch.sh`
  still rsyncs the working tree, because compose has to be present on the box
  to know which images to pull. Ports, volumes and service wiring therefore
  still come from your checkout, not from the release. Deploy a tag from a
  clean checkout of that tag if you want those to match too.
* **NOT fixed — the pinned third-party images.** `vllm`, `postgres`, `searxng`
  and friends are `:latest` or a floating major in `docker-compose.yml`, and
  `patch.sh` never touches them anyway.
