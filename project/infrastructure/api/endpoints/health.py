# FILE: project/infrastructure/api/endpoints/health.py
# SUMMARY: Health check endpoint for monitoring application status.

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Literal, cast

from fastapi import APIRouter, Request, Response, status
from psycopg_pool import AsyncConnectionPool

from project.application.dtos import (
    DetailedHealthResponse,
    ErrorResponse,
    HealthResponse,
)
from project.core.config import APP_VERSION
from project.core.logging import get_logger

# ATTRIBUTE: logger (SemanticLogger)
# SUMMARY: Semantic logger instance for health endpoint diagnostics.
logger = get_logger(__name__)

# ATTRIBUTE: _DATABASE_HEALTH_FAILURE_MESSAGE (str)
# SUMMARY: Stable client-facing message used when the database readiness check fails.
_DATABASE_HEALTH_FAILURE_MESSAGE = "Database connection failed"

# ATTRIBUTE: READINESS_DB_TIMEOUT_SECONDS (float)
# SUMMARY: Wall-clock budget for the readiness DB probe; bounds /health/ready latency
# so a half-open TCP connection cannot stall the probe past k8s timeoutSeconds.
READINESS_DB_TIMEOUT_SECONDS = 2.0

# ATTRIBUTE: _LLM_HEALTH_UNAVAILABLE_MESSAGE (str)
# SUMMARY: Stable client-facing message used when the LLM service is not initialized.
_LLM_HEALTH_UNAVAILABLE_MESSAGE = "LLM service is unavailable"

# ATTRIBUTE: _LLM_HEALTH_PROBE_FAILURE_MESSAGE (str)
# SUMMARY: Stable client-facing message used when the LLM provider probe fails.
_LLM_HEALTH_PROBE_FAILURE_MESSAGE = "LLM provider probe failed"

# ATTRIBUTE: health_router (APIRouter)
# SUMMARY: Router exposing liveness and readiness endpoints.
# Create router for health endpoints
health_router = APIRouter(prefix="/health", tags=["health"])


# FUNCTION: _get_uptime_seconds
# SUMMARY: Calculate application uptime from start_time stored in app.state.
def _get_uptime_seconds(request: Request) -> float:
    # **LOGIC_STEP**: Compute elapsed time since application start.
    start_time = getattr(request.app.state, "start_time", None)
    if not isinstance(start_time, float):
        return 0.0
    return time.monotonic() - start_time


# FUNCTION: _run_db_probe
# SUMMARY: Inner DB readiness probe that reaches the database and confirms migrations have run. Separated from _check_database so the surrounding asyncio.wait_for can cancel it on timeout.
# OUTPUT: (tuple[float, bool]): Probe latency in milliseconds, and whether the schema is migrated.
# NOTE: A bare SELECT 1 answered "healthy" against a database with no tables at all. That is the
# exact state you land in after `make run-local` on a fresh checkout, so readiness lied at the one
# moment it mattered. Reading alembic_version costs a second round trip and turns the silent case
# into a named one.
async def _run_db_probe(pool: AsyncConnectionPool[Any]) -> tuple[float, bool]:
    start_ns = time.perf_counter_ns()
    async with pool.connection() as conn:
        await conn.execute("SELECT 1")
        # **LOGIC_STEP**: Unqualified on purpose — to_regclass resolves through search_path, so a
        # project that puts alembic's version table in its own schema is still recognised. Hardcoding
        # `public.` reported "not migrated" forever on a fully migrated database.
        cursor = await conn.execute("SELECT to_regclass('alembic_version') IS NOT NULL")
        row = await cursor.fetchone()
    migrated = bool(row[0]) if row else False
    return (time.perf_counter_ns() - start_ns) / 1e6, migrated


# FUNCTION: _check_database
# SUMMARY: Perform a real database health check using the checkpoint connection pool, bounded by READINESS_DB_TIMEOUT_SECONDS so a half-open TCP cannot stall the readiness probe.
async def _check_database(pool: AsyncConnectionPool[Any]) -> dict[str, Any]:
    # **LOGIC_STEP**: Execute the probe under an asyncio.wait_for budget to bound probe latency.
    try:
        response_time_ms, migrated = await asyncio.wait_for(
            _run_db_probe(pool),
            timeout=READINESS_DB_TIMEOUT_SECONDS,
        )

        # **LOGIC_STEP**: A reachable but un-migrated database is not ready — name it instead of
        # reporting healthy and failing later on the first real query.
        if not migrated:
            return {
                "status": "unhealthy",
                "message": "Database reachable but not migrated — run `make migrate`",
                "response_time_ms": round(response_time_ms, 2),
            }

        return {
            "status": "healthy",
            "message": "Database connection successful",
            "response_time_ms": round(response_time_ms, 2),
        }
    except asyncio.TimeoutError:
        # **LOGIC_STEP**: Treat timeout as unhealthy with an actionable diagnostic message.
        logger.log_warning(
            warning_type="database_health_check_timeout",
            message=(f"Database health check timed out after {READINESS_DB_TIMEOUT_SECONDS}s"),
            affected_component="database",
        )
        return {
            "status": "unhealthy",
            "message": (f"Database check timed out after {READINESS_DB_TIMEOUT_SECONDS}s"),
            "response_time_ms": None,
        }
    except Exception as e:
        logger.log_warning(
            warning_type="database_health_check_failed",
            message=f"Database health check failed: {e}",
            affected_component="database",
        )
        return {
            "status": "unhealthy",
            "message": _DATABASE_HEALTH_FAILURE_MESSAGE,
            "response_time_ms": None,
        }


