from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import uvicorn

from sqlalchemy.engine import Engine

from family_cfo_api.api.routes import api_router
from family_cfo_api.config import Settings, get_settings
from family_cfo_api.db import create_database_engine
from family_cfo_api.logging import configure_logging
from family_cfo_api.ratelimit import AuthRateLimiter
from family_cfo_api.schemas import ApiError, ErrorResponse


def error_response(
    code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return ErrorResponse(
        error=ApiError(
            code=code,
            message=message,
            details=details or {},
        )
    ).model_dump()


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    # Swagger UI and the OpenAPI schema are disabled in production to avoid
    # exposing the API surface (ADR 0010); they remain on in dev/test.
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        openapi_url="/api/v1/openapi.json" if settings.docs_enabled else None,
        docs_url="/api/v1/docs" if settings.docs_enabled else None,
        redoc_url=None,
    )
    app.state.db_engine = engine or create_database_engine(settings.database_url)
    app.state.settings = settings
    app.state.auth_rate_limiter = AuthRateLimiter(
        max_attempts=settings.auth_rate_limit_max_attempts,
        window_seconds=settings.auth_rate_limit_window_seconds,
        lockout_seconds=settings.auth_rate_limit_lockout_seconds,
        enabled=settings.auth_rate_limit_enabled,
    )
    app.include_router(api_router)

    @app.exception_handler(HTTPException)
    async def handle_http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "HTTP error"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response("http_error", message),
            # Preserve response headers set on the exception (e.g. Retry-After
            # on a 429 from the auth rate limiter).
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_exception(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_response(
                "validation_error", "Request validation failed", {"errors": exc.errors()}
            ),
        )

    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "family_cfo_api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
