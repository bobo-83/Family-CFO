# ADR 0071: Two-Spark cluster — declared peer, automatic detection, opt-in use

## Status

Accepted. (Serving path pending hardware bring-up: the boxes are not yet
cabled; everything up to the toggle is live and verified.)

## Context

The household has a second dedicated GB10 Spark. Two Sparks over their
ConnectX QSFP link (200GbE) are an NVIDIA-supported vLLM tensor-parallel
pair: ~2× the model-memory pool, unlocking 100B–235B-class models the single
box cannot hold. The user asked for automatic detection plus a user-facing
toggle to enable the bigger models.

## Decisions

1. **Enrolled once, detected automatically, used by choice.** The peer is
   DECLARED (`scripts/setup-cluster.sh` writes `CLUSTER_PEER_HOST` — same
   philosophy as deploys: the server is declared, never discovered by
   scanning). After enrollment, detection is automatic: the hardware profile
   TCP-probes the peer's node-exporter on every read and reports
   `cluster_peer_reachable` + `cluster_memory_gb` (2× one node — the boxes
   are symmetric). USING the pool is the household's explicit toggle
   (`ai_runtime_configs.cluster_enabled`), which a model swap must never flip.
2. **The catalog gates by `min_nodes`.** Cluster-tier entries (235B MoE
   NVFP4, GLM-4.5-Air FP8, Llama 3.3 70B FP8) carry `min_nodes: 2`; pickers
   offer them only while toggle-on AND peer-reachable, with fit computed
   against the combined budget. Single-node behavior is untouched otherwise.
3. **Serving is vLLM tensor-parallel over Ray.** The peer runs a lean worker
   compose (same vllm image, `ray start --address=<head>`, host networking,
   node-exporter for the probe). The head gains a compose overlay: host
   networking + NCCL/GLOO ifname pinned to the QSFP interface; cluster models
   swap in via the existing `VLLM_EXTRA_ARGS` seam
   (`--tensor-parallel-size=2 --distributed-executor-backend=ray`).
4. **Degrade to one box, loudly.** Peer unreachable → toggle disabled in the
   UI (saved value kept), cluster tier hidden, single-node serving unaffected.
   `doctor.sh` gains an advisory Cluster section.

## Rejected

- **Zero-config LAN discovery** — scanning contradicts the declared-server
  rule and invites pairing with the wrong machine.
- **Pipeline-parallel over ordinary Ethernet** — bigger models at painful
  latency; the QSFP link is the supported topology, so we require it.
- **Auto-enabling when the peer appears** — swapping to a 235B model changes
  answer latency and disk usage materially; that is a human's call (same
  confirm-first instinct as ADR 0013's one-click apply).
- **Storing the toggle client-side** — web and iOS must agree (ADR 0025).

## Invariant

> A cluster-tier model can only be selected while the enrolled peer is
> reachable and the household turned the toggle on; losing the peer never
> breaks single-node serving.
