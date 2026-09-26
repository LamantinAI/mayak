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


class _CORSWrappedFastAPI(FastAPI):
    # ServerErrorMiddleware wraps every user middleware, CORS included, so a 500 it builds
    # never passes back through CORS. Wrapping the whole built stack puts CORS outside it.
    def __init__(self, *, origins: list[str], allow_credentials: bool, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cors_origins = origins
        self._cors_credentials = allow_credentials

    def build_middleware_stack(self) -> Any:
        return CORSMiddleware(
            super().build_middleware_stack(),
            allow_origins=self._cors_origins,
            allow_credentials=self._cors_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )


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
                        min_size=min(4, settings.postgres.pool_size),
                        max_size=settings.postgres.pool_size,
                        check=AsyncConnectionPool.check_connection,
                        kwargs={
                            # autocommit keeps single statements independent; multi-statement writes must use
                            # connection.transaction() to avoid partial aggregate/outbox writes (ADR-007).
                            "autocommit": True,
                            "connect_timeout": 5,
                            "prepare_threshold": None,
                            # Dead TCP sessions otherwise hold pool slots for minutes.
                            "keepalives": 1,
                            "keepalives_idle": 5,
                            "keepalives_interval": 2,
                            "keepalives_count": 3,
                            "tcp_user_timeout": 10_000,
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

                # Use APP_NAME so extracted projects do not keep the template title.
                app = _CORSWrappedFastAPI(
                    origins=settings.server.cors_origins,
                    allow_credentials=settings.server.cors_allow_credentials,
                    title=f"{settings.project.name} API",
                    description="FastAPI application with semantic logging and hexagonal architecture",
                    version=APP_VERSION,
                    # Starlette debug publishes traceback details on 500; APP_DEBUG must affect
                    # logging only. Guarded by tests/application/test_secret_leak_guards.py.
                    debug=False,
                    lifespan=lifespan,
                )

                # Set up middleware.
                app.add_middleware(
                    AILoggingMiddleware, max_body_bytes=settings.server.max_body_bytes
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
                        # Report the configured product title, not the template title.
                        "title": app.title,
                        "version": APP_VERSION,
                        "debug": settings.project.debug,
                        # Make debug-relaxed guards visible in the startup log.
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
