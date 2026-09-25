# FILE: project/application/dtos.py
# SUMMARY: Contains Pydantic models for API input/output data transfer objects.

from datetime import datetime
from typing import Any, Dict, Literal

from pydantic import Field, field_serializer

from project.application.core_model import CoreModel


class ErrorDetail(CoreModel):
    type: str = Field(
        ...,
        title="Error Type",
        description="Stable machine-readable error type",
        examples=["ValidationError"],
    )

    message: str = Field(
        ...,
        title="Error Message",
        description="Human-readable error message",
        examples=["Request validation failed"],
    )

    status_code: int = Field(
        ...,
        title="Status Code",
        description="HTTP status code associated with the error",
        examples=[422],
    )

    # Returned for validation-like errors.
    details: list[dict[str, Any]] | Dict[str, Any] | None = Field(
        default=None,
        title="Error Details",
        description="Optional structured error details",
    )


class ErrorResponse(CoreModel):
    error: ErrorDetail = Field(
        ...,
        title="Error",
        description="Structured error payload",
    )


class HealthResponse(CoreModel):
    status: Literal["healthy", "unhealthy"] = Field(
        ...,
        title="Status",
        description="The health status of the service",
        examples=["healthy"],
    )

    version: str = Field(
        ...,
        title="Version",
        description="The application version",
        examples=["1.0.0"],
    )

    # UTC.
    timestamp: datetime = Field(
        ...,
        title="Timestamp",
        description="Timestamp when the health check was performed",
        examples=["2024-01-01T12:00:00Z"],
    )

    uptime_seconds: float = Field(
        ...,
        title="Uptime",
        description="Application uptime in seconds",
        examples=[3600.5],
    )

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        return value.isoformat()


class DetailedHealthResponse(HealthResponse):
    checks: Dict[str, Dict[str, Any]] = Field(
        ...,
        title="Health Checks",
        description="Dictionary of health check results for various components",
        examples=[
            {
                "services": {
                    "status": "healthy",
                    "message": "Application services are initialized",
                },
                "llm": {"status": "healthy", "backend_mode": "mock"},
                "database": {"status": "healthy", "response_time_ms": 5},
            }
        ],
    )
