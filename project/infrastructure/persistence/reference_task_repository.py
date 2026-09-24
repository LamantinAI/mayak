# FILE: project/infrastructure/persistence/reference_task_repository.py
# SUMMARY: Reference psycopg repository showing how rows from the real driver become domain objects.

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Mapping

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from project.core.logging.logger import get_logger
from project.domain.exceptions import ConflictError
from project.domain.reference_task import ReferenceTask, normalize_task_id

# Every call gets its own span at INFO with its outcome in span.output: without them a query that
# returned the wrong rows reads `OK 200, 0 spans`, and a child span left at its DEBUG default is
# dropped in production — docs/tracing.md, "A database call is a span an operator can see".
logger = get_logger(__name__)

_COLUMNS = "id, title, details, status, created_at, updated_at"

# bandit flags these f-strings as B608 (SQL built from strings), and the suppression is a claim:
# the only interpolated value is _COLUMNS above, while every caller value travels as a `%s`
# parameter bound server-side. A vertical that interpolates a caller-supplied table, column or
# sort direction has a real injection and must not copy the marker. The marker carries only the
# rule id — bandit reads words after it as test ids — so its reason lives here, above the code.
_SELECT_BY_ID = f"SELECT {_COLUMNS} FROM reference_tasks WHERE id = %s"  # nosec B608

_SELECT_BY_STATUS = (
    f"SELECT {_COLUMNS} FROM reference_tasks "  # nosec B608
    "WHERE status = %s ORDER BY created_at DESC LIMIT %s"
)

# `AND updated_at = %s` is the point: a write computed from a stale read matches no row instead of
# erasing the other writer's change. RETURNING makes the write and the read of what was stored one
# statement, which is what makes the pool's autocommit correct here — ADR-007.
_UPDATE_BY_ID = (
    "UPDATE reference_tasks SET title = %s, details = %s, status = %s, updated_at = %s "  # nosec B608
    f"WHERE id = %s AND updated_at = %s RETURNING {_COLUMNS}"
)

# The unique index that holds "one open task per title", created by migration b5e2c1a9d4f0.
_OPEN_TITLE_INDEX = "uq_reference_tasks_open_title"


# The index holds the rule, not a read before the write: another process can write between the
# two, and no in-process lock reaches it (ADR-007). Matched by constraint name, so a duplicate id
# is not reported as this rule. A rule held by an EXCLUDE constraint raises ExclusionViolation
# instead — catch that, or it passes this handler as a 500.
@asynccontextmanager
async def _open_title_taken_is_a_conflict(title: str) -> AsyncIterator[None]:
    try:
        yield
    except UniqueViolation as exc:
        if exc.diag.constraint_name != _OPEN_TITLE_INDEX:
            raise
        raise ConflictError(f"An open reference task titled '{title}' already exists") from exc


# The driver's types stop here: psycopg hands back uuid.UUID for a uuid column (Decimal for numeric),
# and the domain holds a str.
def row_to_reference_task(row: Mapping[str, Any]) -> ReferenceTask:
    return ReferenceTask(
        id=str(row["id"]),
        title=row["title"],
        details=row["details"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class ReferenceTaskRepository:
    # The pool is built by CompositionRoot; a repository never opens its own.
    def __init__(self, connection_pool: AsyncConnectionPool) -> None:
        self._pool = connection_pool

    async def add(self, task: ReferenceTask) -> None:
        with logger.span("db.reference_task.add", task_id=task.id, level=logging.INFO) as span:
            async with (
                self._pool.connection() as connection,
                _open_title_taken_is_a_conflict(task.title),
            ):
                cursor = await connection.execute(
                    "INSERT INTO reference_tasks "
                    "(id, title, details, status, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        task.id,
                        task.title,
                        task.details,
                        task.status,
                        task.created_at,
                        task.updated_at,
                    ),
                )
                span.output["rows_written"] = cursor.rowcount

    async def get(self, task_id: str) -> ReferenceTask | None:
        canonical_id = normalize_task_id(task_id)
        if canonical_id is None:
            return None
        with logger.span("db.reference_task.get", task_id=canonical_id, level=logging.INFO) as span:
            async with self._pool.connection() as connection:
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(_SELECT_BY_ID, (canonical_id,))
                    row = await cursor.fetchone()
            span.output["row_found"] = row is not None
        return None if row is None else row_to_reference_task(row)

    async def list_by_status(self, status: str, limit: int = 50) -> list[ReferenceTask]:
        with logger.span(
            "db.reference_task.list_by_status", status=status, limit=limit, level=logging.INFO
        ) as span:
            async with self._pool.connection() as connection:
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(_SELECT_BY_STATUS, (status, limit))
                    rows = await cursor.fetchall()
            span.output["row_count"] = len(rows)
        return [row_to_reference_task(row) for row in rows]

    async def update(
        self,
        task: ReferenceTask,
        expected_updated_at: datetime,
    ) -> ReferenceTask | None:
        # None when no row matched the condition — the row moved since the read, or is gone.
        canonical_id = normalize_task_id(task.id)
        if canonical_id is None:
            return None
        with logger.span(
            "db.reference_task.update", task_id=canonical_id, level=logging.INFO
        ) as span:
            async with self._pool.connection() as connection:
                async with (
                    connection.cursor(row_factory=dict_row) as cursor,
                    _open_title_taken_is_a_conflict(task.title),
                ):
                    await cursor.execute(
                        _UPDATE_BY_ID,
                        (
                            task.title,
                            task.details,
                            task.status,
                            task.updated_at,
                            canonical_id,
                            expected_updated_at,
                        ),
                    )
                    row = await cursor.fetchone()
            span.output["row_written"] = row is not None
        return None if row is None else row_to_reference_task(row)
