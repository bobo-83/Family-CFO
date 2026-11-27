import dataclasses
import logging
import os
import re
import uuid
from dataclasses import asdict
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from family_cfo_api import (
    ai_study,
    audit,
    household_crypto,
    models,
    repository,
    rights,
    undo_actions,
)
from family_cfo_api.ai_catalog import MODEL_CATALOG, cluster_peer_status, hardware_profile
from family_cfo_api.ai_runtime_selection import resolve_ai_config
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_current_session, get_engine, require_right
from family_cfo_api.error_envelope import error_response
from family_cfo_api.model_manager_client import (
    ManagerJobError,
    ManagerJobStatus,
    ManagerProtocolError,
    ManagerTransportError,
    ModelManagerClient,
    monotonic,
)
from family_cfo_api.schemas import (
    AiApplyJobStatus,
    AiApplyPlanSummary,
    AiApplyRequest,
    AiApplySelectionSummary,
    AiHardwareProfile,
    AiModelCatalog,
    AiModelDetail,
    AiModelInfo,
    AiRetryRecoveryRequest,
    AiRuntimeConfig,
    AiRuntimeStatus,
    AiStudyInsight,
    AiStudyStatus,
    AiUsageResponse,
    ErrorResponse,
    HouseholdUsage,
    ModelAnswerStats,
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
        cluster_enabled=record.cluster_enabled,
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
    # Felt-latency history is evidence regardless of the runtime's state.
    answer_stats = [
        ModelAnswerStats(model=model, median_ms=median_ms, samples=samples)
        for model, median_ms, samples in repository.answer_time_stats(engine, session.household_id)
    ]
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
            answer_stats=answer_stats,
        )

    ready, served_model = _probe_served_model(config.base_url)
    loading_phase: str | None = None
    loading_detail: str | None = None
    if ready:
        detail = f"AI model '{served_model or config.model}' is loaded and answering."
    else:
        detail = (
            "AI model is starting up (still loading); answers are deterministic until it is ready."
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
        answer_stats=answer_stats,
    )


@router.get(
    "/ai/study",
    operation_id="getAiStudyStatus",
    response_model=AiStudyStatus,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Report how much of the transaction history the advisor has studied",
)
async def get_ai_study_status(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> AiStudyStatus:
    status = ai_study.study_status(engine, session.household_id, settings)
    coverage = (
        100
        if status.total_months == 0
        else round(status.studied_months / status.total_months * 100)
    )
    return AiStudyStatus(
        total_months=status.total_months,
        studied_months=status.studied_months,
        coverage_percent=coverage,
        last_studied_at=status.last_studied_at,
        runtime_usable=status.runtime_usable,
        insights=[
            AiStudyInsight(key=m.key, value=m.value, updated_at=m.updated_at)
            for m in status.insights
        ],
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
    "/ai/models/detail",
    operation_id="getAiModelDetail",
    response_model=AiModelDetail,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        404: {"description": "Unknown model", "model": ErrorResponse},
        503: {"description": "Hugging Face unreachable", "model": ErrorResponse},
    },
    summary="Drill-down for one model: curated/estimated specs + live hub stats",
)
# Sync on purpose (ruff ASYNC210, #183): these handlers make BLOCKING HTTP
# calls (Hugging Face metadata, the model-manager sidecar) with multi-second
# timeouts. As `def`, FastAPI runs them in the threadpool instead of stalling
# the event loop for every concurrent request.
def get_ai_model_detail(
    id: str,
    session: repository.SessionContext = Depends(get_current_session),
    settings: Settings = Depends(get_app_settings),
) -> AiModelDetail:
    # The id is a query param (repo ids contain '/'); same charset guard as
    # the swap flow so the hub URL can't be shaped by the client.
    if not _REPO_ID.match(id):
        raise HTTPException(status_code=422, detail="invalid model id")
    curated = next((m for m in MODEL_CATALOG if m.id == id), None)
    try:
        response = httpx.get(
            f"{settings.hf_hub_url.rstrip('/')}/api/models/{id}",
            timeout=_HF_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        if curated is not None:
            # The hub being down must not blank the drill-down for models we
            # already know everything important about.
            return AiModelDetail(info=AiModelInfo(**asdict(curated)))
        raise HTTPException(status_code=503, detail="Hugging Face is unreachable") from exc
    if response.status_code in (401, 403, 404):
        if curated is not None:
            return AiModelDetail(info=AiModelInfo(**asdict(curated)))
        raise HTTPException(status_code=404, detail="Model not found on the hub")
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="Hugging Face is unreachable")
    item = response.json()
    info = (
        AiModelInfo(**asdict(curated))
        if curated is not None
        else _estimate_from_hf(item, item.get("pipeline_tag") or "text-generation")
    )
    if info is None:
        raise HTTPException(status_code=404, detail="Model is not servable by the runtime")
    card = item.get("cardData") or {}
    license_value = card.get("license")
    return AiModelDetail(
        info=info,
        downloads=item.get("downloads"),
        likes=item.get("likes"),
        last_modified=item.get("lastModified"),
        # Bounded: HF tag lists run long; the drill-down shows a handful.
        tags=[t for t in (item.get("tags") or []) if isinstance(t, str)][:16],
        license=license_value if isinstance(license_value, str) else None,
    )


