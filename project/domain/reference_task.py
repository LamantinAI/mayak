# FILE: project/domain/reference_task.py
# SUMMARY: The reference task: its fields, its statuses, and the checks every writer meets, over HTTP or not.

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from project.domain.exceptions import ValidationError

DEFAULT_STATUS = "pending"
# Closing a task frees its title. "At most one open task per title" spans rows, so it is held by
# the partial unique index uq_reference_tasks_open_title, not by a check here — ADR-007.
CLOSED_STATUS = "done"
ALLOWED_STATUSES = frozenset({DEFAULT_STATUS, "in_progress", CLOSED_STATUS})
# Also the width of the ORM column: a longer title would reach the database and come back a 500.
MAX_TITLE_LENGTH = 200
# Far below SERVER_MAX_BODY_BYTES. That limit keeps one request from filling memory; this keeps a row
# from filling the table, and meets every writer, not only a request. Unbounded, this field was
# copied into a product whose vertical then stored a 20 MB note (2026-09-25).
MAX_DETAILS_LENGTH = 10_000


@dataclass(frozen=True, slots=True)
class ReferenceTask:
    id: str  # a str at the domain boundary, never uuid.UUID
    title: str
    details: str | None
    status: str
    created_at: datetime
    # Also the optimistic token: an update writes only while the row still carries the value it
    # read, so a concurrent update is refused rather than silently erased — ADR-007.
    updated_at: datetime


# The field checks live here, once, so every caller meets them — a request, a job, a test — and the
# DTO declares types only. The service calls them on every path that writes or filters.
def check_title(title: str | None) -> str:
    if title is None:
        raise ValidationError("title must not be null", field="title")
    if not title.strip():
        raise ValidationError("title must not be empty", field="title")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValidationError(
            f"title must be at most {MAX_TITLE_LENGTH} characters, got {len(title)}", field="title"
        )
    return title


def check_details(details: str | None) -> str | None:
    if details is not None and len(details) > MAX_DETAILS_LENGTH:
        raise ValidationError(
            f"details must be at most {MAX_DETAILS_LENGTH} characters, got {len(details)}",
            field="details",
        )
    return details


def check_status(status: str | None) -> str:
    if status is None:
        raise ValidationError("status must not be null", field="status")
    if status not in ALLOWED_STATUSES:
        allowed = ", ".join(sorted(ALLOWED_STATUSES))
        raise ValidationError(
            f"Unknown status '{status}'. Allowed statuses: {allowed}", field="status"
        )
    return status


# The canonical spelling of an id, or None when it cannot be one — a miss, not the 500 PostgreSQL's
# uuid input makes of a malformed literal. Canonical rather than a yes/no: Python also accepts
# `urn:uuid:...` and bare hex, which PostgreSQL rejects, so the caller's spelling never reaches it.
def normalize_task_id(task_id: str) -> str | None:
    try:
        return str(uuid.UUID(task_id))
    except (ValueError, AttributeError, TypeError):
        return None
