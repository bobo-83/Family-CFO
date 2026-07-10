"""Curated model catalog and best-effort hardware profile (ADR 0012).

The catalog is planning data — bf16 serving estimates, maintained by hand —
so the dashboard (and later iOS) can offer a model picker with live
hardware-fit feedback. A selection REPLACES the served models; the UI computes
requirements from the selection alone, never "current + new".
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    label: str
    role: str  # "main" | "vision" | "both"
    parameters_b: float
    est_memory_gb: float  # bf16 weights + working set, before KV headroom
    est_disk_gb: float  # one-time weights download
    tool_parser: str | None
    supports_vision: bool
    gated: bool
    notes: str = ""


# Estimates assume bf16 serving via vLLM. Keep in sync with README's table.
MODEL_CATALOG: tuple[ModelInfo, ...] = (
    ModelInfo(
        id="Qwen/Qwen2.5-7B-Instruct",
        label="Qwen2.5 7B — fast, light",
        role="main",
        parameters_b=7,
        est_memory_gb=16,
        est_disk_gb=15,
        tool_parser="hermes",
        supports_vision=False,
        gated=False,
        notes="Quick answers on modest GPUs; weakest reasoning of the family.",
    ),
    ModelInfo(
        id="Qwen/Qwen2.5-14B-Instruct",
        label="Qwen2.5 14B — balanced",
        role="main",
        parameters_b=14,
        est_memory_gb=30,
        est_disk_gb=28,
        tool_parser="hermes",
        supports_vision=False,
        gated=False,
    ),
    ModelInfo(
        id="Qwen/Qwen2.5-32B-Instruct",
        label="Qwen2.5 32B — recommended",
        role="main",
        parameters_b=32,
        est_memory_gb=65,
        est_disk_gb=62,
        tool_parser="hermes",
        supports_vision=False,
        gated=False,
        notes="The default: strong reasoning and tool use.",
    ),
    ModelInfo(
        id="Qwen/Qwen2.5-32B-Instruct-AWQ",
        label="Qwen2.5 32B AWQ — fast 4-bit (recommended on unified memory)",
        role="main",
        parameters_b=32,
        est_memory_gb=22,
        est_disk_gb=19,
        tool_parser="hermes",
        supports_vision=False,
        gated=False,
        notes="Same model, 4-bit quantized: ~3-4x faster decode on bandwidth-limited hardware (GB10/DGX Spark class) with minimal quality loss.",
    ),
    ModelInfo(
        id="Qwen/Qwen2.5-14B-Instruct-AWQ",
        label="Qwen2.5 14B AWQ — very fast 4-bit",
        role="main",
        parameters_b=14,
        est_memory_gb=11,
        est_disk_gb=9,
        tool_parser="hermes",
        supports_vision=False,
        gated=False,
    ),
    ModelInfo(
        id="Qwen/Qwen2.5-72B-Instruct",
        label="Qwen2.5 72B — strongest",
        role="main",
        parameters_b=72,
        est_memory_gb=145,
        est_disk_gb=140,
        tool_parser="hermes",
        supports_vision=False,
        gated=False,
        notes="Needs very large memory; consider only on 128GB+ unified or multi-GPU.",
    ),
    ModelInfo(
        id="meta-llama/Llama-3.3-70B-Instruct",
        label="Llama 3.3 70B",
        role="main",
        parameters_b=70,
        est_memory_gb=140,
        est_disk_gb=132,
        tool_parser="llama3_json",
        supports_vision=False,
        gated=True,
        notes="Gated on Hugging Face — requires HUGGING_FACE_HUB_TOKEN.",
    ),
    ModelInfo(
        id="Qwen/Qwen2.5-VL-32B-Instruct",
        label="Qwen2.5-VL 32B — vision-capable main",
        role="both",
        parameters_b=33,
        est_memory_gb=70,
        est_disk_gb=66,
        tool_parser="hermes",
        supports_vision=True,
        gated=False,
        notes="Sees photos itself — no separate vision model needed.",
    ),
    ModelInfo(
        id="Qwen/Qwen2.5-VL-72B-Instruct",
        label="Qwen2.5-VL 72B — strongest vision-capable main",
        role="both",
        parameters_b=72,
        est_memory_gb=145,
        est_disk_gb=140,
        tool_parser="hermes",
        supports_vision=True,
        gated=False,
        notes="Sees photos itself; needs very large memory (multi-GPU or 160GB+ unified).",
    ),
    ModelInfo(
        id="Qwen/Qwen2.5-VL-72B-Instruct-AWQ",
        label="Qwen2.5-VL 72B AWQ — biggest vision that fits ~120GB boxes",
        role="both",
        parameters_b=72,
        est_memory_gb=45,
        est_disk_gb=41,
        tool_parser="hermes",
        supports_vision=True,
        gated=False,
        notes="4-bit quantized: 72B-class vision + reasoning in ~45 GB — the strongest vision option on GB10/DGX-Spark-class hardware.",
    ),
    ModelInfo(
        id="Qwen/Qwen2.5-VL-7B-Instruct",
        label="Qwen2.5-VL 7B — vision describer",
        role="vision",
        parameters_b=7,
        est_memory_gb=16,
        est_disk_gb=16,
        tool_parser=None,
        supports_vision=True,
        gated=False,
        notes="The default photo describer.",
    ),
    ModelInfo(
        id="Qwen/Qwen2.5-VL-3B-Instruct",
        label="Qwen2.5-VL 3B — lightest describer",
        role="vision",
        parameters_b=3,
        est_memory_gb=8,
        est_disk_gb=7,
        tool_parser=None,
        supports_vision=True,
        gated=False,
        notes="Lower quality transcription; use when memory is tight.",
    ),
)


def system_memory_gb() -> float | None:
    """Host RAM from /proc/meminfo (visible unlimited inside the container)."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
    except OSError:
        return None
    return None


def hardware_profile() -> dict:
    """Best-effort hardware facts the fit calculation compares against.

    GPU memory is only known when the operator/deploy script provides
    FAMILY_CFO_GPU_MEMORY_GB (nvidia-smi is unavailable in this container and
    reports N/A on unified-memory systems anyway). A null gpu_memory_gb means
    the UI should treat system memory as the budget with a unified/unknown note.
    """
    gpu_env = os.getenv("FAMILY_CFO_GPU_MEMORY_GB", "").strip()
    gpu_memory = None
    if gpu_env:
        try:
            gpu_memory = float(gpu_env)
        except ValueError:
            gpu_memory = None
    disk = shutil.disk_usage("/")
    return {
        "gpu_memory_gb": gpu_memory,
        "system_memory_gb": system_memory_gb(),
        "disk_free_gb": round(disk.free / 1024**3, 1),
        "source": "env" if gpu_memory is not None else "system",
    }
