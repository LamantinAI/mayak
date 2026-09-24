# FILE: project/application/reference_task_dtos.py
# SUMMARY: Wire contract for the reference vertical — types only; the bounds are the domain's checks.
# Kept out of project/application/dtos.py, which holds kernel-wide models: a vertical mixed into it
# could not be deleted without editing a kernel file.

from __future__ import annotations

from datetime import datetime

from pydantic import field_serializer

from project.application.core_model import CoreModel
from project.domain.reference_task import ReferenceTask


class ReferenceTaskCreateRequest(CoreModel):
    title: str
    details: str | None = None


# A patch: an absent field keeps its stored value. The endpoint tells absent from an explicit null
# with model_fields_set, since both arrive here as None.
class ReferenceTaskUpdateRequest(CoreModel):
    title: str | None = None
    details: str | None = None
    status: str | None = None


class ReferenceTaskResponse(CoreModel):
    id: str
    title: str
    details: str | None
    status: str
    created_at: datetime
    updated_at: datetime  # a client re-reads it before it patches again

    @classmethod
    def from_domain(cls, task: ReferenceTask) -> ReferenceTaskResponse:
        return cls(
            id=task.id,
            title=task.title,
            details=task.details,
            status=task.status,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    # ISO 8601, rendered the way HealthResponse renders its timestamps.
    @field_serializer("created_at", "updated_at")
    def serialize_timestamps(self, value: datetime) -> str:
        return value.isoformat()


# An envelope, not a bare array: a top-level array cannot grow a pagination cursor without
# breaking every client.
class ReferenceTaskListResponse(CoreModel):
    items: list[ReferenceTaskResponse]
    count: int

    @classmethod
    def from_domain(cls, tasks: list[ReferenceTask]) -> ReferenceTaskListResponse:
        items = [ReferenceTaskResponse.from_domain(task) for task in tasks]
        return cls(items=items, count=len(items))
