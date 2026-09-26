# FILE: project/infrastructure/api/endpoints/health.py
# SUMMARY: Health check endpoint for monitoring application status.

import asyncio
import time
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Any, Literal, cast

from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request, Response, status
from psycopg_pool import AsyncConnectionPool

from project.application.dtos import (
    DetailedHealthResponse,
    ErrorResponse,
    HealthResponse,
)
from project.core.config import APP_VERSION
from project.core.logging import get_logger

logger = get_logger(__name__)

_DATABASE_HEALTH_FAILURE_MESSAGE = "Database connection failed"
_DATABASE_NOT_MIGRATED_MESSAGE = "Database reachable but not migrated — run `make migrate`"
_DATABASE_BEHIND_MESSAGE = "Database schema is behind this code's migrations — run `make migrate`"
_DATABASE_TIMEOUT_MESSAGE = "Database check timed out"
_MIGRATIONS_UNREADABLE_MESSAGE = "This code's migrations could not be read"
_DATABASE_REVISION_MALFORMED_MESSAGE = "Database records a malformed migration revision"

# Only named safe DB failures are exposed; raw errors may contain credentials.
_SAFE_DATABASE_MESSAGES = frozenset(
    {
        _DATABASE_NOT_MIGRATED_MESSAGE,
        _DATABASE_BEHIND_MESSAGE,
        _DATABASE_TIMEOUT_MESSAGE,
        _MIGRATIONS_UNREADABLE_MESSAGE,
        _DATABASE_REVISION_MALFORMED_MESSAGE,
    }
)

# alembic/ beside the package: where a checkout has it and where the image copies it (Dockerfile).
_MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "alembic"

# Wall-clock budget for the readiness DB probe; bounds /health/ready latency
# so a half-open TCP connection cannot stall the probe past k8s timeoutSeconds.
READINESS_DB_TIMEOUT_SECONDS = 2.0

# psycopg cancellation can itself hang on a dead server; cap probes to one per pool. A stuck
# probe ends when the DB answers again or the pool's keepalive/tcp_user_timeout (composition_root)
# drops the socket — until then, staying unhealthy is correct.
_active_db_probes: dict[int, asyncio.Task[tuple[float, frozenset[str] | None]]] = {}

_LLM_HEALTH_UNAVAILABLE_MESSAGE = "LLM service is unavailable"

_LLM_HEALTH_PROBE_FAILURE_MESSAGE = "LLM provider probe failed"

# Create router for health endpoints
health_router = APIRouter(prefix="/health", tags=["health"])


def _get_uptime_seconds(request: Request) -> float:
    # Compute elapsed time since application start.
    start_time = getattr(request.app.state, "start_time", None)
    if not isinstance(start_time, float):
        return 0.0
    return time.monotonic() - start_time


# Cache migration metadata: checking files on every readiness call is expensive.
@cache
def _migration_revisions() -> tuple[frozenset[str], frozenset[str]]:
    script = ScriptDirectory(str(_MIGRATIONS_DIR))
    known = frozenset(revision.revision for revision in script.walk_revisions())
    return frozenset(script.get_heads()), known


# Read alembic_version, including its empty-table state; SELECT 1 misses unmigrated DBs. Separated
# from _check_database so a probe left running past its timeout can be tracked and cancelled by
# _active_db_probes instead of merely abandoned.
async def _run_db_probe(pool: AsyncConnectionPool[Any]) -> tuple[float, frozenset[str] | None]:
    start_ns = time.perf_counter_ns()
    revisions: frozenset[str] | None = None
    async with pool.connection() as conn:
        # to_regclass follows search_path, including non-public Alembic schemas.
        cursor = await conn.execute("SELECT to_regclass('alembic_version') IS NOT NULL")
        row = await cursor.fetchone()
        if row and row[0]:
            cursor = await conn.execute("SELECT version_num FROM alembic_version")
            revisions = frozenset(str(found[0]) for found in await cursor.fetchall())
    return (time.perf_counter_ns() - start_ns) / 1e6, revisions


