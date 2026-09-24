# FILE: project/application/dtos.py
# SUMMARY: Contains Pydantic models for API input/output data transfer objects.

from datetime import datetime
from typing import Any, Dict, Literal

from pydantic import Field, field_serializer

from project.application.core_model import CoreModel


# SUMMARY: Standard error response model for API errors.
class ErrorDetail(CoreModel):
    # SUMMARY: Stable machine-readable error classification.
    type: str = Field(
        ...,
        title="Error Type",
        description="Stable machine-readable error type",
        examples=["ValidationError"],
    )

    # SUMMARY: Human-readable client-facing error message.
    message: str = Field(
        ...,
        title="Error Message",
        description="Human-readable error message",
        examples=["Request validation failed"],
    )

    # SUMMARY: HTTP status code associated with the error.
    status_code: int = Field(
        ...,
        title="Status Code",
        description="HTTP status code associated with the error",
        examples=[422],
    )

    # SUMMARY: Optional structured detail payload returned for validation-like errors.
    details: list[dict[str, Any]] | Dict[str, Any] | None = Field(
        default=None,
        title="Error Details",
        description="Optional structured error details",
    )


# SUMMARY: Standard error envelope for API errors.
class ErrorResponse(CoreModel):
    # SUMMARY: Structured error payload returned to the client.
    error: ErrorDetail = Field(
        ...,
        title="Error",
        description="Structured error payload",
    )


# SUMMARY: Response model for health check endpoint.
class HealthResponse(CoreModel):
    # SUMMARY: Aggregated health status of the service.
    status: Literal["healthy", "unhealthy"] = Field(
        ...,
        title="Status",
        description="The health status of the service",
        examples=["healthy"],
    )

    # SUMMARY: Application version reported by the health endpoint.
    version: str = Field(
        ...,
        title="Version",
        description="The application version",
        examples=["1.0.0"],
    )

    # SUMMARY: UTC timestamp when the health payload was generated.
    timestamp: datetime = Field(
        ...,
        title="Timestamp",
        description="Timestamp when the health check was performed",
        examples=["2024-01-01T12:00:00Z"],
    )

    # SUMMARY: Process uptime in seconds at the moment of the health check.
    uptime_seconds: float = Field(
        ...,
        title="Uptime",
        description="Application uptime in seconds",
        examples=[3600.5],
    )

    # SUMMARY: Serializes datetime to ISO format for JSON response.
    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        return value.isoformat()


# SUMMARY: Detailed response model for health check with additional system information.
class DetailedHealthResponse(HealthResponse):
    # SUMMARY: Per-component readiness details for service wiring, the LLM, and the database.
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
