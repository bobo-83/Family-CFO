# Model Manager

The one privileged sidecar in the stack (ADR 0013): mounts the Docker socket +
project directory and exposes a single validated operation — swap the served
AI models by running `scripts/swap-model.sh`. Strict `org/name` repo-id
validation; one swap at a time; internal Docker network only, never published.
The API (owner role) is the only caller. Remove it (`--scale model-manager=0`)
to fall back to the command-line swap flow (ADR 0012).

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

## Swap ownership and operational limits (#167)

Dashboard and standalone CLI swaps take a non-blocking exclusive `flock` on
an open deployment-directory descriptor. The host checkout and the manager's
`/project` bind mount **must identify the same directory**, not separate copies.
The production guarantee requires a native Linux host/container shared kernel
and bind-mounted directory identity. **Development caveat:** macOS Docker
Desktop's virtiofs does not propagate flock across the host/VM boundary. Do not
run its container manager and the macOS host CLI concurrently expecting
exclusion; use a single execution environment for swaps there.
Keep that directory in place throughout swaps and upgrades; `.env` may be
rewritten without changing lock identity. Contention returns dashboard 409 or
CLI failure with a retry message. Do not bypass a held lock.

The manager passes its descriptor to the real script; CLI operation also works
with the manager disabled. Ownership lasts through `.env` writes and Compose
recreation, not asynchronous model downloading/loading. Script success is not
proof the model is serving. Status is process-local: a restart resets it to
`idle`, without durable job recovery. A surviving orphan retains the inherited
lock until its last descriptor closes, so `idle` can still mean requests get
409. Container termination may kill the script instead; survival/completion is
not guaranteed.

This is **not a general deployment mutex**. In particular,
`scripts/setup-cluster.sh` is an excluded independent `.env` writer: never run
it concurrently with a swap. An accepted swap that later fails does not roll
back household configuration automatically. Check desired-versus-serving status
and the mismatch banner, resolve the failure, then Apply again; do not assume
the old model is still serving.

## Matched rollout and rollback

`swap-model.sh` and `scripts/lib/swap_lock.py` are bind-mounted project code;
manager Python code is baked into `services/model-manager/Dockerfile`'s image.
Ship the scripts and image from the **same revision**:

1. Quiesce dashboard and CLI swaps and let active work finish. Check actual
   processes/ownership as well as status; `idle` alone is insufficient.
2. Stop the manager. Keep the deployment directory itself in place and install
   the matching project scripts (including the lock helper).
3. Rebuild and recreate the manager image from that revision using the normal
   deployment Compose configuration, mounting that same host directory at
   `/project`. Verify the deployed script/helper revision and image build
   provenance before re-enabling swaps; do not infer identity from status.
4. Re-enable swaps and check desired/serving status after Apply. Do not perform
   an unsolicited model download merely to verify rollout.

Mixed versions are unsupported for mutual exclusion: a legacy manager cannot
pass ownership to the new script; the new manager with the legacy script can
still self-reject its `/status` job; a legacy CLI ignores the directory lock.
A script-only update is not a completed fix. For rollback, quiesce again, wait
for active work, stop the manager, and restore **both** scripts and the matching
manager image before resuming. Never replace the deployment directory or force
past ownership during upgrade/rollback.

## Integration verification

From the repository root with the manager dev dependencies installed:

```bash
# Offline, synthetic fixtures; no Docker, network, or model downloads:
apps/api/.venv/bin/python -m pytest scripts/tests/test_swap_model.py -k 'not docker_bind_mount'
# Real Docker bind-mount gate (preload the small Python runtime yourself):
docker pull python:3.12-slim
FAMILY_CFO_REQUIRE_DOCKER=1 apps/api/.venv/bin/python -m pytest scripts/tests/test_swap_model.py
```

The portable tests run the real script and a separate manager server with
synthetic `.env` data and PATH stubs for Docker, offline model inspection,
128-GB memory reporting, and GNU-style `sed -i` (so BSD-sed contributors can run
the suite too). The suite also pins the manager's node-memory evidence with
`FAMILY_CFO_NODE_MEMORY_GB` (GiB; operator-declared, so classified as estimated,
not verified — it overrides the `/proc/meminfo`/`sysctl` probe) so admission
does not depend on the runner's RAM; production keeps the measured probe. They do not claim native macOS production-script compatibility.
The Docker test uses `--pull=never`, no network, and no Docker socket mount;
it checks both contention orders and release across a real bind mount. Missing
Docker skips only when `FAMILY_CFO_REQUIRE_DOCKER` is unset; installed-but-broken
Docker or a missing runtime image fails. The explicit macOS Docker Desktop
platform skip records the host/VM flock limitation above, not verified
exclusion. It never skips the Linux contention path. `services.yml` installs
Docker and preloads the image on Linux, where REQUIRE is set; portable tests also run on
macOS. Python, Bash, and standard POSIX `ps` are used by the portable suite.
