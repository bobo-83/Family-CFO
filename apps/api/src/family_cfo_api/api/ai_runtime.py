import logging
import re
from dataclasses import asdict
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, undo_actions
from family_cfo_api.ai_catalog import MODEL_CATALOG, hardware_profile
from family_cfo_api.ai_runtime_selection import resolve_ai_config
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    AiApplyRequest,
    AiHardwareProfile,
    AiModelCatalog,
    AiModelInfo,
    AiRuntimeConfig,
    AiRuntimeStatus,
    AiSwapStatus,
    ErrorResponse,
)

router = APIRouter(tags=["AI Runtime"])
logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SECONDS = 2.0


def _validate_base_url(base_url: str, settings: Settings) -> None:
    """Reject any base_url outside the deployment allowlist (SSRF guard, ADR 0010).

    The server POSTs household financial context to this URL, so a free-form
    value would let an owner turn the API into an SSRF/exfiltration proxy.
    Pointing the model elsewhere is a deliberate operator act (edit
    FAMILY_CFO_AI_ALLOWED_BASE_URLS), not something a session can do.
    """
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=422, detail="base_url must be an http(s) URL")

    allowed = settings.allowed_ai_base_urls()
    if base_url.rstrip("/") not in {url.rstrip("/") for url in allowed}:
        raise HTTPException(
            status_code=422,
            detail="base_url is not in the allowed AI runtime list for this deployment",
        )


def _default_config(settings: Settings) -> AiRuntimeConfig:
    """The config a household inherits before saving its own — the deployment default.

    The Docker stack enables AI here via ``FAMILY_CFO_AI_*`` (it ships a vLLM
    service); a bare/non-Docker run leaves it disabled so no financial context
    is sent to a runtime that isn't there.
    """
    return AiRuntimeConfig(
        provider=settings.ai_default_provider,
        base_url=settings.ai_default_base_url,
        model=settings.ai_default_model,
        enabled=settings.ai_default_enabled,
    )


def _to_schema(record: repository.AiRuntimeConfigRecord) -> AiRuntimeConfig:
    return AiRuntimeConfig(
        provider=record.provider,
        base_url=record.base_url,
        model=record.model,
        enabled=record.enabled,
    )


