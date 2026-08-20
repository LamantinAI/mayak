# FILE: project/application/reference_task_service.py
# SUMMARY: Reference application service — the worked example of the orchestration layer in this
# template. It is deliberately small: create, read, list. What it demonstrates is the shape, not
# the feature.

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from project.domain.exceptions import NotFoundError, ValidationError
from project.domain.ports import ReferenceTaskRepositoryPort
from project.domain.reference_task import (
    ALLOWED_STATUSES,
    DEFAULT_STATUS,
    MAX_TITLE_LENGTH,
    ReferenceTask,
)

# ATTRIBUTE: MAX_LIST_LIMIT (int)
# SUMMARY: Hard ceiling on page size, enforced here rather than only in the DTO. A caller that
# reaches the service directly — a background job, another service, a test — bypasses FastAPI
# validation entirely, so a limit that lives only in the request model is not a limit.
MAX_LIST_LIMIT = 200


# CLASS: project.application.reference_task_service.ReferenceTaskService
# SUMMARY: Orchestrates the reference task aggregate over a repository port.
# The constructor takes the Protocol, never the concrete repository: that is what lets the unit
# tests run without a database and what keeps psycopg out of this layer.
# scripts/validate_architecture.py enforces the ban mechanically —
# _APPLICATION_BANNED_PREFIXES = ("project.infrastructure",) — so an import of the concrete
# adapter here fails the gate rather than merely violating a convention.
class ReferenceTaskService:
    # FUNCTION: project/application/reference_task_service/ReferenceTaskService/__init__
    # SUMMARY: Store the repository port supplied by service_registration.build_reference_services.
    def __init__(self, repository: ReferenceTaskRepositoryPort) -> None:
        self._repository = repository

    # FUNCTION: project/application/reference_task_service/ReferenceTaskService/create_task
    # SUMMARY: Build a task, assign its identity and creation time, and persist it.
    # OUTPUT: (ReferenceTask): The stored task, including the generated id and timestamp.
    # RAISES: ValidationError: When the title is empty or longer than MAX_TITLE_LENGTH.
    async def create_task(self, title: str, details: str | None = None) -> ReferenceTask:
        # **LOGIC_STEP**: The same bound the DTO declares, enforced again here for the same reason
        # MAX_LIST_LIMIT is: a non-HTTP caller never meets the DTO. Without this the title reaches
        # a varchar(200) column and the driver's error becomes a 500 instead of a 422.
        if not title.strip():
            raise ValidationError("title must not be empty")
        if len(title) > MAX_TITLE_LENGTH:
            raise ValidationError(
                f"title must be at most {MAX_TITLE_LENGTH} characters, got {len(title)}"
            )

        # **LOGIC_STEP**: Identity and time are decided here, not in the endpoint and not in the
        # database. The endpoint would make them client-controllable; a database default would
        # make them invisible to the unit tests and untestable without a live server.
        task = ReferenceTask(
            id=str(uuid4()),
            title=title,
            details=details,
            status=DEFAULT_STATUS,
            created_at=datetime.now(timezone.utc),
        )
        await self._repository.add(task)
        return task

    # FUNCTION: project/application/reference_task_service/ReferenceTaskService/get_task
    # SUMMARY: Load one task by identifier.
    # OUTPUT: (ReferenceTask): The stored task.
    # RAISES: NotFoundError: When no task carries this identifier.
    async def get_task(self, task_id: str) -> ReferenceTask:
        task = await self._repository.get(task_id)
        if task is None:
            # **LOGIC_STEP**: The service raises a domain exception; it does not know about HTTP.
            # exception_handlers.py maps NotFoundError to 404 for every endpoint at once, so an
            # endpoint that catches this to build its own response is duplicating the mapping.
            raise NotFoundError(f"Reference task '{task_id}' does not exist")
        return task

    # FUNCTION: project/application/reference_task_service/ReferenceTaskService/list_tasks
    # SUMMARY: List tasks in one workflow status, newest first.
    # OUTPUT: (list[ReferenceTask]): Matching tasks, ordered by creation time descending.
    # RAISES: ValidationError: When the status is outside the domain's closed set, or the limit
    #         falls outside 1..MAX_LIST_LIMIT.
    async def list_tasks(self, status: str, limit: int = 50) -> list[ReferenceTask]:
        if status not in ALLOWED_STATUSES:
            allowed = ", ".join(sorted(ALLOWED_STATUSES))
            raise ValidationError(f"Unknown status '{status}'. Allowed statuses: {allowed}")
        if not 1 <= limit <= MAX_LIST_LIMIT:
            raise ValidationError(f"limit must be between 1 and {MAX_LIST_LIMIT}, got {limit}")
        return await self._repository.list_by_status(status, limit)
