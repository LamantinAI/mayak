# FILE: project/application/reference_task_service.py
# SUMMARY: Reference application service: create, read, list and a read-modify-write update, over a port.

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from project.domain.exceptions import ConflictError, NotFoundError, ValidationError
from project.domain.ports import ReferenceTaskRepositoryPort
from project.domain.reference_task import (
    DEFAULT_STATUS,
    ReferenceTask,
    check_details,
    check_status,
    check_title,
)


class _Unchanged:
    pass


# "Not part of the patch", as distinct from "set to null": with `None` meaning both, a nullable
# field could never be cleared — `{"details": null}` would answer 200 and change nothing.
UNCHANGED = _Unchanged()

# Enforced here, not only over HTTP: a job or a test that calls the service never meets a request model.
MAX_LIST_LIMIT = 200


# Takes the Protocol, never the repository: scripts/validate_architecture.py fails an import of
# project.infrastructure here, and the unit tests run without a database.
class ReferenceTaskService:
    def __init__(self, repository: ReferenceTaskRepositoryPort) -> None:
        self._repository = repository

    async def create_task(self, title: str, details: str | None = None) -> ReferenceTask:
        # Identity and time are decided here: the endpoint would let a client choose them, and a
        # database default would hide them from the unit tests. One instant for both stamps,
        # because updated_at is the token the first update compares against.
        now = datetime.now(timezone.utc)
        task = ReferenceTask(
            id=str(uuid4()),
            title=check_title(title),
            details=check_details(details),
            status=DEFAULT_STATUS,
            created_at=now,
            updated_at=now,
        )
        await self._repository.add(task)
        return task

    async def get_task(self, task_id: str) -> ReferenceTask:
        task = await self._repository.get(task_id)
        if task is None:
            # A domain error; exception_handlers.py maps it to 404 for every endpoint at once.
            raise NotFoundError(f"Reference task '{task_id}' does not exist")
        return task

    async def list_tasks(self, status: str, limit: int = 50) -> list[ReferenceTask]:
        check_status(status)
        if not 1 <= limit <= MAX_LIST_LIMIT:
            raise ValidationError(
                f"limit must be between 1 and {MAX_LIST_LIMIT}, got {limit}", field="limit"
            )
        return await self._repository.list_by_status(status, limit)

    # Read-modify-write, the shape of every non-trivial update: read, stamp a new updated_at, and
    # write on condition of the OLD one. Drop the condition and a concurrent update is lost with no
    # failing test — ADR-007, "Read-modify-write across requests".
    async def update_task(
        self,
        task_id: str,
        title: str | None | _Unchanged = UNCHANGED,
        details: str | None | _Unchanged = UNCHANGED,
        status: str | None | _Unchanged = UNCHANGED,
    ) -> ReferenceTask:
        # An empty patch would bump updated_at and answer 200 for a request that asked for nothing.
        if all(isinstance(field, _Unchanged) for field in (title, details, status)):
            raise ValidationError("at least one of title, details or status must be provided")
        current = await self.get_task(task_id)
        changed = replace(
            current,
            title=current.title if isinstance(title, _Unchanged) else check_title(title),
            details=current.details if isinstance(details, _Unchanged) else check_details(details),
            status=current.status if isinstance(status, _Unchanged) else check_status(status),
            updated_at=datetime.now(timezone.utc),
        )
        stored = await self._repository.update(changed, expected_updated_at=current.updated_at)
        if stored is None:
            # Zero rows means the row moved (409: re-read and retry) or is gone (404: retrying
            # chases a row that will not come back). The UPDATE cannot tell; a second read can.
            await self.get_task(task_id)
            raise ConflictError(
                f"Reference task '{task_id}' was modified by another request; re-read and retry"
            )
        return stored