@router.get(
    "/ai/runtime",
    operation_id="getAiRuntimeConfig",
    response_model=AiRuntimeConfig,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Get active AI runtime configuration",
)
async def get_ai_runtime_config(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> AiRuntimeConfig:
    record = repository.get_ai_runtime_config(engine, session.household_id)
    return _to_schema(record) if record is not None else _default_config(settings)


def _probe_served_model(base_url: str) -> tuple[bool, str | None]:
    """Ask the runtime for its loaded model; (ready, served_model_id) with a short timeout."""
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/v1/models", timeout=_PROBE_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json().get("data") or []
        if data:
            return True, data[0].get("id")
        return False, None
    except (httpx.HTTPError, ValueError, KeyError):
        return False, None


# --- M50: explain what "loading" actually means -------------------------------

_ERROR_MARKERS = ("ValueError", "RuntimeError", "CUDA out of memory", "Traceback", "Error:")
_DOWNLOAD_RE = re.compile(r"([\w.-]+\.safetensors)[^\n]*?(\d{1,3})%")
_SHARDS_RE = re.compile(r"Loading safetensors checkpoint shards[^\n]*?(\d{1,3})\s*%")


def classify_vllm_logs(text: str) -> tuple[str, str] | None:
    """Map a vLLM log tail to (phase, human detail). Pure; unit-tested."""
    if not text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # A crash beats everything: surface the last error-ish line.
    for line in reversed(lines):
        if any(marker in line for marker in _ERROR_MARKERS):
            cleaned = line.split(")", 1)[-1].strip() if "(EngineCore" in line else line
            # Drop compose prefixes like "vllm-1  | ".
            cleaned = cleaned.split("| ", 1)[-1].strip()
            return "error", cleaned[:300]

    shard = None
    for match in _SHARDS_RE.finditer(text):
        shard = match.group(1)
    if shard is not None:
        return "loading", f"Loading weights into memory — {shard}%"

    download = None
    for match in _DOWNLOAD_RE.finditer(text):
        download = match
    if download is not None:
        return "downloading", f"Downloading {download.group(1)} — {download.group(2)}%"
    if "Downloading" in text or "download" in text.lower():
        return "downloading", "Downloading model weights…"

    if "Capturing CUDA graph" in text or "torch.compile" in text or "Warming up" in text:
        return "warming_up", "Warming up (compiling kernels)…"

    return "starting", "Starting the engine…"


def _loading_status_from_manager(settings: Settings) -> tuple[str, str] | None:
    """Fetch the vLLM log tail via the model manager and classify it."""
    if not settings.model_manager_url:
        return None
    try:
        response = httpx.get(
            f"{settings.model_manager_url.rstrip('/')}/logs",
            params={"service": "vllm", "tail": 40},
            timeout=_HF_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return classify_vllm_logs(response.json().get("lines", ""))
    except (httpx.HTTPError, ValueError):
        return None


@router.get(
    "/ai/runtime/status",
    operation_id="getAiRuntimeStatus",
    response_model=AiRuntimeStatus,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Report whether the AI runtime is loaded and which model is serving",
)
async def get_ai_runtime_status(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> AiRuntimeStatus:
    config = resolve_ai_config(engine, session.household_id, settings)
    vision_enabled = settings.ai_supports_vision or (
        settings.ai_vision_enabled and bool(settings.ai_vision_model)
    )
    if not config.is_usable:
        return AiRuntimeStatus(
            enabled=config.enabled,
            provider=config.provider,
            model=config.model,
            ready=False,
            served_model=None,
            detail=(
                "AI runtime is disabled; the advisor answers from deterministic calculations."
                if not config.enabled
                else "AI runtime is enabled but not fully configured."
            ),
            vision_enabled=vision_enabled,
        )

    ready, served_model = _probe_served_model(config.base_url)
    loading_phase: str | None = None
    loading_detail: str | None = None
    if ready:
        detail = f"AI model '{served_model or config.model}' is loaded and answering."
    else:
        detail = (
            "AI model is starting up (still loading); answers are deterministic "
            "until it is ready."
        )
        # M50: say what "loading" actually means (or that it crashed).
        if config.provider == "vllm":
            classified = _loading_status_from_manager(settings)
            if classified is not None:
                loading_phase, loading_detail = classified

    # Vision (ADR 0011): the main model if marked vision-capable, else the describer.
    vision_ready = False
    vision_model: str | None = None
    if settings.ai_supports_vision and ready:
        vision_ready, vision_model = True, served_model or config.model
    elif settings.ai_vision_enabled and settings.ai_vision_model:
        vision_ready, vision_served = _probe_served_model(settings.ai_vision_base_url)
        vision_model = vision_served or settings.ai_vision_model

    return AiRuntimeStatus(
        enabled=True,
        provider=config.provider,
        model=config.model,
        ready=ready,
        served_model=served_model,
        detail=detail,
        vision_ready=vision_ready,
        vision_model=vision_model,
        vision_enabled=vision_enabled,
        loading_phase=loading_phase,
        loading_detail=loading_detail,
    )


@router.get(
    "/ai/models",
    operation_id="listAiModels",
    response_model=AiModelCatalog,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List the curated model catalog for the runtime picker",
)
async def list_ai_models(
    session: repository.SessionContext = Depends(get_current_session),
) -> AiModelCatalog:
    return AiModelCatalog(models=[AiModelInfo(**asdict(model)) for model in MODEL_CATALOG])


@router.get(
    "/ai/hardware",
    operation_id="getAiHardwareProfile",
    response_model=AiHardwareProfile,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Report best-effort hardware facts for model-fit planning",
)
async def get_ai_hardware_profile(
    session: repository.SessionContext = Depends(get_current_session),
) -> AiHardwareProfile:
    return AiHardwareProfile(**hardware_profile())


@router.put(
    "/ai/runtime",
    operation_id="updateAiRuntimeConfig",
    response_model=AiRuntimeConfig,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Update AI runtime configuration",
)
async def update_ai_runtime_config(
    payload: AiRuntimeConfig,
    session: repository.SessionContext = Depends(require_role("owner")),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> AiRuntimeConfig:
    _validate_base_url(payload.base_url, settings)
    before = repository.get_ai_runtime_config(engine, session.household_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "ai_runtime.updated",
        "ai_runtime_config",
        session.household_id,
        f"AI runtime set to {payload.provider}/{payload.model} (enabled={payload.enabled})",
        undo_token=undo_actions.ai_runtime_updated(before),
    )
    record = repository.upsert_ai_runtime_config(
        engine,
        household_id=session.household_id,
        provider=payload.provider,
        base_url=payload.base_url,
        model=payload.model,
        enabled=payload.enabled,
    )
    return _to_schema(record)


# --- Hugging Face search + one-click apply (ADR 0013) -------------------------

# org/name with a conservative charset — mirrored in the model-manager sidecar.
_REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_PARAMS_IN_NAME = re.compile(r"(\d+(?:\.\d+)?)\s*[bB](?:[-_.]|$)")
_HF_TIMEOUT_SECONDS = 6.0


def _bytes_per_param(model_id: str) -> tuple[float, str]:
    """GB-per-billion-params from quantization markers in the name (M49)."""
    lower = model_id.lower()
    if any(marker in lower for marker in ("awq", "gptq", "int4", "4bit", "4-bit")):
        return 0.65, "4-bit"
    if any(marker in lower for marker in ("fp8", "int8", "8bit", "8-bit")):
        return 1.1, "8-bit"
    return 2.1, "bf16"


# Formats vLLM cannot serve — they crowd the pool without being usable (M54).
_UNSERVABLE_MARKERS = ("gguf", "mlx", "bnb", "exl2", "onnx", "openvino")


def _estimate_from_hf(item: dict, pipeline: str) -> AiModelInfo | None:
    """Map an HF Hub result to catalog shape with ESTIMATED specs (ADR 0013)."""
    model_id = item.get("modelId") or item.get("id") or ""
    if not _REPO_ID.match(model_id):
        return None
    lower_id = model_id.lower()
    if any(marker in lower_id for marker in _UNSERVABLE_MARKERS):
        return None
    match = _PARAMS_IN_NAME.search(model_id.rsplit("/", 1)[-1])
    params = float(match.group(1)) if match else 0.0
    is_vision = pipeline == "image-text-to-text" or "-vl-" in model_id.lower()
    lower = model_id.lower()
    parser = "llama3_json" if "llama" in lower else "hermes"
    gb_per_b, precision = _bytes_per_param(model_id)
    return AiModelInfo(
        id=model_id,
        label=f"{model_id.rsplit('/', 1)[-1]} (Hugging Face)",
        role="both" if is_vision else "main",
        parameters_b=params,
        # Precision-aware: bf16 ~2 GB/B, fp8/int8 ~1.1, 4-bit ~0.65 (M49).
        est_memory_gb=round(params * gb_per_b) if params else 0,
        est_disk_gb=round(params * gb_per_b * 0.95) if params else 0,
        tool_parser=None if pipeline == "image-text-to-text" else parser,
        supports_vision=is_vision,
        gated=bool(item.get("gated")),
        notes=(
            f"Estimated specs from the model name ({precision}) — verify before "
            "relying on the fit verdict."
        ),
        # M71: lets the picker rank modern models above old ones.
        created_at=item.get("createdAt"),
    )


# M53: deliberate size/quant fan-out — HF cannot sort by parameter count, so
# "optimal for this server" needs hinted queries; the download charts alone
# never surface big-but-unpopular models.
_DEEP_HINTS = ("", "AWQ", "FP8", "70B", "72B", "90B", "110B", "A22B")


def _hf_model_exists(hub_url: str, model_id: str) -> bool | None:
    """True/False when the hub answers; None (skip the check) when unreachable."""
    try:
        response = httpx.get(
            f"{hub_url.rstrip('/')}/api/models/{model_id}", timeout=_HF_TIMEOUT_SECONDS
        )
    except httpx.HTTPError:
        return None
    if response.status_code == 200:
        return True
    if response.status_code in (401, 403, 404):
        # 401/403 = gated or nonexistent private repo — either way the swap
        # script's anonymous download would fail, so treat as not available.
        return False
    return None


def _fetch_hf_page(hub_url: str, pipe: str, q: str, limit: int) -> list[dict]:
    params: dict[str, str | int] = {"pipeline_tag": pipe, "sort": "downloads", "limit": limit}
    if q:
        params["search"] = q
    response = httpx.get(f"{hub_url}/api/models", params=params, timeout=_HF_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


@router.get(
    "/ai/models/search",
    operation_id="searchAiModels",
    response_model=AiModelCatalog,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        503: {"description": "Hugging Face unreachable", "model": ErrorResponse},
    },
    summary="Search Hugging Face for models (estimated specs)",
)
async def search_ai_models(
    q: str = "",
    pipeline: str = "any",
    limit: int = 10,
    deep: bool = False,
    session: repository.SessionContext = Depends(get_current_session),
    settings: Settings = Depends(get_app_settings),
) -> AiModelCatalog:
    # M48: an empty q returns the pipeline's most-downloaded models — the live
    # lists behind the dashboard's quick filters. M53: deep=true fans out over
    # size/quant-hinted queries so the largest FITTING models surface too.
    if pipeline not in ("any", "text-generation", "image-text-to-text"):
        raise HTTPException(status_code=422, detail="invalid pipeline")
    limit = max(1, min(limit, 30))
    pipelines = (
        ("text-generation", "image-text-to-text") if pipeline == "any" else (pipeline,)
    )
    queries: tuple[str, ...] = (q,) if not deep else tuple(
        dict.fromkeys((q, *_DEEP_HINTS))  # user's q first, deduped, order kept
    )

    models: list[AiModelInfo] = []
    seen: set[str] = set()
    jobs = [(pipe, query) for pipe in pipelines for query in queries]
    try:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
            pages = list(
                pool.map(
                    lambda job: _fetch_hf_page(settings.hf_hub_url, job[0], job[1], limit),
                    jobs,
                )
            )
        for (pipe, _query), page in zip(jobs, pages):
            for item in page:
                mapped = _estimate_from_hf(item, pipe)
                if mapped and mapped.id not in seen:
                    seen.add(mapped.id)
                    models.append(mapped)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="Hugging Face is unreachable; showing curated models only"
        ) from exc
    return AiModelCatalog(models=models[:120])


@router.post(
    "/ai/runtime/apply",
    operation_id="applyAiModelSelection",
    response_model=AiSwapStatus,
    status_code=202,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        409: {"description": "A swap is already running", "model": ErrorResponse},
        503: {"description": "Model manager unavailable", "model": ErrorResponse},
    },
    summary="Apply a model selection: download and switch the served models",
)
async def apply_ai_model_selection(
    payload: AiApplyRequest,
    session: repository.SessionContext = Depends(require_role("owner")),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> AiSwapStatus:
    if not settings.model_manager_url:
        raise HTTPException(
            status_code=503,
            detail="Model manager is not available; use scripts/swap-model.sh on the server",
        )
    if not _REPO_ID.match(payload.main_model):
        raise HTTPException(status_code=422, detail="invalid main model id")
    if payload.vision_model is not None and not _REPO_ID.match(payload.vision_model):
        raise HTTPException(status_code=422, detail="invalid vision model id")
    # M66: a typo'd id would tear the containers down and then fail at
    # download. Verify each repo exists on the hub first; when the hub is
    # unreachable the check is skipped (offline-tolerant).
    for model_id in (payload.main_model, payload.vision_model):
        if model_id is not None and _hf_model_exists(settings.hf_hub_url, model_id) is False:
            raise HTTPException(
                status_code=422,
                detail=f"'{model_id}' was not found on Hugging Face — check the model id",
            )
    # M51: never run two instances of the same model. Identical main+vision
    # collapses to the single-instance path (the swap script serves one
    # container; photos work iff the model is actually vision-capable).
    if payload.vision_model == payload.main_model:
        payload.vision_model = None

    try:
        response = httpx.post(
            f"{settings.model_manager_url.rstrip('/')}/swap",
            json={"main_model": payload.main_model, "vision_model": payload.vision_model},
            timeout=_HF_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Model manager unreachable") from exc
    if response.status_code == 409:
        raise HTTPException(status_code=409, detail="A model swap is already in progress")
    if response.status_code >= 400:
        raise HTTPException(status_code=503, detail="Model manager rejected the request")

    # Keep the household config in sync so status/mismatch reflect the new target.
    repository.upsert_ai_runtime_config(
        engine,
        household_id=session.household_id,
        provider="vllm",
        base_url=settings.ai_default_base_url,
        model=payload.main_model,
        enabled=True,
    )
    logger.info(
        "model apply started household_id=%s main=%s vision=%s",
        session.household_id,
        payload.main_model,
        payload.vision_model,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "ai_runtime.model_applied",
        "ai_runtime_config",
        session.household_id,
        f"Model swap started: main={payload.main_model} vision={payload.vision_model or 'none'}",
    )
    return AiSwapStatus(**response.json())


@router.get(
    "/ai/runtime/apply/status",
    operation_id="getAiApplyStatus",
    response_model=AiSwapStatus,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Report the state of the last model apply",
)
async def get_ai_apply_status(
    session: repository.SessionContext = Depends(get_current_session),
    settings: Settings = Depends(get_app_settings),
) -> AiSwapStatus:
    if not settings.model_manager_url:
        return AiSwapStatus(state="unavailable")
    try:
        response = httpx.get(
            f"{settings.model_manager_url.rstrip('/')}/status", timeout=_HF_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return AiSwapStatus(**response.json())
    except (httpx.HTTPError, ValueError):
        return AiSwapStatus(state="unavailable")
