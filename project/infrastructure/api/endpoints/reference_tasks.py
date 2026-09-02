# FILE: project/infrastructure/api/endpoints/reference_tasks.py
# SUMMARY: Reference HTTP surface for the template's worked vertical. Every handler is three lines
# on purpose: parse, delegate, convert. Anything longer than that belongs in the application
# service, where it can be tested without a client.

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi import status as http_status

from project.application.dtos import ErrorResponse
from project.application.reference_task_service import UNCHANGED
from project.application.reference_task_dtos import (
    ReferenceTaskCreateRequest,
    ReferenceTaskListResponse,
    ReferenceTaskResponse,
    ReferenceTaskUpdateRequest,
)
from project.domain.reference_task import DEFAULT_STATUS
from project.infrastructure.api.dependencies import ReferenceTaskServiceDep

# ATTRIBUTE: reference_tasks_router (APIRouter)
# SUMMARY: Router exposing the reference vertical. Registered in router_registration.py only when
# the service exists — see the comment there.
reference_tasks_router = APIRouter(prefix="/reference-tasks", tags=["reference-tasks"])


# FUNCTION: create_reference_task
# SUMMARY: Create one reference task.
# OUTPUT: (ReferenceTaskResponse): The stored task with its generated id and creation time.
@reference_tasks_router.post(
    "",
    response_model=ReferenceTaskResponse,
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a reference task",
)
async def create_reference_task(
    payload: ReferenceTaskCreateRequest,
    service: ReferenceTaskServiceDep,
) -> ReferenceTaskResponse:
    # **LOGIC_STEP**: The handler never constructs the service and never touches the repository.
    # It receives the typed alias, and validate_endpoint_wiring.py fails the build if it does
    # anything else — see endpoint.no_direct_service_import and endpoint.no_depends_without_alias.
    task = await service.create_task(title=payload.title, details=payload.details)
    return ReferenceTaskResponse.from_domain(task)


# FUNCTION: get_reference_task
# SUMMARY: Load one reference task by identifier.
# OUTPUT: (ReferenceTaskResponse): The stored task.
@reference_tasks_router.get(
    "/{task_id}",
    response_model=ReferenceTaskResponse,
    summary="Read a reference task",
    responses={http_status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
async def get_reference_task(
    task_id: str,
    service: ReferenceTaskServiceDep,
) -> ReferenceTaskResponse:
    # **LOGIC_STEP**: No try/except here. The service raises NotFoundError and
    # exception_handlers.py turns every ProjectError subclass into its HTTP status once, for the
    # whole application. A handler that catches it locally produces a second, divergent error
    # envelope — which is how two services in one repository end up with two error formats.
    task = await service.get_task(task_id)
    return ReferenceTaskResponse.from_domain(task)


# FUNCTION: update_reference_task
# SUMMARY: Change some fields of one reference task.
# OUTPUT: (ReferenceTaskResponse): The stored task after the change.
@reference_tasks_router.patch(
    "/{task_id}",
    response_model=ReferenceTaskResponse,
    summary="Update a reference task",
    responses={
        http_status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        http_status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        http_status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    },
)
async def update_reference_task(
    task_id: str,
    payload: ReferenceTaskUpdateRequest,
    service: ReferenceTaskServiceDep,
) -> ReferenceTaskResponse:
    # **LOGIC_STEP**: `model_fields_set` is what makes `{"details": null}` mean "clear it" and an
    # absent `details` mean "leave it alone". Passing `payload.details` straight through cannot:
    # both arrive as None, so the nullable column could never be emptied. Everything the caller did
    # not mention stays UNCHANGED, and the service decides what that means.
    supplied = payload.model_fields_set
    task = await service.update_task(
        task_id,
        title=payload.title if "title" in supplied else UNCHANGED,
        details=payload.details if "details" in supplied else UNCHANGED,
        status=payload.status if "status" in supplied else UNCHANGED,
    )
    return ReferenceTaskResponse.from_domain(task)


# FUNCTION: list_reference_tasks
# SUMMARY: List reference tasks in one workflow status, newest first.
# OUTPUT: (ReferenceTaskListResponse): Matching tasks and their count.
@reference_tasks_router.get(
    "",
    response_model=ReferenceTaskListResponse,
    summary="List reference tasks by status",
    responses={http_status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse}},
)
async def list_reference_tasks(
    service: ReferenceTaskServiceDep,
    status: str = Query(default=DEFAULT_STATUS, description="Workflow status to filter by"),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum number of tasks to return"),
) -> ReferenceTaskListResponse:
    # **LOGIC_STEP**: Query bounds are declared twice on purpose — here for the OpenAPI contract
    # and a 422 before any code runs, and again in the service for callers that never pass through
    # FastAPI. The service is the one that decides; this declaration only documents and pre-checks.
    tasks = await service.list_tasks(status=status, limit=limit)
    return ReferenceTaskListResponse.from_domain(tasks)
