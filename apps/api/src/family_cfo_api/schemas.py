from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ApiError

