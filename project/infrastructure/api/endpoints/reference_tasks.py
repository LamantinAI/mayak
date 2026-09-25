# FILE: project/infrastructure/api/endpoints/reference_tasks.py
# SUMMARY: Reference HTTP surface: each handler parses, delegates to the service, converts the result.
# No try/except: exception_handlers.py turns the service's domain errors into 404, 409 and 422 for
# every endpoint at once, and a local handler would build a second, divergent error envelope. The
# service arrives as the typed alias; validate_endpoint_wiring.py fails a handler that builds it.

from __future__ import annotations

from fastapi import APIRouter
from fastapi import status as http_status

from project.application.reference_task_dtos import (
    ReferenceTaskCreateRequest,
    ReferenceTaskListResponse,
    ReferenceTaskResponse,
    ReferenceTaskUpdateRequest,
)
from project.application.reference_task_service import UNCHANGED
from project.domain.reference_task import DEFAULT_STATUS
from project.infrastructure.api.dependencies import ReferenceTaskServiceDep

# Registered in router_registration.py only when the service exists — see the comment there.
reference_tasks_router = APIRouter(prefix="/reference-tasks", tags=["reference-tasks"])


@reference_tasks_router.post("", status_code=http_status.HTTP_201_CREATED)
async def create_reference_task(
    payload: ReferenceTaskCreateRequest, service: ReferenceTaskServiceDep
) -> ReferenceTaskResponse:
    task = await service.create_task(title=payload.title, details=payload.details)
    return ReferenceTaskResponse.from_domain(task)


@reference_tasks_router.get("/{task_id}")
async def get_reference_task(
    task_id: str, service: ReferenceTaskServiceDep
) -> ReferenceTaskResponse:
    return ReferenceTaskResponse.from_domain(await service.get_task(task_id))


@reference_tasks_router.patch("/{task_id}")
async def update_reference_task(
    task_id: str, payload: ReferenceTaskUpdateRequest, service: ReferenceTaskServiceDep
) -> ReferenceTaskResponse:
    # model_fields_set is what makes {"details": null} mean "clear it" and an absent details mean
    # "keep it": both arrive as None. What the caller did not send stays UNCHANGED.
    supplied = payload.model_fields_set
    task = await service.update_task(
        task_id,
        title=payload.title if "title" in supplied else UNCHANGED,
        details=payload.details if "details" in supplied else UNCHANGED,
        status=payload.status if "status" in supplied else UNCHANGED,
    )
    return ReferenceTaskResponse.from_domain(task)


@reference_tasks_router.get("")
async def list_reference_tasks(
    service: ReferenceTaskServiceDep, status: str = DEFAULT_STATUS, limit: int = 50
) -> ReferenceTaskListResponse:
    tasks = await service.list_tasks(status=status, limit=limit)
    return ReferenceTaskListResponse.from_domain(tasks)
