# FILE: project/infrastructure/persistence/reference_task_repository.py
# SUMMARY: Reference psycopg repository showing how rows from the real driver become domain objects.

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from project.core.logging.logger import get_logger
from project.domain.reference_task import ReferenceTask, normalize_task_id

# ATTRIBUTE: logger (SemanticLogger)
# SUMMARY: Module logger, used to give every database call its own span under the request.
# NOTE: Without these spans the database is invisible to the trace. Measured on a live container:
# a request whose query returned the wrong rows rendered as `OK 200, 0 spans` — indistinguishable
# from a correct one. A child span costs 2784 ns in production, where its DEBUG events are filtered
# before they are built (see the guard in project/core/logging/logger.py), against 12821 ns with
# debug on. Three spans per request is 0.8 % of one core at 1000 rps.
logger = get_logger(__name__)

# ATTRIBUTE: _COLUMNS (str)
# SUMMARY: Column list shared by every read query so the row mapper sees a stable shape.
_COLUMNS = "id, title, details, status, created_at, updated_at"

# ATTRIBUTE: _SELECT_BY_ID (str)
# SUMMARY: Single-row read, built once at import so no query text is assembled per call.
# NOTE: bandit reports B608 (hardcoded_sql_expressions) on both constants below, and the
# suppression is a claim rather than a mute button. What earns it: the only interpolated value is
# `_COLUMNS`, defined two lines up, while every value that comes from a caller travels as a `%s`
# parameter psycopg binds server-side. bandit cannot tell those apart — it flags any f-string
# shaped like SQL, at Low confidence, and `-ll` filters by severity so Low confidence still fails
# the job. A vertical that interpolates a caller-supplied table, column, or sort direction has a
# real injection and must not carry these markers over with the pattern.
#
# Hoisting the queries out of the `execute()` calls does not remove the finding — measured; it
# only moves it. It is done anyway because the query text is then built once at import instead of
# on every call.
#
# The markers stay bare, and this comment never spells the marker out. bandit scans every
# comment in the file for it, reads the words that follow as test ids, and prints a warning line
# per word — and worse, a marker written inside prose suppresses the finding from the wrong
# place. Reasoning goes above the code; the marker itself carries only the rule id.
_SELECT_BY_ID = f"SELECT {_COLUMNS} FROM reference_tasks WHERE id = %s"  # nosec B608

# ATTRIBUTE: _SELECT_BY_STATUS (str)
# SUMMARY: Status-filtered read, newest first, with the page size bound as a parameter.
_SELECT_BY_STATUS = (
    # Same reasoning as _SELECT_BY_ID above: only _COLUMNS is interpolated.
    f"SELECT {_COLUMNS} FROM reference_tasks "  # nosec B608
    "WHERE status = %s ORDER BY created_at DESC LIMIT %s"
)

# ATTRIBUTE: _UPDATE_BY_ID (str)
# SUMMARY: Conditional write: it changes the row only while the row is still the one that was read.
# NOTE: `AND updated_at = %s` is the whole point of this statement, and the reason a vertical that
# needs an update copies THIS rather than writing the obvious `WHERE id = %s`. Without it two
# requests that read the same task and change different fields both report success and the second
# write silently erases the first — a lost update. With it, the second write matches no row, the
# repository returns None, and the service turns that into a 409 the caller can retry.
#
# RETURNING is not decoration either: it makes the write and the read of the stored state one
# statement, so the answer cannot be a row somebody changed again in between, and it keeps this
# method to a single `execute()` — which is what makes the pool's autocommit correct here rather
# than something to wrap in a transaction. See docs/adr/ADR-007-autocommit-and-explicit-transactions.md.
# Same nosec reasoning as the two queries above: only _COLUMNS is interpolated, every caller value
# travels as a bound parameter.
_UPDATE_BY_ID = (
    "UPDATE reference_tasks SET title = %s, details = %s, status = %s, updated_at = %s "  # nosec B608
    f"WHERE id = %s AND updated_at = %s RETURNING {_COLUMNS}"
)


