# FILE: project/core/lifecycle.py
# SUMMARY: Lifecycle manager for async resources initialization and shutdown.

import asyncio
import time
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import AsyncGenerator, Callable

from fastapi import FastAPI

from project.core.config import Settings
from project.core.logging import SemanticLogger


def create_lifespan(
    settings: Settings, logger: SemanticLogger
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    _ = settings  # reserved for future per-vertical hooks

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        from project.core.composition_root import cleanup_services

        # --- STARTUP LOGIC ---
        logger.log_system_event(event_name="application_startup_initiated", category="lifecycle")

        # Record application start time for uptime tracking.
        app.state.start_time = time.monotonic()

        services = app.state.services

        try:
            try:
                # Open database connection pool.
                if "db_pool" in services and services["db_pool"]:
                    with logger.span("open_db_pool") as cp_span:
                        await asyncio.wait_for(services["db_pool"].open(), timeout=30)
                        cp_span.output = {"status": "opened"}
            except Exception as e:
                logger.log_error(
                    error_type="application_startup_failed",
                    message="Application startup failed during lifespan initialization",
                    exception=e,
                )
                raise

            # Yield control back to FastAPI.
            yield
        finally:
            # A cancelled startup raises CancelledError, a BaseException `except Exception`
            # above never sees, and must still close the pool.
            logger.log_system_event(
                event_name="application_shutdown_initiated", category="lifecycle"
            )
            await cleanup_services(services)
            logger.log_system_event(
                event_name="application_shutdown_completed", category="lifecycle"
            )

    return lifespan
