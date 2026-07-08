from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import AiRuntimeConfig, ErrorResponse

router = APIRouter(tags=["AI Runtime"])

# Returned when a household has never configured a runtime. Disabled by
# default so no household starts sending financial context to any runtime
# without an explicit opt-in (local AI first, per README architectural
# principles).
_DEFAULT_CONFIG = AiRuntimeConfig(
    provider="vllm", base_url="http://vllm:8000", model="", enabled=False
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
) -> AiRuntimeConfig:
    record = repository.get_ai_runtime_config(engine, session.household_id)
    return _to_schema(record) if record is not None else _DEFAULT_CONFIG


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
) -> AiRuntimeConfig:
    record = repository.upsert_ai_runtime_config(
        engine,
        household_id=session.household_id,
        provider=payload.provider,
        base_url=payload.base_url,
        model=payload.model,
        enabled=payload.enabled,
    )
    return _to_schema(record)
