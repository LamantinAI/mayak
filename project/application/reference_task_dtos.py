# FILE: project/application/reference_task_dtos.py
# SUMMARY: Wire contract for the reference vertical. Kept out of project/application/dtos.py on
# purpose: that file holds kernel-wide models (errors, health), and a vertical that mixes its
# payloads into it cannot be deleted without editing a kernel file.

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_serializer

from project.application.core_model import CoreModel
from project.domain.reference_task import MAX_TITLE_LENGTH, ReferenceTask


# CLASS: project.application.reference_task_dtos.ReferenceTaskCreateRequest
# SUMMARY: Request body accepted by POST /reference-tasks.
# EXTENDS: project.application.core_model.CoreModel
class ReferenceTaskCreateRequest(CoreModel):
    # ATTRIBUTE: title (str)
    # SUMMARY: Human-readable task title. The upper bound is the domain's MAX_TITLE_LENGTH, which
    # also sizes the ORM column — a DTO that accepts more than the column holds turns a client
    # mistake into a 500. Declaring it here as well buys the 422 with a field-level message.
    title: str = Field(
        ...,
        min_length=1,
        max_length=MAX_TITLE_LENGTH,
        title="Title",
        description="Human-readable task title",
        examples=["Draft the migration plan"],
    )

    # ATTRIBUTE: details (str | None)
    # SUMMARY: Optional free-form description.
    details: str | None = Field(
        default=None,
        title="Details",
        description="Optional free-form task description",
        examples=["Cover both the forward and the rollback path"],
    )


# CLASS: project.application.reference_task_dtos.ReferenceTaskResponse
# SUMMARY: Response model for a single reference task.
# EXTENDS: project.application.core_model.CoreModel
class ReferenceTaskResponse(CoreModel):
    # ATTRIBUTE: id (str)
    # SUMMARY: Stable task identifier.
    id: str = Field(
        ...,
        title="Identifier",
        description="Stable task identifier",
        examples=["6f1b7d18-6f1a-4a1f-9a4c-2a0f5f0b6c11"],
    )

    # ATTRIBUTE: title (str)
    # SUMMARY: Human-readable task title.
    title: str = Field(..., title="Title", description="Human-readable task title")

    # ATTRIBUTE: details (str | None)
    # SUMMARY: Optional free-form description.
    details: str | None = Field(
        default=None,
        title="Details",
        description="Optional free-form task description",
    )

    # ATTRIBUTE: status (str)
    # SUMMARY: Workflow status of the task.
    status: str = Field(
        ...,
        title="Status",
        description="Workflow status of the task",
        examples=["pending"],
    )

    # ATTRIBUTE: created_at (datetime)
    # SUMMARY: UTC timestamp when the task was created.
    created_at: datetime = Field(
        ...,
        title="Created At",
        description="Timestamp when the task was created",
        examples=["2026-08-05T12:00:00Z"],
    )

    # FUNCTION: project/application/reference_task_dtos/ReferenceTaskResponse/from_domain
    # SUMMARY: Build the wire model from the domain object.
    # OUTPUT: (ReferenceTaskResponse): DTO carrying the same values as the domain task.
    @classmethod
    def from_domain(cls, task: ReferenceTask) -> "ReferenceTaskResponse":
        # **LOGIC_STEP**: The conversion lives on the DTO, so the endpoint stays a router and the
        # service never learns the wire format. Adding a response field is then a one-file change.
        return cls(
            id=task.id,
            title=task.title,
            details=task.details,
            status=task.status,
            created_at=task.created_at,
        )

    # FUNCTION: project/application/reference_task_dtos/ReferenceTaskResponse/serialize_created_at
    # SUMMARY: Render the timestamp in ISO 8601, matching HealthResponse.
    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()


# CLASS: project.application.reference_task_dtos.ReferenceTaskListResponse
# SUMMARY: Response model for a status-filtered task list.
# EXTENDS: project.application.core_model.CoreModel
class ReferenceTaskListResponse(CoreModel):
    # ATTRIBUTE: items (list[ReferenceTaskResponse])
    # SUMMARY: Matching tasks, newest first.
    items: list[ReferenceTaskResponse] = Field(
        ...,
        title="Items",
        description="Matching tasks, ordered by creation time descending",
    )

    # ATTRIBUTE: count (int)
    # SUMMARY: Number of tasks in this page. An envelope, not a bare array, because a bare
    # top-level array cannot grow a pagination cursor without breaking every client.
    count: int = Field(
        ...,
        ge=0,
        title="Count",
        description="Number of tasks returned in this page",
        examples=[2],
    )

    # FUNCTION: project/application/reference_task_dtos/ReferenceTaskListResponse/from_domain
    # SUMMARY: Build the envelope from domain objects.
    # OUTPUT: (ReferenceTaskListResponse): DTO wrapping the converted tasks and their count.
    @classmethod
    def from_domain(cls, tasks: list[ReferenceTask]) -> "ReferenceTaskListResponse":
        items = [ReferenceTaskResponse.from_domain(task) for task in tasks]
        return cls(items=items, count=len(items))
