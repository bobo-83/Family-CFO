"""The public error envelope, shared by the app factory and routes.

Routes that must emit a STABLE reason code (issue #170's apply contract puts
the reason in `error.code`) build the same shape the exception handlers in
`main.py` produce — without importing the app factory, which would be a
circular import (`main` imports the routers that would need this).
"""

from __future__ import annotations

from typing import Any

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
