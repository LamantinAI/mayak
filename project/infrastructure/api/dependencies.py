# FILE: project/infrastructure/api/dependencies.py
# SUMMARY: FastAPI dependency injection helpers for accessing application services from app.state.

from typing import Annotated, TypeVar

from fastapi import Depends, Request

from project.application.reference_task_service import ReferenceTaskService
from project.core.logging import get_logger
from project.domain.exceptions import ProjectError

# ATTRIBUTE: logger (SemanticLogger)
# SUMMARY: Semantic logger used for dependency resolution failures.
logger = get_logger(__name__)

T = TypeVar("T")


# FUNCTION: _get_service
# SUMMARY: Generic helper to retrieve a typed service from app.state.services by key.
# INPUT: request (Request): Incoming FastAPI request whose app.state holds services.
# INPUT: service_type (type[T]): Expected service class for type-safe retrieval.
# RAISES: ProjectError: When services are missing or of unexpected type.
def _get_service(request: Request, key: str, service_type: type[T]) -> T:
    services = getattr(request.app.state, "services", None)

    if services is None:
        logger.log_error(
            error_type="dependency_injection_failed",
            message="Services not found in app.state - composition root may not have been properly initialized",
        )
        raise ProjectError(
            "Application services not properly initialized. "
            "Ensure composition root has been executed before accessing dependencies."
        )

    service = services.get(key)

    if service is None:
        logger.log_error(
            error_type="dependency_injection_failed",
            message=f"{service_type.__name__} not found in app.state.services",
        )
        raise ProjectError(
            f"{service_type.__name__} not properly configured in application dependencies. "
            "Check composition root configuration."
        )

    if not isinstance(service, service_type):
        logger.log_error(
            error_type="dependency_injection_failed",
            message=f"{key} is not an instance of {service_type.__name__}",
        )
        raise ProjectError(
            f"{service_type.__name__} has invalid type in application dependencies. "
            "Check composition root configuration."
        )

    return service


# FUNCTION: get_reference_task_service
# SUMMARY: Resolve the reference vertical's application service from the registry.
# INPUT: request (Request): Incoming FastAPI request whose app.state holds the service registry.
# OUTPUT: (ReferenceTaskService): The service built by service_registration.build_reference_services.
# RAISES: ProjectError: When the service is absent from the registry.
def get_reference_task_service(request: Request) -> ReferenceTaskService:
    # **LOGIC_STEP**: The three-argument _get_service call is not a style choice.
    # ai_context/extraction.py::extract_dependency_registry recognises exactly this shape to link
    # alias -> getter -> service key, and validate_endpoint_wiring.py refuses any endpoint whose
    # alias chain does not resolve. Inlining the registry lookup here breaks both.
    return _get_service(request, "reference_task_service", ReferenceTaskService)


# ATTRIBUTE: ReferenceTaskServiceDep (Annotated[ReferenceTaskService, Depends])
# SUMMARY: Canonical typed alias for the reference vertical. Copy this two-part shape — getter
# plus Annotated alias — for every new vertical; endpoints must depend on the alias and never on
# Depends(get_...) written inline, which validate_endpoint_wiring.py rejects as
# endpoint.no_depends_without_alias.
#
# This alias is non-nullable even though the service is None when POSTGRES_ENABLED=false. That is
# deliberate: router_registration.py leaves the routes unregistered in that mode, so a request can
# never reach a handler holding a missing service, and the handler bodies stay free of None
# checks. A vertical that would rather keep its routes visible and answer 503 declares the getter
# as `-> MyService | None` instead; extraction supports that shape too.
ReferenceTaskServiceDep = Annotated[ReferenceTaskService, Depends(get_reference_task_service)]