@router.get(
    "/ai/hardware",
    operation_id="getAiHardwareProfile",
    response_model=AiHardwareProfile,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Report best-effort hardware facts for model-fit planning",
)
async def get_ai_hardware_profile(
    session: repository.SessionContext = Depends(get_current_session),
    settings: Settings = Depends(get_app_settings),
) -> AiHardwareProfile:
    profile = hardware_profile()
    # #182: a resident dedicated describer shrinks what the MAIN model can
    # actually claim (the 0.65-fraction lesson from the MiniMax bring-up).
    if not settings.ai_supports_vision:
        profile["vision_reserved_gb"] = _vision_slot_gb(settings)
    return AiHardwareProfile(**profile)


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
    session: repository.SessionContext = Depends(require_right(rights.AI_RUNTIME_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> AiRuntimeConfig:
    _validate_base_url(payload.base_url, settings)
    before = repository.get_ai_runtime_config(engine, session.household_id)
    # Issue #170 (ADR 0081): a delayed apply commit must never race an
    # Advanced save. PUTs affecting the managed runtime selection or the
    # household's cluster consent are refused while a replacement is
    # unfinished (detection cached <=1 s; fail closed when the manager state
    # cannot be established), and every config PUT serializes with the
    # receipt-commit transaction on the same advisory lock.
    if _affects_managed_deployment(payload, before, settings):
        _refuse_while_replacement_in_flight(settings)
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
        cluster_enabled=payload.cluster_enabled,
        deployment_locked=True,
    )
    return _to_schema(record)


# --- managed-deployment write exclusion (issue #170, ADR 0081) -----------------

_DEPLOYMENT_STATE_CACHE_TTL_SECONDS = 1.0
_deployment_state_cache: tuple[float, str] | None = None


def _affects_managed_deployment(
    payload: AiRuntimeConfig, before: repository.AiRuntimeConfigRecord | None, settings: Settings
) -> bool:
    """Whether this PUT touches the managed runtime selection or cluster consent."""
    if payload.provider == "vllm" and payload.base_url == settings.ai_default_base_url:
        return True
    return before is not None and payload.cluster_enabled != before.cluster_enabled


def _managed_deployment_state(settings: Settings) -> str:
    """`busy`/`free`/`unknown` — is a replacement transaction in flight?

    Cached <=1 s in this process (bounded added latency in the PUT path).
    A mixed protocol pair (`manager_upgrade_required`) cannot establish v2
    state and therefore reads as `unknown`: fail closed, never guess.
    """
    global _deployment_state_cache
    if not settings.model_manager_url:
        return "free"  # no manager configured: no managed deployment can run
    now = monotonic()
    if (
        _deployment_state_cache is not None
        and now - _deployment_state_cache[0] < _DEPLOYMENT_STATE_CACHE_TTL_SECONDS
    ):
        return _deployment_state_cache[1]
    state = "unknown"
    try:
        client = _manager_client(settings)
        client.verify_protocol()
        current = client.current_job()
        state = "busy" if current is not None and current.is_unfinished else "free"
    except (ManagerTransportError, ManagerProtocolError, ManagerJobError):
        state = "unknown"
    _deployment_state_cache = (now, state)
    return state


def _refuse_while_replacement_in_flight(settings: Settings) -> None:
    state = _managed_deployment_state(settings)
    if state == "busy":
        raise HTTPException(
            status_code=409,
            detail=(
                "A model replacement is in progress; runtime settings are "
                "locked until it commits or is restored"
            ),
        )
    if state == "unknown":
        raise HTTPException(
            status_code=503,
            detail=(
                "The model manager's apply state could not be established; "
                "runtime settings fail closed until it answers"
            ),
        )


# --- Hugging Face search + one-click apply (ADR 0013) -------------------------

# org/name with a conservative charset — mirrored in the model-manager sidecar.
_REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_PARAMS_IN_NAME = re.compile(r"(\d+(?:\.\d+)?)\s*[bB](?:[-_.]|$)")
_HF_TIMEOUT_SECONDS = 6.0


def _bytes_per_param(model_id: str) -> tuple[float, str]:
    """GB-per-billion-params from quantization markers in the name (M49)."""
    lower = model_id.lower()
    if any(
        marker in lower
        for marker in ("awq", "gptq", "int4", "4bit", "4-bit", "nvfp4", "mxfp4", "fp4")
    ):
        return 0.65, "4-bit"
    if any(marker in lower for marker in ("fp8", "int8", "8bit", "8-bit")):
        return 1.1, "8-bit"
    return 2.1, "bf16"


# Formats vLLM cannot serve — they crowd the pool without being usable (M54).
_UNSERVABLE_MARKERS = ("gguf", "mlx", "bnb", "exl2", "onnx", "openvino")


def _vision_slot_gb(settings: Settings) -> float | None:
    """The dedicated describer's memory budget: fraction × one node's memory."""
    profile = hardware_profile()
    node_gb = profile.get("gpu_memory_gb") or profile.get("system_memory_gb")
    if node_gb is None or settings.vision_gpu_fraction <= 0:
        return None
    return node_gb * settings.vision_gpu_fraction


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
    pipelines = ("text-generation", "image-text-to-text") if pipeline == "any" else (pipeline,)
    queries: tuple[str, ...] = (
        (q,)
        if not deep
        else tuple(
            dict.fromkeys((q, *_DEEP_HINTS))  # user's q first, deduped, order kept
        )
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


# --- one-shot apply admission, read-only status, recovery (issue #170) ---------


def _manager_client(settings: Settings) -> ModelManagerClient:
    """The typed v2 transport with the dedicated apply budget (spec 04)."""
    return ModelManagerClient(
        settings.model_manager_url,
        settings.model_manager_credential,
        apply_timeout_seconds=settings.model_manager_apply_timeout_seconds,
    )


def _apply_error(status_code: int, code: str, message: str) -> JSONResponse:
    """A structured apply error: the stable reason lives in error.code (spec 04)."""
    return JSONResponse(status_code=status_code, content=error_response(code, message))


def _manager_failure(exc: Exception) -> JSONResponse:
    """Safe conversion of a manager failure to the public error envelope.

    Specific safe causes pass through with their stable codes; anything else
    is a 503 that preserves the current configuration."""
    if isinstance(exc, ManagerJobError):
        if exc.status_code in (404, 409, 422):
            return _apply_error(exc.status_code, exc.code, exc.message)
        return _apply_error(503, exc.code, exc.message)
    if isinstance(exc, ManagerProtocolError):
        return _apply_error(503, "manager_upgrade_required", str(exc))
    return _apply_error(503, "manager_unavailable", "Model manager unreachable")


@dataclasses.dataclass(frozen=True, slots=True)
class _NormalizedSelection:
    """The API-internal normalized request — exactly what the manager receives.

    `photo_mode` may still be `auto`: the MANAGER resolves it authoritatively
    from installed metadata (its `normalize_photo_mode`). The public
    `AiApplySelectionSummary` is resolved-only by contract, so the request
    shape never rides through it.
    """

    main_model: str
    vision_model: str | None
    photo_mode: str
    topology: str


def _synthesize_request_id(selection: _NormalizedSelection) -> uuid.UUID:
    """The idempotency identity for a legacy body without request_id.

    Deterministic in the NORMALIZED request: the same selection replayed
    resolves to the same identity (the manager replays it as a read, never a
    second launch); different content is genuinely a different request."""
    identity = "|".join(
        (
            selection.main_model,
            selection.vision_model or "",
            selection.photo_mode,
            selection.topology,
        )
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, f"family-cfo-ai-apply|{identity}")


def _validate_apply_selection(payload: AiApplyRequest, settings: Settings) -> _NormalizedSelection:
    """Preflight input guards that stay API-side (spec 04's safe causes)."""
    if not _REPO_ID.match(payload.main_model):
        raise _ApplyRefusal(422, "invalid_model_id", "invalid main model id")
    if payload.vision_model is not None and not _REPO_ID.match(payload.vision_model):
        raise _ApplyRefusal(422, "invalid_model_id", "invalid vision model id")
    # M66: a typo'd id would tear the containers down and then fail at
    # download. Verify each repo exists on the hub first; when the hub is
    # unreachable the check is skipped (offline-tolerant — the manager
    # resolves pinned metadata and refuses with model_not_found itself).
    for model_id in (payload.main_model, payload.vision_model):
        if model_id is not None and _hf_model_exists(settings.hf_hub_url, model_id) is False:
            raise _ApplyRefusal(
                422, "model_not_found", f"'{model_id}' was not found on Hugging Face"
            )
    vision_model = payload.vision_model
    photo_mode = payload.photo_mode
    # M51: never run two instances of the same model. Identical main+vision
    # normalizes to one dual-capable instance with photos routed to it; the
    # manager refuses when the model cannot actually accept images.
    if vision_model == payload.main_model:
        vision_model = None
        photo_mode = "main"
    # The selected mode controls routing — it never contradicts itself.
    if photo_mode in ("main", "off") and vision_model is not None:
        raise _ApplyRefusal(
            422,
            "unsupported_photo_mode",
            f"photo_mode '{photo_mode}' cannot be combined with a separate vision model",
        )
    if photo_mode == "separate" and vision_model is None:
        raise _ApplyRefusal(
            422, "unsupported_photo_mode", "photo_mode 'separate' requires a distinct vision model"
        )
    # Memory fit is the MANAGER's admission call (ADR 0081): its evidence-based
    # planning refuses a photo model that cannot run. The API host may not be a
    # GPU box (two-Spark: peers hold them), so host-memory math here is not
    # placement evidence — it only produced false refusals (#170).
    return _NormalizedSelection(
        main_model=payload.main_model,
        vision_model=vision_model,
        photo_mode=photo_mode,
        topology=payload.topology,
    )


class _ApplyRefusal(Exception):
    """A structured preflight refusal with a stable public reason."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _cluster_consent(engine: Engine, household_id: str, topology: str) -> bool:
    """The household's stored cluster consent — never the public request (ADR 0071/0081)."""
    if topology != "cluster":
        # auto/single: consent rides along as authorization evidence the
        # manager may only use for a VALID two-node plan.
        config = repository.get_ai_runtime_config(engine, household_id)
        return bool(config.cluster_enabled) if config else False
    config = repository.get_ai_runtime_config(engine, household_id)
    consent = bool(config.cluster_enabled) if config else False
    if not consent:
        raise _ApplyRefusal(
            409,
            "cluster_consent_required",
            "cluster serving needs the household's second-box setting",
        )
    # ADR 0071: a cluster apply is refused while the peer is down — a TP=2
    # engine with one node would crash-loop exactly like the first bring-up.
    _, peer_reachable = cluster_peer_status()
    if not peer_reachable:
        raise _ApplyRefusal(
            409,
            "cluster_peer_unreachable",
            "The second box is not reachable; cluster serving needs it",
        )
    return consent


def _write_apply_audit_once(
    engine: Engine,
    household_id: str,
    actor_user_id: str | None,
    action: str,
    job_id: str,
    summary: str,
) -> None:
    """One apply audit per job + action (the 0099 partial unique index).

    Replays (transport-ambiguous retries, reconciler races) hit the index and
    are swallowed — the operational record exists exactly once."""
    try:
        audit.write_audit(
            engine,
            household_id,
            actor_user_id,
            action,
            models.AI_RUNTIME_APPLY_JOB_ENTITY,
            job_id,
            summary,
        )
    except IntegrityError:
        logger.info("apply audit %s for job %s already recorded", action, job_id)


def _selection_schema(document: dict | None) -> AiApplySelectionSummary | None:
    if not document:
        return None
    try:
        return AiApplySelectionSummary(
            main_model=document.get("main_model") or "",
            vision_model=document.get("vision_model"),
            photo_mode=document.get("photo_mode") or "off",
            topology=document.get("topology") or "auto",
        )
    except ValueError:
        # A skewed manager version must degrade the summary, not 500 a read.
        logger.warning("unrecognized apply selection document: %s", document)
        return None


def _plan_schema(document: dict | None) -> AiApplyPlanSummary | None:
    if not document:
        return None
    try:
        return AiApplyPlanSummary(
            main_model=document.get("main_model") or "",
            main_revision=document.get("main_revision") or "",
            vision_model=document.get("vision_model"),
            vision_revision=document.get("vision_revision"),
            photo_mode=document.get("photo_mode") or "off",
            topology=document.get("topology") or "single",
            runtime_version=document.get("runtime_version") or "",
            image_digest=document.get("image_digest") or "",
            context_limit=int(document.get("context_limit") or 0),
            concurrency_limit=int(document.get("concurrency_limit") or 0),
            node_requirements=document.get("node_requirements") or [],
            warnings=list(document.get("warnings") or ()),
        )
    except (TypeError, ValueError):
        logger.warning("unrecognized apply plan document: %s", document)
        return None


def _projected_status(status: ManagerJobStatus) -> AiApplyJobStatus:
    """The additive legacy projection (ADR 0074 Decision 5): the coarse
    `state` enum plus the full lifecycle additively. Never mutates anything."""
    selection = status.requested_selection or status.previous_selection or {}
    return AiApplyJobStatus(
        job_id=status.job_id,
        request_id=status.request_id,
        sequence=status.sequence,
        state=status.coarse_state,
        lifecycle_state=status.state or None,
        main_model=selection.get("main_model"),
        vision_model=selection.get("vision_model"),
        log_tail=status.message or "",
        requested_selection=_selection_schema(status.requested_selection),
        resolved_plan=_plan_schema(status.resolved_plan),
        previous_selection=_selection_schema(status.previous_selection),
        created_at=status.created_at,
        updated_at=status.updated_at,
        deadline_at=status.deadline_at,
        reason_code=status.reason_code,
        message=status.message,
        recovery_available=status.recovery_available,
    )


def _no_job_status(
    state: str, *, reason_code: str | None = None, message: str | None = None
) -> AiApplyJobStatus:
    """The explicit idle/unavailable read: no job has been admitted."""
    return AiApplyJobStatus(
        job_id=None,
        request_id=None,
        sequence=None,
        state=state,
        lifecycle_state=None,
        main_model=None,
        vision_model=None,
        log_tail=message or "",
        requested_selection=None,
        resolved_plan=None,
        previous_selection=None,
        created_at=None,
        updated_at=None,
        deadline_at=None,
        reason_code=reason_code,
        message=message,
        recovery_available=False,
    )


@router.post(
    "/ai/runtime/apply",
    operation_id="applyAiModelSelection",
    response_model=AiApplyJobStatus,
    status_code=202,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        409: {
            "description": "A replacement is in flight or consent is missing",
            "model": ErrorResponse,
        },
        422: {
            "description": "The selection was rejected before any mutation",
            "model": ErrorResponse,
        },
        503: {"description": "Model manager unavailable or protocol skew", "model": ErrorResponse},
    },
    summary="Apply a model selection: validated launch, verified swap, committed settings",
)
def apply_ai_model_selection(
    payload: AiApplyRequest,
    session: repository.SessionContext = Depends(require_right(rights.AI_RUNTIME_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> AiApplyJobStatus | JSONResponse:
    if not settings.model_manager_url:
        return _apply_error(
            503,
            "manager_unavailable",
            "Model manager is not available; use scripts/swap-model.sh on the server",
        )
    if not settings.model_manager_credential.strip():
        # v2 mutators require the deployment-scoped credential; without it
        # nothing may mutate, so apply fails closed (ADR 0081 S7).
        return _apply_error(
            503,
            "manager_unavailable",
            "The model-manager deployment credential is not configured",
        )
    try:
        selection = _validate_apply_selection(payload, settings)
        topology = "cluster" if payload.cluster else payload.topology
        cluster_consent = _cluster_consent(engine, session.household_id, topology)
    except _ApplyRefusal as refusal:
        return _apply_error(refusal.status_code, refusal.code, refusal.message)

    request_id = payload.request_id or _synthesize_request_id(selection)
    client = _manager_client(settings)
    try:
        client.verify_protocol()
        status = client.admit(
            {
                "request_id": str(request_id),
                "main_model": selection.main_model,
                "vision_model": selection.vision_model,
                "photo_mode": selection.photo_mode,
                "topology": topology,
                "cluster_consent": cluster_consent,
                "origin": "api",
                "runtime_key": f"vllm+{settings.ai_default_base_url}",
                "initiator_household_id": session.household_id,
                "initiator_user_id": session.user_id,
            }
        )
    except (ManagerProtocolError, ManagerTransportError, ManagerJobError) as exc:
        return _manager_failure(exc)

    # After durable acceptance: one idempotent operational audit. NO household
    # settings change here — the reconciler commits them exactly once after
    # verified readiness (ADR 0081 S3), so a failed candidate never repoints.
    _write_apply_audit_once(
        engine,
        session.household_id,
        session.user_id,
        "ai_runtime.model_apply_started",
        str(status.job_id),
        f"Model apply accepted: job={status.job_id} main={selection.main_model} "
        f"vision={selection.vision_model or 'none'}",
    )
    logger.info(
        "model apply accepted household_id=%s job=%s main=%s vision=%s",
        session.household_id,
        status.job_id,
        selection.main_model,
        selection.vision_model,
    )
    return _projected_status(status)


@router.get(
    "/ai/runtime/apply/status",
    operation_id="getAiApplyStatus",
    response_model=AiApplyJobStatus,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        404: {"description": "Unknown retained job id", "model": ErrorResponse},
    },
    summary="Report apply-job status (read-only)",
)
def get_ai_apply_status(
    job_id: uuid.UUID | None = None,
    session: repository.SessionContext = Depends(get_current_session),
    settings: Settings = Depends(get_app_settings),
) -> AiApplyJobStatus | JSONResponse:
    """Read-only: never commits settings, launches recovery, acknowledges
    jobs, or creates rows. With `job_id` it reports that exact job; without
    it the manager's current/latest job, else an explicit idle."""
    if not settings.model_manager_url:
        return _no_job_status("unavailable", message="Model manager is not configured")
    client = _manager_client(settings)
    try:
        client.verify_protocol()
        if job_id is not None:
            status = client.job(str(job_id))
        else:
            status = client.current_job()
    except ManagerJobError as exc:
        if exc.status_code == 404:
            return _apply_error(404, "unknown_job", "No retained apply job with that id")
        return _manager_failure(exc)
    except (ManagerProtocolError, ManagerTransportError) as exc:
        # Reads degrade loudly instead of erroring the poll loop (spec 04).
        code = "manager_upgrade_required" if isinstance(exc, ManagerProtocolError) else None
        return _no_job_status("unavailable", reason_code=code, message=str(exc))
    if status is None:
        return _no_job_status("idle")
    return _projected_status(status)


@router.post(
    "/ai/runtime/apply/{job_id}/retry-recovery",
    operation_id="retryAiApplyRecovery",
    response_model=AiApplyJobStatus,
    status_code=202,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Unknown retained job id", "model": ErrorResponse},
        409: {"description": "The job is not in a recoverable state", "model": ErrorResponse},
        503: {"description": "Model manager unavailable or protocol skew", "model": ErrorResponse},
    },
    summary="Re-run the frozen direction of a durable apply decision",
)
def retry_ai_apply_recovery(
    job_id: uuid.UUID,
    payload: AiRetryRecoveryRequest,
    session: repository.SessionContext = Depends(require_right(rights.AI_RUNTIME_MANAGE)),
    settings: Settings = Depends(get_app_settings),
) -> AiApplyJobStatus | JSONResponse:
    """Direction-aware recovery (ADR 0081): `restore` re-runs the frozen
    restore plan for `recovery_failed`; `commit` re-runs post-commit
    forward repair only for `commit_repair_required`. Never a new model,
    never an image upgrade. The new `request_id` identifies the attempt."""
    if not settings.model_manager_url:
        return _apply_error(
            503,
            "manager_unavailable",
            "Model manager is not available; use scripts/swap-model.sh on the server",
        )
    if not settings.model_manager_credential.strip():
        return _apply_error(
            503,
            "manager_unavailable",
            "The model-manager deployment credential is not configured",
        )
    client = _manager_client(settings)
    try:
        client.verify_protocol()
        status = client.recover(str(job_id))
    except (ManagerProtocolError, ManagerTransportError, ManagerJobError) as exc:
        return _manager_failure(exc)
    logger.info(
        "apply recovery re-run job=%s request_id=%s by household=%s",
        job_id,
        payload.request_id,
        session.household_id,
    )
    return _projected_status(status)


@router.get(
    "/ai/usage",
    operation_id="getAiUsage",
    response_model=AiUsageResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Per-household advisor and storage usage (operator fairness view)",
)
async def get_ai_usage(
    session: repository.SessionContext = Depends(require_right(rights.AI_RUNTIME_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> AiUsageResponse:
    usage = repository.advisor_usage_by_household(engine)
    paths = repository.storage_paths_by_household(engine)
    missing_keys = set(household_crypto.households_missing_member_wraps(engine))
    households = []
    for household_id in repository.list_households(engine):
        household = repository.get_household(engine, household_id)
        if household is None:
            continue
        storage = 0
        for rel in paths.get(household_id, []):
            base = (
                os.path.join(settings.import_staging_dir, "documents")
                if not rel.startswith("attachments")
                else settings.import_staging_dir
            )
            try:
                storage += os.path.getsize(os.path.join(base, rel))
            except OSError:
                continue
        entry = usage.get(household_id, {})
        households.append(
            HouseholdUsage(
                household_id=household_id,
                name=household.display_name,
                chats_24h=entry.get("chats_24h", 0),
                chats_7d=entry.get("chats_7d", 0),
                median_answer_ms=entry.get("median_answer_ms"),
                storage_bytes=storage,
                member_key_ok=household_id not in missing_keys,
            )
        )
    households.sort(key=lambda h: h.chats_7d, reverse=True)
    return AiUsageResponse(households=households, chat_hourly_limit=settings.chat_hourly_limit)
