from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_current_session, get_engine, require_role
from family_cfo_api.schemas import AiRuntimeConfig, ErrorResponse

router = APIRouter(tags=["AI Runtime"])


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
