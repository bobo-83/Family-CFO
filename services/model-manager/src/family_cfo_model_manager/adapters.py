"""Injected I/O adapters for the resolver (ADR 0081 S2, issue #170 Item 3).

planning.py stays pure; these adapters observe the real world and classify
everything they return. Evidence discipline: a fact is VERIFIED only when it
was observed (manifest bytes, successful config construction, hardware reads);
predictions are ESTIMATED; missing required evidence stays UNKNOWN and the
resolver refuses - it is never converted to zero, "fits", or a single-node
assumption. The Hub is respected the same way the serving runtime respects it:
HF_HUB_OFFLINE makes complete cached metadata acceptable and missing evidence
a refusal. Importable without FastAPI/Pydantic.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from family_cfo_model_manager.contracts import Evidence, ReasonCode
from family_cfo_model_manager.planning import (
    ModelFacts,
    NodeHardware,
    ProfilesDocument,
    load_profiles,
)

_HUB_TIMEOUT_SECONDS = 10.0
_HUB_API = "https://huggingface.co/api/models"
_REPO_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$"
)

# Bytes per parameter by safetensors dtype. U8 entries are FP4-packed byte
# PAIRS - never read as uncompressed parameters (plan: memory policy).
_DTYPE_BYTES = {
    "F64": 8.0,
    "F32": 4.0,
    "BF16": 2.0,
    "F16": 2.0,
    "I64": 8.0,
    "I32": 4.0,
    "I16": 2.0,
    "I8": 1.0,
    "U8": 0.5,  # FP4 pair packing
    "F8_E4M3": 1.0,
    "F8_E5M2": 1.0,
    "F4": 0.5625,  # NVFP4
}


class MetadataUnavailable(Exception):
    """Hub/cache metadata could not be resolved.

    Carries the stable reason the caller must refuse with, so a metadata
    failure can never escape as an unhandled exception (which would surface
    as a 500 instead of the structured preflight refusal)."""

    def __init__(
        self, message: str, reason_code: ReasonCode = ReasonCode.INSUFFICIENT_EVIDENCE
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def validate_repo_id(value: str) -> str:
    """The only caller-supplied identifier ever used, strictly validated."""
    if not _REPO_ID_RE.match(value or ""):
        raise MetadataUnavailable(
            f"invalid model id: {value!r}", ReasonCode.INVALID_MODEL_ID
        )
    return value


class HubMetadataSource:
    """ModelFacts from the Hub API, or the local HF cache when offline."""

    def __init__(
        self,
        *,
        cache_root: Path | str | None = None,
        token_env: str = "HUGGING_FACE_HUB_TOKEN",
    ) -> None:
        self._cache_root = Path(
            cache_root or os.environ.get("HF_HOME", "~/.cache/huggingface")
        ).expanduser() / "hub"
        self._token_env = token_env

    def facts_for(self, repo_id: str) -> ModelFacts:
        validate_repo_id(repo_id)
        offline = os.environ.get("HF_HUB_OFFLINE", "").strip() == "1"
        if offline:
            return self._facts_from_cache(repo_id)
        try:
            return self._facts_from_hub(repo_id)
        except MetadataUnavailable:
            cached = self._try_cache(repo_id)
            if cached is not None:
                return cached
            raise

    # --- Hub ------------------------------------------------------------------

    def _facts_from_hub(self, repo_id: str) -> ModelFacts:
        document = self._hub_document(repo_id)
        return self._facts_from_document(repo_id, document, Evidence.VERIFIED)

    def _hub_document(self, repo_id: str) -> dict:
        url = f"{_HUB_API}/{repo_id}?blobs=true"
        request = urllib.request.Request(url)
        token = os.environ.get(self._token_env, "").strip()
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=_HUB_TIMEOUT_SECONDS) as response:
                if response.status == 404:
                    raise MetadataUnavailable(
                        f"model '{repo_id}' was not found",
                        ReasonCode.MODEL_NOT_FOUND,
                    )
                document = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise MetadataUnavailable(
                    f"access to '{repo_id}' was denied (gated model?)",
                    ReasonCode.MODEL_ACCESS_DENIED,
                ) from exc
            if exc.code == 404:
                raise MetadataUnavailable(
                    f"model '{repo_id}' was not found",
                    ReasonCode.MODEL_NOT_FOUND,
                ) from exc
            raise MetadataUnavailable(
                f"the model hub returned {exc.code} for '{repo_id}'",
                ReasonCode.PREFLIGHT_UNAVAILABLE,
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise MetadataUnavailable(
                f"the model hub is unreachable: {type(exc).__name__}",
                ReasonCode.PREFLIGHT_UNAVAILABLE,
            ) from exc
        if not isinstance(document, dict):
            raise MetadataUnavailable(
                f"the hub returned an unexpected shape for '{repo_id}'",
                ReasonCode.INSUFFICIENT_EVIDENCE,
            )
        return document

    def _facts_from_document(
        self, repo_id: str, document: dict, evidence: Evidence
    ) -> ModelFacts:
        config = document.get("config") or {}
        architectures = config.get("architectures") or []
        architecture = architectures[0] if architectures else None
        revision = document.get("sha")
        weights = self._resident_weight_bytes(document)
        kv_per_token = self._kv_bytes_per_token(config)
        multimodal: bool | None = None
        if isinstance(config, dict) and config:
            multimodal = "vision_config" in config
        context = config.get("max_position_embeddings")
        return ModelFacts(
            repo_id=repo_id,
            revision=str(revision) if revision else None,
            architecture=architecture,
            quantization=self._quantization(config),
            resident_weight_bytes=weights,
            multimodal=multimodal,
            model_context_limit=int(context) if isinstance(context, int) else None,
            evidence=evidence,
            kv_bytes_per_token=kv_per_token,
        )

    @staticmethod
    def _quantization(config: dict) -> str | None:
        quant = config.get("quantization_config") or {}
        method = quant.get("quant_method")
        return str(method) if method else None

    @staticmethod
    def _resident_weight_bytes(document: dict) -> int | None:
        parameters = document.get("safetensors") or {}
        counts = parameters.get("parameters")
        if not isinstance(counts, dict) or not counts:
            return None
        total = 0.0
        for dtype, count in counts.items():
            per_param = _DTYPE_BYTES.get(str(dtype).upper())
            if per_param is None:
                return None  # unknown dtype: not convertible, never guessed
            total += float(count) * per_param
        return int(total)

    @staticmethod
    def _kv_bytes_per_token(config: dict) -> int | None:
        """Architecture-aware KV estimate for standard GQA/MHA layouts.

        2 (K+V) x layers x kv_heads x head_dim x bytes_per_element. A
        nonstandard cache layout refuses upstream (insufficient_evidence)
        rather than estimating blindly.
        """
        try:
            layers = int(config["num_hidden_layers"])
            kv_heads = int(config.get("num_key_value_heads") or config["num_attention_heads"])
            head_dim = int(config.get("head_dim") or 0) or int(
                config["hidden_size"] // config["num_attention_heads"]
            )
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return None
        dtype = str(config.get("torch_dtype") or "bfloat16").lower()
        element_bytes = 1.0 if "fp8" in dtype or "float8" in dtype else 2.0
        return int(2 * layers * kv_heads * head_dim * element_bytes)

    # --- offline cache ------------------------------------------------------------

    def _facts_from_cache(self, repo_id: str) -> ModelFacts:
        cached = self._try_cache(repo_id)
        if cached is None:
            if not (self._cache_root / ("models--" + repo_id.replace("/", "--"))).is_dir():
                # Nothing of this repository is cached: everywhere this engine
                # is allowed to look, the model was not found.
                raise MetadataUnavailable(
                    f"model '{repo_id}' was not found in the local cache and "
                    f"HF_HUB_OFFLINE is set; retry online or complete the cache",
                    ReasonCode.MODEL_NOT_FOUND,
                )
            raise MetadataUnavailable(
                f"HF_HUB_OFFLINE is set and no complete cached metadata for "
                f"'{repo_id}' exists; retry online or complete the cache",
                ReasonCode.INSUFFICIENT_EVIDENCE,
            )
        return cached

    def _try_cache(self, repo_id: str) -> ModelFacts | None:
        directory = self._cache_root / ("models--" + repo_id.replace("/", "--"))
        snapshots = directory / "snapshots"
        if not snapshots.is_dir():
            return None
        for snapshot in sorted(snapshots.iterdir()):
            config_path = snapshot / "config.json"
            if not config_path.is_file():
                continue
            try:
                config = json.loads(config_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            weights = self._cache_weight_bytes(snapshot, config)
            document = {"config": config, "sha": snapshot.name, "safetensors": None}
            facts = self._facts_from_document(repo_id, document, Evidence.VERIFIED)
            # Cache facts keep the resolved weight byte count even though the
            # hub "safetensors" summary is absent offline.
            if facts.resident_weight_bytes is None and weights is not None:
                facts = ModelFacts(
                    repo_id=facts.repo_id,
                    revision=facts.revision,
                    architecture=facts.architecture,
                    quantization=facts.quantization,
                    resident_weight_bytes=weights,
                    multimodal=facts.multimodal,
                    model_context_limit=facts.model_context_limit,
                    evidence=Evidence.VERIFIED,
                    kv_bytes_per_token=facts.kv_bytes_per_token,
                )
            return facts
        return None

    def _cache_weight_bytes(self, snapshot: Path, config: dict) -> int | None:
        """Active weight bytes from the index json (selected format), or the
        total of present safetensors files."""
        index = snapshot / "model.safetensors.index.json"
        if index.is_file():
            try:
                document = json.loads(index.read_text())
                return int(document.get("metadata", {}).get("total_size") or 0) or None
            except (OSError, json.JSONDecodeError, ValueError):
                return None
        total = 0
        found = False
        for weight_file in snapshot.glob("*.safetensors"):
            found = True
            try:
                total += weight_file.stat().st_size
            except OSError:
                return None
        return total if found else None


class LocalHardwareSource:
    """One node's hardware facts: unified memory IS one pool (ADR 0071).

    Memory comes from /proc/meminfo (the host total, even inside the manager
    container) with a sysctl fallback on macOS; the model-cache filesystem and
    free space are probed through an allowlisted read-only `docker exec df`
    into the runtime container, so the manager needs no cache mount of its
    own. FAMILY_CFO_NODE_MEMORY_GB overrides the probe with an operator-
    declared value (deterministic tests, constrained deployments); it is
    declared, not measured, so it classifies as ESTIMATED evidence.
    """

    def __init__(
        self,
        node: str = "primary",
        *,
        cache_probe: "callable[[str], tuple[int, int]] | None" = None,
    ) -> None:
        self._node = node
        self._cache_probe = cache_probe or self._docker_cache_probe

    def nodes(self) -> tuple[NodeHardware, ...]:
        declared = _declared_memory_gb()
        if declared is not None:
            memory_gb, evidence = declared, Evidence.ESTIMATED
        else:
            memory_gb = _meminfo_gb()
            evidence = Evidence.VERIFIED if memory_gb is not None else Evidence.UNKNOWN
        cache_gb: int | None = None
        free_gb: int | None = None
        try:
            cache_gb, free_gb = self._cache_probe("vllm")
        except Exception:  # noqa: BLE001 - probed facts stay unknown, never zero
            cache_gb, free_gb = None, None
        return (
            NodeHardware(
                node=self._node,
                memory_gb=memory_gb,
                cache_filesystem_gb=cache_gb,
                disk_free_gb=free_gb,
                resident_managed_gb=None,
                evidence=evidence,
            ),
        )

    @staticmethod
    def _docker_cache_probe(container: str) -> tuple[int, int]:
        import subprocess

        result = subprocess.run(
            ["docker", "exec", container, "df", "-k", "-P", "/root/.cache/huggingface"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise MetadataUnavailable("cache filesystem probe failed")
        for line in result.stdout.splitlines():
            parts = line.split()
            # POSIX df: Filesystem 1024-blocks Used Available Capacity ...
            if (
                len(parts) >= 4
                and parts[0] != "Filesystem"
                and parts[1].isdigit()
                and parts[3].isdigit()
            ):
                total_kb, avail_kb = int(parts[1]), int(parts[3])
                return total_kb / 1_000_000, avail_kb / 1_000_000
        raise MetadataUnavailable("cache filesystem probe was unparseable")


def _declared_memory_gb() -> float | None:
    """Operator-declared node memory: FAMILY_CFO_NODE_MEMORY_GB, in GiB.

    Takes precedence over the measured probe so tests and constrained
    deployments get deterministic admission. A declared value was not
    observed on this host, so callers must classify it below VERIFIED.
    """
    raw = os.getenv("FAMILY_CFO_NODE_MEMORY_GB", "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _meminfo_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return kb / 1_000_000
    except (OSError, ValueError, IndexError):
        pass
    if sys.platform == "darwin":
        # Unified-memory hosts without /proc (a macOS development box): the
        # host total is still the one memory pool.
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return int(result.stdout.strip()) / 1_000_000_000
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
    return None


class FileProfiles:
    """ProfileSource over profiles.json (fail-closed loader from planning)."""

    def __init__(self, path: Path | str) -> None:
        self._document: ProfilesDocument = load_profiles(path)

    def policy_for(self, repo_id: str, architecture: str | None):
        return self._document.policy_for(repo_id, architecture)
