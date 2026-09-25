# FILE: project/core/composition_root.py
# SUMMARY: Composition Root that creates and links all dependencies, creating adapters and injecting them into Core services for FastAPI application.

from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from psycopg_pool import AsyncConnectionPool

from project.core.config import APP_VERSION, GUARDS_RELAXED_BY_DEBUG, Settings, get_settings
from project.core.logging import get_logger
from project.core.service_registration import build_reference_services
from project.domain.exceptions import ProjectError
from project.infrastructure.agents.llm_service import LLMService
from project.infrastructure.api.exception_handlers import setup_exception_handlers
from project.infrastructure.api.middleware import AILoggingMiddleware
from project.infrastructure.api.router_registration import include_application_routers


class CompositionRoot:
    def __init__(self) -> None:
        # Initialize logger for dependency injection tracking.
        self._logger = get_logger(__name__)
        self._settings: Settings | None = None

    def build_dependencies(self) -> Dict[str, Any]:
        with self._logger.span("build_dependencies") as span_ctx:
            try:
                # Load and validate configuration.
                settings = get_settings()
                settings.validate_runtime()
                self._settings = settings

                self._logger.log_state_snapshot(
                    entity="app_config",
                    snapshot={
                        "model": settings.llm.model,
                        "base_url": settings.llm.base_url,
                        "db_host": settings.postgres.host,
                        "server_host": settings.server.host,
                        "server_port": settings.server.port,
                        "cors_origins": settings.server.cors_origins,
                    },
                    capture_reason="dependency_building",
                )

                # Initialize PostgreSQL connection pool (singleton), unless this
                # project declared it needs no relational store (POSTGRES_ENABLED=false).
                db_pool: AsyncConnectionPool | None = None
                if settings.postgres.enabled:
                    db_pool = AsyncConnectionPool(
                        str(settings.postgres.database_url),
                        open=False,
                        max_size=settings.postgres.pool_size,
                        kwargs={
                            # autocommit=True is deliberate, not a default left in
                            # place — a method with one execute() needs nothing further; a method
                            # that must land two or more statements together (aggregate + outbox
                            # row, order + line items) wraps them in
                            # "async with connection.transaction():" inside the connection block
                            # it already opens, or a crash between the two leaves a partial write
                            # nothing here catches. Full rationale, the code shape, and how to
                            # prove it with a functional test:
                            # docs/adr/ADR-007-autocommit-and-explicit-transactions.md.
                            "autocommit": True,
                            "connect_timeout": 5,
                            "prepare_threshold": None,
                        },
                    )

                # Initialize the shared LLMService used by application verticals.
                llm_service = LLMService()

                # Assemble the service registry.
                services: Dict[str, Any] = {
                    "db_pool": db_pool,
                    "llm_service": llm_service,
                }
                services.update(build_reference_services(settings, llm_service, db_pool))
                span_ctx.output = {
                    "service_count": len(services),
                    "services": list(services.keys()),
                }
                return services

            except Exception as e:
                # Handle and log any errors during dependency building.
                error_message = f"Failed to build dependencies: {e}"
                self._logger.log_error(
                    error_type="dependency_building_failed",
                    message=error_message,
                    exception=e,
                    exc_info=True,
                )
                raise ProjectError(error_message) from e

    def build_application(self, lifespan: Any = None) -> FastAPI:

        with self._logger.span("build_application") as span_ctx:
            try:
                # Build dependencies first.
                dependencies = self.build_dependencies()
                settings = self._settings
                if settings is None:
                    raise ProjectError("Settings not initialized before app creation")

                # Initialize FastAPI application. The title comes from APP_NAME
                # rather than a literal: the name was declared as a setting, documented in
                # .env.sample and never read, so every project built from the template shipped
                # OpenAPI titled after the template instead of after itself.
                app = FastAPI(
                    title=f"{settings.project.name} API",
                    description="FastAPI application with semantic logging and hexagonal architecture",
                    version=APP_VERSION,
                    # Never settings.project.debug. Starlette handles unhandled
                    # exceptions in ServerErrorMiddleware, which checks its own debug flag FIRST
                    # and, when set, renders the full traceback to the client — file paths, local
                    # variables, and whatever secret was in scope — while the application's
                    # registered Exception handler and its SAFE_INTERNAL_ERROR_MESSAGE are never
                    # reached. APP_DEBUG is documented as verbose logging and a single worker;
                    # turning it on must not silently also publish tracebacks over HTTP. The same
                    # trap made the whole test suite exercise a code path production never used
                    # until tests/conftest.py pinned debug off. Guarded by
                    # tests/application/test_secret_leak_guards.py.
                    debug=False,
                    lifespan=lifespan,
                )

                # Set up middleware.
                app.add_middleware(AILoggingMiddleware)

                # Add CORS middleware
                app.add_middleware(
                    CORSMiddleware,
                    allow_origins=settings.server.cors_origins,
                    # Read from settings rather than hardcoding True. Used to be a
                    # literal with no supported way to turn it off; project/core/config_runtime.py
                    # (Settings.validate_runtime) owns why this combined with a wildcard origin is
                    # the actual vulnerability and refuses that combination outside debug mode.
                    allow_credentials=settings.server.cors_allow_credentials,
                    allow_methods=["*"],
                    allow_headers=["*"],
                )

                # Set up exception handlers.
                setup_exception_handlers(app)

                # Include routers.
                include_application_routers(app, dependencies)

                # Store services in app.state for FastAPI dependency injection.
                self._logger.log_state_change(
                    entity="app.state",
                    changes={
                        "services": {
                            "old": None,
                            "new": "service registry built by CompositionRoot",
                        }
                    },
                    change_reason="fastapi_dependency_injection_setup",
                )

                app.state.services = dependencies
                app.state.settings = settings

                self._logger.log_state_snapshot(
                    entity="app.state.services",
                    snapshot={
                        "total_services": len(dependencies),
                    },
                    capture_reason="dependency_injection_verification",
                )

                # Log application startup event.
                self._logger.log_system_event(
                    event_name="fastapi_application_built",
                    category="application_lifecycle",
                    new_value={
                        # app.title, never a literal. The literal was the
                        # template's own name, so every project built from it logged a title it
                        # had already stopped using at the FastAPI() call above.
                        "title": app.title,
                        "version": APP_VERSION,
                        "debug": settings.project.debug,
                        # Which startup checks this build skipped, by the
                        # variable each protects, so a debug flag left on in a deployed
                        # container shows in the first lines of its log rather than in a README
                        # row nobody reads at 3 a.m. Empty whenever the guards ran.
                        "guards_relaxed_by_debug": (
                            list(GUARDS_RELAXED_BY_DEBUG) if settings.project.debug else []
                        ),
                        "host": settings.server.host,
                        "port": settings.server.port,
                    },
                )

                span_ctx.output = {
                    "version": APP_VERSION,
                    "service_count": len(dependencies),
                    "debug": app.debug,
                }
                return app

            except Exception as e:
                # Handle and log any errors during application building.
                error_message = f"Failed to build FastAPI application: {e}"
                self._logger.log_error(
                    error_type="application_building_failed",
                    message=error_message,
                    exception=e,
                    exc_info=True,
                )
                raise ProjectError(error_message) from e


async def cleanup_services(services: Dict[str, Any]) -> None:
    logger = get_logger(__name__)

    with logger.span(
        "cleanup_services",
        input_params={"service_count": len(services)},
    ) as span_ctx:
        errors: list[tuple[str, Exception]] = []

        # Close the shared database pool last.
        if "db_pool" in services and services["db_pool"]:
            try:
                await services["db_pool"].close()
            except Exception as e:
                errors.append(("db_pool", e))
                logger.log_error(
                    error_type="cleanup_pool_failed",
                    message="Failed to close database pool",
                    exception=e,
                )

        if errors:
            span_ctx.output = {
                "status": "partial_failure",
                "error_count": len(errors),
                "failed_steps": [name for name, _ in errors],
            }
        else:
            logger.log_system_event(event_name="services_cleaned_up", category="cleanup")
            span_ctx.output = {"status": "cleaned"}
