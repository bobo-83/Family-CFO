# Guides

Operator and developer guides for running and working on Family CFO. For the
design specs and decisions, see the [Spec Kit](../specs/README.md) and
[ADRs](../adr/).

- [Home-Server Deployment](./deployment.md) — clean checkout → running stack,
  first-run household setup, updates, and operations.
- [Local Development](./local-development.md) — running and building the backend,
  frontend, and service packages outside Docker; migrations and the OpenAPI
  contract.
- [AI Advisor](./ai-advisor.md) — deploying the local vLLM runtime and testing
  the agentic tool-calling advisor end-to-end (model choice, tool-calling flags,
  opt-in config, and how to confirm the model actually engaged).
- [Backup and Restore](./backup-and-restore.md) — the encryption key, taking
  backups, restoring, and the version-match requirement.
- [Tax Parameter Updates](./tax-parameter-updates.md) — refreshing the
  brackets, standard deductions, and filing-status constants the tax estimator
  uses when a new tax year lands.
- [Security Hardening](./security.md) — TLS, sessions, roles, auditing, data at
  rest, secrets, and the operator responsibilities checklist.
- [Troubleshooting](./troubleshooting.md) — common startup and operating
  problems and how to diagnose them.

Feature-specific and component detail lives in the component READMEs
(`apps/api`, `apps/web`, `docker`, `database`, `services/*`), which these guides
link to rather than duplicate.
