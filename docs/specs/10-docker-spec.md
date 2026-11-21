# Docker Spec

## Goal

Family CFO should run on a home server with:

```bash
docker compose up -d
```

## Planned Containers

### family-cfo-web

Angular dashboard.

### family-cfo-api

FastAPI backend.

### family-cfo-db

PostgreSQL.

### family-cfo-vector

Qdrant.

### family-cfo-vllm

Local LLM runtime. **On by default** (M17): no longer behind a Compose profile,
so `docker compose up -d` starts it and the api/worker are wired to use it
(`FAMILY_CFO_AI_*`). This assumes a GPU-capable host with the NVIDIA Container
Toolkit; the command enables tool-calling (`--enable-auto-tool-choice
--tool-call-parser`) required by the M16 agentic advisor. GPU-less hosts opt out
with `FAMILY_CFO_AI_ENABLED=false` and `docker compose up -d --scale vllm=0`.
Only the on-box local model is defaulted on; external/cloud runtimes remain
opt-in (ADR 0008). `scripts/deploy.sh` stands the whole stack up on a local or
remote host with one command — see the [AI Advisor guide](../guides/ai-advisor.md).

### family-cfo-worker

OCR, imports, scheduled reports, and background tasks.

## Future Containers

- Reverse proxy
- Monitoring
- Backup

## Volumes

Persistent data:

- PostgreSQL data
- Qdrant data
- Model cache
- Encrypted backups
- Import staging

## Network Rules

- Internal services communicate on a private Docker network.
- Only intended UI/API ports are exposed.
- vLLM should not be exposed publicly by default.

## M124 Deployment Scope (ADR 0076)

Qualified incomplete monetary aggregates require no Docker image, service,
volume, network, environment variable, system-tool, runtime topology, or cloud
dependency change. API, worker, web, local AI, PostgreSQL, and Qdrant retain their
existing ownership and deployment boundaries. No Compose file or Dockerfile is
part of M124.

The API and generated clients nevertheless form one intentional breaking
contract release. They are deployed and rolled back together under the existing
version/compatibility process; mixed old/new artifacts are unsupported. No SQL
migration or database rollback step is required.

## Backup Retention Deployment Scope (issue #116, ADR 0077)

Active cadence, destination, tier policies, logical caps, reserves, review gate,
and destination generations are database-owned by the `backup_settings`
singleton. API and worker build the same immutable execution configuration from
that row plus process-owned database/staging/local-path/encryption-key settings;
neither container may select an arbitrary household's backup fields.

The API and worker retain identical access to existing backup volumes and
bootstrap inputs. No new cloud service, backup sidecar, network exposure, or
sensitive-data dependency is added. PostgreSQL remains the production
cross-process lock provider through a session advisory lock on a dedicated
connection; SQLite uses only the explicit local test seam. The worker runs
cadence-independent backup maintenance on its existing interval even when
frequency is off or a backup is not due.

For one compatibility release only,
`FAMILY_CFO_BACKUP_RETENTION_COUNT`,
`FAMILY_CFO_OFFBOX_BACKUP_RETENTION_DAYS`, existing household backup columns,
Compose wiring, and `.env.example` entries remain deprecated bootstrap inputs.
They are consumed only when transactionally materializing a missing singleton;
a structured warning records that bootstrap occurred. Once the singleton exists,
database values are authoritative and later environment changes do nothing.
Removal is a separately planned cleanup after the rollback window.

Add positive process setting `FAMILY_CFO_BACKUP_IO_TIMEOUT_SECONDS` (default
3600) for bounded dump/restore and supported SMB I/O. It is runtime ownership,
not an editable retention field. Local free space uses the encrypted-backup
volume's `statvfs`; this does not preflight a separate system temporary volume
used by dump adapters. SMB uses public caller-available quota/capacity through
`smbclient.stat_volume()` and same-share atomic promotion through
`smbclient.rename()`.

Upgrade creates schema first and bootstraps settings from database evidence plus
legacy inputs on first repository read; Alembic never persists whichever process
environment happened to run migration. Every upgraded/restore installation
pauses automatic pruning until explicit administrator activation. Rollback copies
only representable fields and requires explicit legacy retention environment
values because tier policies are not representable; no rollback recreates a
deleted archive.

At the pinned contract baseline, API and worker move first to the later `0.160`
contract/runtime, then dependent web and iOS clients. Contract number is
revalidated if main advances. Deployment and rollback never expose a dependent
client before every served API supports its required fields. Operator docs must
state that recovery status is a bounded inventory/read probe and capacity is an
observation, not a guarantee.

Verification adds PostgreSQL 17 migration/advisory-lock integration in backend
CI with a workflow-owned synthetic test database and
`FAMILY_CFO_REQUIRE_POSTGRESQL=1`; contributor machines may loudly skip only
those explicitly marked cases when PostgreSQL is absent. Deterministic local/SMB
fakes select all other fallback/error paths explicitly; tests never infer tool
absence or ask for NAS/DB credentials.

## AI Runtime Apply Deployment Scope (issue #170, ADR 0081)

The model-manager remains the sole Docker-socket holder, internal-network only,
never recreated by a swap it performs. The swap transaction adds these
deployment-owned facts:

- **Durable journal mount.** `PROJECT_DIR/.runtime-swap/v2/` (requests, job
  records, snapshots, `current.json`) must persist on the bind-mounted project
  directory with mode 0700/0600, excluded from Git and diagnostic exports and
  added to `.gitignore` as `.runtime-swap/`. A failed required journal write
  prevents the next deployment mutation.
- **Immutable runtime artifacts.** Launch uses frozen immutable image IDs and
  pinned model revisions — no implicit pulls, no runtime upgrades during a
  swap; rollback artifacts and caches are never pruned by the apply flow.
  Staging may update cache contents but not active runtime configuration.
- **Sidecar protocol/version.** The manager speaks protocol major 2 and
  reports its build identity from `services/model-manager/BUILD`
  (`GET /v2/capabilities`); manager code stays baked into its image, and the
  deployment/rollback rules of ADR 0013's matched-pair rollout carry over
  with the ordered migration of ADR 0081 (disable Apply → drain → migration
  `0099` → manager v2 → API/reconciler → generated-client clients → enable).
  During any skew, mutation fails closed.
- **Peer checks.** Cluster topology validation runs the bounded SSH command
  allowlist over the enrolled peer (host-key-verified, read-only probes
  only); node-exporter reachability alone is insufficient, and TP2 is
  verified as two expected GPU ranks on the declared nodes.
- **Restart and ordering behavior.** Manager shutdown may leave the supervised
  executor alive (explicitly supported); startup recovery reads the journal
  and resumes unfinished jobs before admitting new ones and never signals a
  recovered PID. `api`/`worker` are recreated only during post-commit
  finalization, using their currently deployed application images (never
  pulled or upgraded); a failed candidate never touches application routing.
- **Transport.** The reverse-proxy read timeout must be verified ≥ 210 s in
  the deployment spec so the 180 s synchronous preflight cannot be severed by
  the proxy.
- **No new exposure.** No public manager port, no new container class, no
  cloud dependency; checker containers are ephemeral, capped (2 CPUs /
  4 GiB), read-only-root, and cleaned by exact recorded container IDs.

## Acceptance Criteria

- Compose file supports local development and home-server deployment profiles.
- Secrets are provided through environment files or Docker secrets.
- Volumes are documented.
- Backup and restore strategy is documented before release.
