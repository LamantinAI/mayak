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


# CLASS: project.application.reference_task_dtos.ReferenceTaskUpdateRequest
# SUMMARY: Request body accepted by PATCH /reference-tasks/{task_id}.
# EXTENDS: project.application.core_model.CoreModel
# NOTE: Every field is optional and every default is None, which is what makes this a patch rather
# than a replacement: an absent field keeps its stored value. The service refuses a body where all
# three are absent, so "optional" never degrades into "a write that changes nothing but bumps the
# timestamp". The bounds repeat the create request's on purpose — a caller who patches a title past
# the column width deserves the same 422 as one who creates it that way.
class ReferenceTaskUpdateRequest(CoreModel):
    # ATTRIBUTE: title (str | None)
    # SUMMARY: New title, or None to keep the stored one.
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_TITLE_LENGTH,
        title="Title",
        description="New human-readable task title",
        examples=["Draft the rollback plan"],
    )

    # ATTRIBUTE: details (str | None)
    # SUMMARY: New description, or None to keep the stored one.
    details: str | None = Field(
        default=None,
        title="Details",
        description="New free-form task description",
        examples=["Cover the forward path too"],
    )

    # ATTRIBUTE: status (str | None)
    # SUMMARY: New workflow status, or None to keep the stored one.
    # NOTE: Typed as a plain string here and checked against ALLOWED_STATUSES in the service, not
    # as an Enum in the DTO. The closed set is a business rule, and a caller that reaches the
    # service without passing through FastAPI — a job, a queue consumer, a test — must meet it too.
    status: str | None = Field(
        default=None,
        min_length=1,
        title="Status",
        description="New workflow status of the task",
        examples=["in_progress"],
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

    # ATTRIBUTE: updated_at (datetime)
    # SUMMARY: UTC timestamp of the last write; a client re-reads it before it patches again.
    updated_at: datetime = Field(
        ...,
        title="Updated At",
        description="Timestamp of the last write to the task",
        examples=["2026-08-05T12:30:00Z"],
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
            updated_at=task.updated_at,
        )

    # FUNCTION: project/application/reference_task_dtos/ReferenceTaskResponse/serialize_timestamps
    # SUMMARY: Render both timestamps in ISO 8601, matching HealthResponse.
    @field_serializer("created_at", "updated_at")
    def serialize_timestamps(self, value: datetime) -> str:
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