# FUNCTION: _check_llm_service
# SUMMARY: Validate the LLM service according to configured readiness mode.
async def _check_llm_service(request: Request) -> dict[str, Any]:
    # **LOGIC_STEP**: Retrieve the LLM service from app.state and validate access.
    services = getattr(request.app.state, "services", {})
    llm_service = services.get("llm_service")
    if llm_service is None:
        return {
            "status": "unhealthy",
            "message": _LLM_HEALTH_UNAVAILABLE_MESSAGE,
            "provider_reachable": False,
        }

    result = cast(dict[str, Any], await llm_service.check_readiness())
    if result.get("status") != "unhealthy":
        return result

    sanitized = dict(result)
    if result.get("mode") == "probe":
        sanitized["message"] = _LLM_HEALTH_PROBE_FAILURE_MESSAGE
    else:
        sanitized["message"] = _LLM_HEALTH_UNAVAILABLE_MESSAGE
    return sanitized


# FUNCTION: _build_readiness_response
# SUMMARY: Build aggregated readiness status from critical and optional dependency checks.
async def _build_readiness_response(
    request: Request,
) -> tuple[Literal["healthy", "unhealthy"], dict[str, dict[str, Any]]]:
    # **LOGIC_STEP**: Retrieve configured services and fail fast when DI is unavailable.
    services = getattr(request.app.state, "services", None)
    if services is None:
        return (
            "unhealthy",
            {
                "services": {
                    "status": "unhealthy",
                    "message": "Application services are not initialized",
                }
            },
        )

    checks: dict[str, dict[str, Any]] = {
        "services": {
            "status": "healthy",
            "message": "Application services are initialized",
        }
    }

    llm_check = await _check_llm_service(request)
    checks["llm"] = llm_check

    # **LOGIC_STEP**: A project that declared POSTGRES_ENABLED=false has no pool by design.
    # Reporting that as unhealthy would make readiness permanently false for a service that is
    # working exactly as configured, so the check reports "disabled" and leaves the critical
    # set. A missing pool while the database IS enabled stays unhealthy — that is a real fault.
    settings = getattr(request.app.state, "settings", None)
    database_enabled = getattr(getattr(settings, "postgres", None), "enabled", True)

    db_pool = services.get("db_pool")
    if not database_enabled:
        checks["database"] = {
            "status": "disabled",
            "message": "Database not used by this project (POSTGRES_ENABLED=false)",
        }
    elif not isinstance(db_pool, AsyncConnectionPool):
        checks["database"] = {
            "status": "unhealthy",
            "message": "Database pool is not configured",
        }
    else:
        checks["database"] = await _check_database(db_pool)
        if checks["database"].get("status") == "unhealthy":
            checks["database"]["message"] = _DATABASE_HEALTH_FAILURE_MESSAGE

    critical_statuses = {
        checks["services"]["status"],
        checks["llm"]["status"],
    }
    if database_enabled:
        critical_statuses.add(checks["database"]["status"])
    if "unhealthy" in critical_statuses:
        return "unhealthy", checks

    return "healthy", checks


# FUNCTION: health_check
# SUMMARY: Basic health check endpoint.
@health_router.get(
    "/",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Basic health check",
    description="Returns the basic health status of the application, including version and uptime.",
    responses={
        status.HTTP_200_OK: {
            "model": HealthResponse,
            "description": "Service is healthy",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": ErrorResponse,
            "description": "Service is unhealthy",
        },
    },
)
async def health_check(request: Request) -> HealthResponse:
    # **LOGIC_STEP**: Get application uptime and return health status.
    uptime = _get_uptime_seconds(request)
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(timezone.utc),
        version=APP_VERSION,
        uptime_seconds=uptime,
    )


# FUNCTION: readiness_check
# SUMMARY: Readiness check endpoint validating the three dependencies the kernel has: service wiring, the LLM, and the database.
# INPUT: response (Response): The outgoing HTTP response used to set readiness status.
@health_router.get(
    "/ready",
    response_model=DetailedHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Readiness check",
    description="Returns readiness state for critical and optional dependencies.",
    responses={
        status.HTTP_200_OK: {
            "model": DetailedHealthResponse,
            "description": "Service is ready",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": DetailedHealthResponse,
            "description": "Service is not ready",
        },
    },
)
async def readiness_check(request: Request, response: Response) -> DetailedHealthResponse:
    # **LOGIC_STEP**: Aggregate readiness checks and map unhealthy state to HTTP 503.
    overall_status, checks = await _build_readiness_response(request)
    if overall_status == "unhealthy":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return DetailedHealthResponse(
        status=overall_status,
        checks=checks,
        timestamp=datetime.now(timezone.utc),
        version=APP_VERSION,
        uptime_seconds=_get_uptime_seconds(request),
    )
