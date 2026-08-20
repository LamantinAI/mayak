# FILE: project/infrastructure/api/router_registration.py
# SUMMARY: Helpers that register API routers while keeping CompositionRoot focused on application orchestration.

from typing import Any, Dict

from fastapi import FastAPI

from project.infrastructure.api.endpoints.health import health_router
from project.infrastructure.api.endpoints.reference_tasks import reference_tasks_router


# FUNCTION: include_application_routers
# SUMMARY: Register all application routers on the FastAPI instance.
# INPUT: services (Dict[str, Any] | None): Service registry used to skip routers whose backing
#        service was not built.
def include_application_routers(app: FastAPI, services: Dict[str, Any] | None = None) -> None:
    # **LOGIC_STEP**: Register the kernel health router. Verticals add their own
    # routers here when they introduce new endpoints.
    app.include_router(health_router)

    # **LOGIC_STEP**: The reference vertical needs the relational store, so its routes exist only
    # when the store does. With POSTGRES_ENABLED=false the service is None and the paths are
    # simply absent from the application and from OpenAPI — which is honest, and keeps the
    # handlers free of "is my subsystem on?" branches. A vertical that would rather answer 503
    # registers unconditionally and declares a nullable dependency alias instead; see
    # project/infrastructure/api/dependencies.py.
    registry = services or {}
    if registry.get("reference_task_service") is not None:
        app.include_router(reference_tasks_router)
