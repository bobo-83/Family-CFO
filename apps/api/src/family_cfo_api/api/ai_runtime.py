import logging
from dataclasses import asdict
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.ai_catalog import MODEL_CATALOG, hardware_profile
from family_cfo_api.ai_runtime_selection import resolve_ai_config
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    AiHardwareProfile,
    AiModelCatalog,
    AiModelInfo,
    AiRuntimeConfig,
    AiRuntimeStatus,
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
    detail = (
        f"AI model '{served_model or config.model}' is loaded and answering."
        if ready
        else "AI model is starting up (still loading); answers are deterministic until it is ready."
    )

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
    record = repository.upsert_ai_runtime_config(
        engine,
        household_id=session.household_id,
        provider=payload.provider,
        base_url=payload.base_url,
        model=payload.model,
        enabled=payload.enabled,
    )
    return _to_schema(record)