async def _check_database(pool: AsyncConnectionPool[Any]) -> dict[str, Any]:
    try:
        expected, known = _migration_revisions()
    except Exception as e:
        logger.log_warning(
            warning_type="database_health_check_failed",
            message=f"Reading the migrations in {_MIGRATIONS_DIR} failed: {e}",
            affected_component="database",
        )
        return {
            "status": "unhealthy",
            "message": _MIGRATIONS_UNREADABLE_MESSAGE,
            "response_time_ms": None,
        }

    # asyncio.wait_for cancels the awaiting coroutine on timeout, but a cancelled psycopg
    # call can itself hang on a dead server — the cancellation is awaited, and that await
    # never returns. Tracking the task in _active_db_probes and using asyncio.wait instead
    # lets this function return on the timeout without ever awaiting that cancellation.
    key = id(pool)
    previous = _active_db_probes.get(key)
    if previous is not None and not previous.done():
        return {
            "status": "unhealthy",
            "message": _DATABASE_TIMEOUT_MESSAGE,
            "timeout_seconds": READINESS_DB_TIMEOUT_SECONDS,
            "response_time_ms": None,
        }
    probe = asyncio.create_task(_run_db_probe(pool))
    _active_db_probes[key] = probe

    def settled(task: asyncio.Task[tuple[float, frozenset[str] | None]]) -> None:
        if _active_db_probes.get(key) is task:
            _active_db_probes.pop(key, None)
        if not task.cancelled():
            task.exception()

    probe.add_done_callback(settled)
    try:
        done, _ = await asyncio.wait({probe}, timeout=READINESS_DB_TIMEOUT_SECONDS)
        if not done:
            probe.cancel()
            raise asyncio.TimeoutError
        response_time_ms, revisions = probe.result()
        latency = round(response_time_ms, 2)

        # A reachable but un-migrated database is not ready — name it instead of
        # reporting healthy and failing later on the first real query.
        if not revisions:
            return {
                "status": "unhealthy",
                "message": _DATABASE_NOT_MIGRATED_MESSAGE,
                "response_time_ms": latency,
            }
        # Blank or padded revisions are corrupt, not unknown newer revisions.
        if any(not revision or revision != revision.strip() for revision in revisions):
            return {
                "status": "unhealthy",
                "message": _DATABASE_REVISION_MALFORMED_MESSAGE,
                "response_time_ms": latency,
                "schema_revision": sorted(revisions),
            }
        if revisions != expected and revisions <= known:
            return {
                "status": "unhealthy",
                "message": _DATABASE_BEHIND_MESSAGE,
                "response_time_ms": latency,
                "schema_revision": sorted(revisions),
                "expected_revision": sorted(expected),
            }

        result: dict[str, Any] = {
            "status": "healthy",
            "message": "Database connection successful",
            "response_time_ms": latency,
        }
        # An old replica stays ready against a newer schema during a rolling deploy.
        if not revisions <= known:
            result["message"] = "Database connection successful; its schema is newer than this code"
            result["schema_revision"] = sorted(revisions)
            result["expected_revision"] = sorted(expected)
        return result
    except asyncio.TimeoutError:
        # Treat timeout as unhealthy with an actionable diagnostic message.
        logger.log_warning(
            warning_type="database_health_check_timeout",
            message=(f"Database health check timed out after {READINESS_DB_TIMEOUT_SECONDS}s"),
            affected_component="database",
        )
        return {
            "status": "unhealthy",
            "message": _DATABASE_TIMEOUT_MESSAGE,
            "timeout_seconds": READINESS_DB_TIMEOUT_SECONDS,
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
    finally:
        if not probe.done():
            probe.cancel()


async def _check_llm_service(request: Request) -> dict[str, Any]:
    # Retrieve the LLM service from app.state and validate access.
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


# Only critical checks decide the verdict; every check is still reported (ADR-008).
async def _build_readiness_response(
    request: Request,
) -> tuple[Literal["healthy", "unhealthy"], dict[str, dict[str, Any]]]:
    # Retrieve configured services and fail fast when DI is unavailable.
    services = getattr(request.app.state, "services", None)
    if services is None:
        return (
            "unhealthy",
            {
                "services": {
                    "status": "unhealthy",
                    "message": "Application services are not initialized",
                    "critical": True,
                }
            },
        )

    checks: dict[str, dict[str, Any]] = {
        "services": {
            "status": "healthy",
            "message": "Application services are initialized",
            "critical": True,
        }
    }

    # One defensive getattr chain: an app with no settings object degrades to each check's own
    # default instead of raising — ADR-006 for the database, ADR-008 for the LLM check.
    settings = getattr(request.app.state, "settings", None)
    llm_critical = getattr(getattr(settings, "agent", None), "llm_readiness_critical", False)
    database_enabled = getattr(getattr(settings, "postgres", None), "enabled", True)

    llm_check = await _check_llm_service(request)
    # Default AGENT_LLM_READINESS_CRITICAL=false keeps a shared provider's bad minute from
    # evicting every replica at once (ADR-008); the check's real status is still reported either way.
    llm_check["critical"] = llm_critical
    checks["llm"] = llm_check

    # No pool by design (POSTGRES_ENABLED=false) is reported "disabled"; a missing pool while
    # the database IS enabled stays unhealthy — that is a real fault.
    db_pool = services.get("db_pool")
    if not database_enabled:
        checks["database"] = {
            "status": "disabled",
            "message": "Database not used by this project (POSTGRES_ENABLED=false)",
            "critical": False,
        }
    elif not isinstance(db_pool, AsyncConnectionPool):
        checks["database"] = {
            "status": "unhealthy",
            "message": "Database pool is not configured",
            "critical": True,
        }
    else:
        checks["database"] = await _check_database(db_pool)
        if (
            checks["database"].get("status") == "unhealthy"
            and checks["database"].get("message") not in _SAFE_DATABASE_MESSAGES
        ):
            checks["database"]["message"] = _DATABASE_HEALTH_FAILURE_MESSAGE
        checks["database"]["critical"] = True

    critical_statuses = {checks["services"]["status"]}
    if llm_critical:
        critical_statuses.add(checks["llm"]["status"])
    if database_enabled:
        critical_statuses.add(checks["database"]["status"])
    if "unhealthy" in critical_statuses:
        return "unhealthy", checks

    return "healthy", checks


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
    # Get application uptime and return health status.
    uptime = _get_uptime_seconds(request)
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(timezone.utc),
        version=APP_VERSION,
        uptime_seconds=uptime,
    )


# Readiness check endpoint validating the three dependencies the kernel has: service wiring, the LLM, and the database.
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
    # Aggregate readiness checks and map unhealthy state to HTTP 503.
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
