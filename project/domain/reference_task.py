# FILE: project/domain/reference_task.py
# SUMMARY: Framework-free domain model backing the kernel's reference persistence example.

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# ATTRIBUTE: DEFAULT_STATUS (str)
# SUMMARY: Status every task starts in. Mirrors the server-side default in the ORM model.
DEFAULT_STATUS = "pending"

# ATTRIBUTE: ALLOWED_STATUSES (frozenset[str])
# SUMMARY: The closed set of workflow statuses. Lives in the domain, not in the DTO, because it is
# a business rule rather than a wire-format detail: the service enforces it for every caller,
# including callers that never pass through FastAPI validation.
ALLOWED_STATUSES = frozenset({DEFAULT_STATUS, "in_progress", "done"})

# ATTRIBUTE: MAX_TITLE_LENGTH (int)
# SUMMARY: Longest title the store can hold. One number, three consumers: the ORM column, the
# request DTO, and the service. It used to live only in the last two, which meant a caller that
# reached the service without passing through FastAPI — a background job, a queue consumer, a
# test — could hand over a longer title and turn its own mistake into a database error surfacing
# as a 500. A bound the domain owns applies to every caller.
MAX_TITLE_LENGTH = 200


# FUNCTION: normalize_task_id
# SUMMARY: Reduce a caller-supplied identifier to its canonical form, or report that it names nothing.
# INPUT: task_id (str): Identifier supplied by a caller, typically straight off a URL path.
# OUTPUT: (Optional[str]): Canonical lowercase hyphenated UUID, or None when the value cannot be one.
# NOTE: The store keys tasks by a uuid column, so a value that is not a UUID cannot match any row.
# Saying that here, in the domain, rather than letting the driver discover it: PostgreSQL answers a
# malformed literal with InvalidTextRepresentation, which unwinds as an unhandled exception and
# reaches the client as 500. Measured on a live container — `GET /reference-tasks/does-not-exist`
# returned "InternalServerError" for what is plainly a request for something that is not there.
#
# It returns the canonical form rather than a yes/no, because the two grammars are not the same
# one. Python's uuid.UUID also accepts `urn:uuid:...`, a bare `uuid:` or `urn:` prefix, braces, and
# unhyphenated hex; PostgreSQL's uuid input accepts braces, case and hyphen variation but no scheme
# prefix. A pure predicate therefore waved `urn:uuid:a0ee...` through to the driver, which rejected
# it — the same 500, for a narrower and much less obvious set of inputs. Canonicalising closes the
# gap in the direction that cannot fail: whatever spelling Python understands leaves here in the
# one spelling PostgreSQL is documented to accept.
def normalize_task_id(task_id: str) -> Optional[str]:
    try:
        return str(uuid.UUID(task_id))
    except (ValueError, AttributeError, TypeError):
        return None


# DATACLASS: project.domain.reference_task.ReferenceTask
# SUMMARY: Immutable domain view of a reference task, expressed without ORM or driver types.
@dataclass(frozen=True, slots=True)
class ReferenceTask:
    # ATTRIBUTE: id (str)
    # SUMMARY: Stable identifier. Always a string at the domain boundary, never a uuid.UUID.
    id: str

    # ATTRIBUTE: title (str)
    # SUMMARY: Human-readable task title.
    title: str

    # ATTRIBUTE: details (str | None)
    # SUMMARY: Optional task description.
    details: str | None

    # ATTRIBUTE: status (str)
    # SUMMARY: Workflow status of the task.
    status: str

    # ATTRIBUTE: created_at (datetime)
    # SUMMARY: Timestamp when the task was created.
    created_at: datetime

    # ATTRIBUTE: updated_at (datetime)
    # SUMMARY: Timestamp of the last write, and the token that makes an update detect a lost one.
    # NOTE: This field exists for the concurrency check, not for display. An update reads the task,
    # changes a field and writes it back; between the read and the write another request can do the
    # same, and a blind `UPDATE ... WHERE id = %s` then overwrites whatever that request stored —
    # a lost update, silent, with both callers told they succeeded. The repository's update instead
    # matches on `id AND updated_at`, so a row written by somebody else since the read matches
    # nothing and the service answers 409 rather than destroying that write. Full reasoning, and
    # when to prefer an integer version column instead, are in
    # docs/adr/ADR-007-autocommit-and-explicit-transactions.md.
    updated_at: datetime
