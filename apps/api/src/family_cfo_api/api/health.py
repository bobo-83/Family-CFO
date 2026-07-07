from fastapi import APIRouter
from sqlalchemy.exc import SQLAlchemyError

from family_cfo_api.config import Settings, get_settings
from family_cfo_api.db import check_database_connection, create_database_engine
from family_cfo_api.schemas import ErrorResponse, HealthResponse

router = APIRouter(tags=["Health"])


def build_health_response(settings: Settings) -> HealthResponse:
    status = "ok"

    if settings.health_check_database:
        try:
            engine = create_database_engine(settings.database_url)
            check_database_connection(engine)
        except SQLAlchemyError:
            status = "degraded"

    return HealthResponse(status=status, version=settings.version)


@router.get(
    "/health",
    operation_id="getHealth",
    response_model=HealthResponse,
    responses={
        500: {
            "description": "Structured error response",
            "model": ErrorResponse,
        },
    },
    summary="Check API health",
)
async def get_health() -> HealthResponse:
    return build_health_response(get_settings())