# FUNCTION: row_to_reference_task
# SUMMARY: Convert one driver row into a domain object, normalizing driver-native types.
# INPUT: row (Mapping[str, Any]): Row produced by psycopg's dict_row factory.
# OUTPUT: (ReferenceTask): Domain object whose field types match the domain declaration.
def row_to_reference_task(row: Mapping[str, Any]) -> ReferenceTask:
    # **LOGIC_STEP**: psycopg returns a uuid.UUID for a uuid column and a Decimal for numeric,
    # regardless of how SQLAlchemy is configured — SQLAlchemy is not on this path at runtime.
    # Converting here is what keeps the driver's types out of the domain. Skipping this line is
    # the classic failure: unit tests pass against a mock that yields str, and the first real
    # request fails deep inside serialization.
    return ReferenceTask(
        id=str(row["id"]),
        title=row["title"],
        details=row["details"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# CLASS: project.infrastructure.persistence.reference_task_repository.ReferenceTaskRepository
# SUMMARY: Reference implementation of ReferenceTaskRepositoryPort over the shared psycopg pool.
class ReferenceTaskRepository:
    # FUNCTION: project/infrastructure/persistence/reference_task_repository/ReferenceTaskRepository/__init__
    # SUMMARY: Store the shared pool built by CompositionRoot; repositories never open their own.
    # INPUT: connection_pool (AsyncConnectionPool): Shared async PostgreSQL pool.
    def __init__(self, connection_pool: AsyncConnectionPool) -> None:
        self._pool = connection_pool

    # FUNCTION: project/infrastructure/persistence/reference_task_repository/ReferenceTaskRepository/add
    # SUMMARY: Insert a single task.
    # INPUT: task (ReferenceTask): Domain object to persist.
    async def add(self, task: ReferenceTask) -> None:
        with logger.span("db.reference_task.add", task_id=task.id):
            async with self._pool.connection() as connection:
                await connection.execute(
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

    # FUNCTION: project/infrastructure/persistence/reference_task_repository/ReferenceTaskRepository/get
    # SUMMARY: Load one task by identifier.
    # OUTPUT: (ReferenceTask | None): Domain object, or None when no row matches.
    async def get(self, task_id: str) -> ReferenceTask | None:
        # **LOGIC_STEP**: Canonicalised before the driver sees it, and answered here when it cannot
        # be. The id column is a uuid, and psycopg meets a malformed literal with
        # InvalidTextRepresentation — an unhandled exception that reaches the client as 500.
        # Measured on a live container: GET /reference-tasks/does-not-exist answered
        # "InternalServerError". No row can carry an id that is not a UUID, so the honest answer for
        # one is the answer this method already has for a miss. The canonical form is what goes to
        # the driver, not the caller's spelling: Python accepts `urn:uuid:...` where PostgreSQL does
        # not, so passing the original string through would have reproduced the same 500 for a
        # narrower set of inputs.
        canonical_id = normalize_task_id(task_id)
        if canonical_id is None:
            return None
        with logger.span("db.reference_task.get", task_id=canonical_id) as span:
            async with self._pool.connection() as connection:
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(_SELECT_BY_ID, (canonical_id,))
                    row = await cursor.fetchone()
            # **LOGIC_STEP**: The outcome, not just the timing. A query that silently matches
            # nothing and one that returns the row look identical in a trace that records only
            # duration — that is the shape the missing spans hid.
            span.output["row_found"] = row is not None
        return None if row is None else row_to_reference_task(row)

    # FUNCTION: project/infrastructure/persistence/reference_task_repository/ReferenceTaskRepository/list_by_status
    # SUMMARY: List tasks in one workflow status, newest first.
    # OUTPUT: (list[ReferenceTask]): Domain objects ordered by creation time descending.
    async def list_by_status(self, status: str, limit: int = 50) -> list[ReferenceTask]:
        with logger.span("db.reference_task.list_by_status", status=status, limit=limit) as span:
            async with self._pool.connection() as connection:
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(_SELECT_BY_STATUS, (status, limit))
                    rows = await cursor.fetchall()
            span.output["row_count"] = len(rows)
        return [row_to_reference_task(row) for row in rows]

    # FUNCTION: project/infrastructure/persistence/reference_task_repository/ReferenceTaskRepository/update
    # SUMMARY: Write a changed task only while the stored row still carries the timestamp it was read with.
    # OUTPUT: (ReferenceTask | None): The stored task, or None when no row matched the condition.
    async def update(
        self,
        task: ReferenceTask,
        expected_updated_at: datetime,
    ) -> ReferenceTask | None:
        # **LOGIC_STEP**: Canonicalised for the same reason `get` does it: the id column is a uuid,
        # and a malformed literal reaches the client as a 500 rather than as the miss it is.
        canonical_id = normalize_task_id(task.id)
        if canonical_id is None:
            return None
        with logger.span("db.reference_task.update", task_id=canonical_id) as span:
            async with self._pool.connection() as connection:
                async with connection.cursor(row_factory=dict_row) as cursor:
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
            # **LOGIC_STEP**: The outcome, not just the timing — and here the two outcomes mean
            # completely different things to whoever reads the trace. A miss is either a row
            # somebody else wrote first or a row that is gone, and both are worth seeing without
            # correlating a 409 back to a query that looked like every other one.
            span.output["row_written"] = row is not None
        return None if row is None else row_to_reference_task(row)
