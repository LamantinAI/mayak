# FILE: project/core/lifecycle.py
# SUMMARY: Lifecycle manager for async resources initialization and shutdown.

import asyncio
import time
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import AsyncGenerator, Callable

from fastapi import FastAPI

from project.core.config import Settings
from project.core.logging import SemanticLogger


# FUNCTION: create_lifespan
# SUMMARY: Create FastAPI lifespan context manager for async resource management.
def create_lifespan(
    settings: Settings, logger: SemanticLogger
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    _ = settings  # reserved for future per-vertical hooks

    # FUNCTION: lifespan
    # SUMMARY: Manage async resource lifecycle (startup/shutdown) for FastAPI application.
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        from project.core.composition_root import cleanup_services

        # --- STARTUP LOGIC ---
        logger.log_system_event(event_name="application_startup_initiated", category="lifecycle")

        # **LOGIC_STEP**: Record application start time for uptime tracking.
        app.state.start_time = time.monotonic()

        services = app.state.services

        try:
            # **LOGIC_STEP**: Open database connection pool.
            if "db_pool" in services and services["db_pool"]:
                with logger.span("open_db_pool") as cp_span:
                    await asyncio.wait_for(services["db_pool"].open(), timeout=30)
                    cp_span.output = {"status": "opened"}

        except Exception as e:
            # **LOGIC_STEP**: Ensure partially initialized services are cleaned up before failing startup.
            logger.log_error(
                error_type="application_startup_failed",
                message="Application startup failed during lifespan initialization",
                exception=e,
            )
            await cleanup_services(services)
            raise

        # **LOGIC_STEP**: Yield control back to FastAPI.
        yield

        # --- SHUTDOWN LOGIC ---
        logger.log_system_event(event_name="application_shutdown_initiated", category="lifecycle")

        await cleanup_services(services)
        logger.log_system_event(event_name="application_shutdown_completed", category="lifecycle")

    return lifespan
