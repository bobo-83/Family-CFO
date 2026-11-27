# AI runtime #170 — two-Spark deployment validation (pending)

Issue #170's regression case is the checkpoint that started it:
`nvidia/Qwen3.8-Flash-Next-NVFP4` — rejected by the installed vLLM 0.24.0
(`qwen4_exp` architecture), later verified serving on the two DGX Sparks with
vLLM 0.32.0, tensor parallelism 2, `gpu_memory_utilization` 0.72, and an
8,192-token context ([checkpoint requirements](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4#usage)).
Those settings are specific to that hardware/runtime combination and must be
recorded as a reviewed deployment profile, never generic defaults
([ADR 0081](../adr/0081-ai-runtime-apply-transactions.md); plan
`docs/plans/ai-runtime-apply-validation-recovery-2026-11-20.md`).

This checklist records the regression evidence the plan's two-Spark
deployment-specific regression gate requires. **Every item is pending**: the
run happens on the real hardware, not in CI. No secrets, hostnames, or
credential material belong in this file — record identities (image IDs,
revisions, versions) only.

## Evidence checklist

- [ ] **Installed runtime/image identities on both Sparks** — installed vLLM
  versions and the exact immutable image IDs serving on each node at the time
  of the run.
- [ ] **Resolved immutable checkpoint revision** — the pinned repository
  revision the apply flow resolved for `nvidia/Qwen3.8-Flash-Next-NVFP4` (not
  a moving tag).
- [ ] **TP2/Ray placement + per-node admission estimates** — the frozen plan's
  selected topology, rank placement across the declared nodes, and each
  node's verified/estimated memory/disk requirements with their evidence
  classifications.
- [ ] **Explicit reviewed context/GPU settings** — the profile's context
  length (8,192), `gpu_memory_utilization` (0.72), and any parser/scheduler
  settings, recorded as reviewed profile values specific to this
  hardware/runtime pair.
- [ ] **Parsed synthetic tool call** — a bounded synthetic probe returned a
  parsed function name with schema-valid arguments (no household tool
  executed), within the ≤128-token probe bound.
- [ ] **Synthetic photo request through the intended application route** —
  one embedded synthetic PNG through the exact intended photo route produced
  a nonempty structurally valid answer within the ≤64-token bound (transport
  smoke only — not a visual-accuracy claim).
- [ ] **Household settings unchanged until commit, updated exactly once
  afterward** — journal/receipt evidence shows no settings write before the
  durable commit decision and exactly one receipt for the job.
- [ ] **Injected startup failure + verified restoration** — a deliberately
  induced startup failure (the issue's original failure mode class) resulted
  in automatic restoration of the previously working runtime and its
  verification (`restored`, settings still previous, original cause
  retained).
- [ ] **Manager restart drill** — restart the manager around an in-flight/
  pending job: executor survives or resumes; no second executor; status reads
  the durable journal.
- [ ] **API restart drill on both sides of the commit boundary** — before
  commit: reconciliation resumes and completes the decision; after commit:
  acknowledgement replay is safe and no rollback occurs.
- [ ] **Mixed-version fail-safe** (if exercised during rollout) — a v1/v2
  skew pair performs no mutation and reports `manager_upgrade_required` /
  upgrade-required, per ADR 0081.

## Outcome

Pending. On completion, record the observed values per item with dates and
the deployed manager protocol/build (`GET /v2/capabilities`), and mark the
gate in the M23 #170 amendment satisfied.
