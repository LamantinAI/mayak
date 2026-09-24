# FILE: tests/db/test_reference_task_repository.py
# SUMMARY: The reference repository's SQL, run against a real PostgreSQL and judged by what it returns.
# NOTE: This is the suite a mock cannot replace. A mocked pool returns whatever the test author
# imagined; only the real driver reveals that a uuid column arrives as uuid.UUID, a numeric as
# Decimal, and a timestamptz as an aware datetime — and only a real query shows that its filter,
# ordering, page bound and write condition do what the text says. Verticals add their repository
# suites next to this one; tests/db/conftest.py gives them the database.

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from psycopg_pool import AsyncConnectionPool

from project.domain.reference_task import ReferenceTask
from project.infrastructure.persistence.reference_task_repository import ReferenceTaskRepository


# FUNCTION: repository
# SUMMARY: Provide the repository over the db tier's pool, every table empty.
# OUTPUT: (ReferenceTaskRepository): Repository bound to the tier's session pool.
@pytest.fixture
def repository(db_pool: AsyncConnectionPool) -> ReferenceTaskRepository:
    return ReferenceTaskRepository(db_pool)


# FUNCTION: _task
# SUMMARY: Build a domain task with a fresh identifier.
# OUTPUT: (ReferenceTask): Domain object ready to persist.
# NOTE: Title and details are two different non-empty strings on purpose. With details=None an
# INSERT that exchanged the two columns failed on the NOT NULL title — red, but for a reason that
# says nothing about the exchange; with distinct values the round trip below names it.
def _task(status: str = "pending", created_at: datetime | None = None) -> ReferenceTask:
    stamp = created_at or datetime.now(timezone.utc)
    return ReferenceTask(
        id=str(uuid4()),
        title="Reference task",
        details="What the reference task is about",
        status=status,
        created_at=stamp,
        updated_at=stamp,
    )


# FUNCTION: test_get_returns_domain_types_not_driver_types
# SUMMARY: Verify the identifier crosses the boundary as str, which is what the domain declares.
async def test_get_returns_domain_types_not_driver_types(
    repository: ReferenceTaskRepository,
) -> None:
    task = _task()

    await repository.add(task)
    loaded = await repository.get(task.id)

    assert loaded is not None
    # **LOGIC_STEP**: psycopg hands back uuid.UUID here. Without the conversion in
    # row_to_reference_task this assertion fails while every mocked unit test still passes.
    assert isinstance(loaded.id, str)
    # **LOGIC_STEP**: The whole object, not a few fields: an INSERT or a mapper that exchanged two
    # columns of one type passes a field-by-field check that happens to skip one of them.
    assert loaded == task


# FUNCTION: test_get_returns_none_for_unknown_identifier
# SUMMARY: Verify a missing row is reported as None rather than raising.
async def test_get_returns_none_for_unknown_identifier(
    repository: ReferenceTaskRepository,
) -> None:
    assert await repository.get(str(uuid4())) is None


# FUNCTION: test_list_by_status_filters_and_orders_newest_first
# SUMMARY: Verify the status filter and the created_at ordering hold against real SQL.
async def test_list_by_status_filters_and_orders_newest_first(
    repository: ReferenceTaskRepository,
) -> None:
    now = datetime.now(timezone.utc)
    older = _task(status="pending", created_at=now - timedelta(minutes=5))
    newer = _task(status="pending", created_at=now)
    other = _task(status="done", created_at=now)
    for task in (older, newer, other):
        await repository.add(task)

    pending = await repository.list_by_status("pending")

    assert [item.id for item in pending] == [newer.id, older.id]
    assert all(isinstance(item.id, str) for item in pending)


# FUNCTION: test_list_by_status_respects_limit
# SUMMARY: Verify the limit parameter reaches the query instead of being applied in Python.
async def test_list_by_status_respects_limit(repository: ReferenceTaskRepository) -> None:
    now = datetime.now(timezone.utc)
    for offset in range(3):
        await repository.add(_task(created_at=now - timedelta(minutes=offset)))

    assert len(await repository.list_by_status("pending", limit=2)) == 2


# FUNCTION: test_update_writes_the_new_state_and_returns_it
# SUMMARY: Verify the conditional write stores the change and hands back the stored row.
async def test_update_writes_the_new_state_and_returns_it(
    repository: ReferenceTaskRepository,
) -> None:
    task = _task()
    await repository.add(task)
    changed = replace(
        task,
        title="Changed",
        status="in_progress",
        updated_at=task.updated_at + timedelta(seconds=1),
    )

    stored = await repository.update(changed, expected_updated_at=task.updated_at)

    assert stored is not None
    assert stored.title == "Changed"
    assert stored.status == "in_progress"
    # **LOGIC_STEP**: Read it back through the driver rather than trusting the RETURNING row alone.
    # These are the same row only if the write really landed.
    reloaded = await repository.get(task.id)
    assert reloaded is not None
    assert reloaded.title == "Changed"
    assert reloaded.updated_at == stored.updated_at


# FUNCTION: test_update_refuses_a_row_another_writer_moved
# SUMMARY: Verify a write computed from a stale read matches no row and changes nothing.
# NOTE: This is the deterministic version of the race, staged by hand: read, let somebody else
# write, then try to write from the first read. A repository whose UPDATE matched on the id alone
# returns the row here and erases the other writer's title, with every unit test still green —
# measured on an agent-written vertical that had exactly that shape. Only a real
# database can show it, because the condition lives in the WHERE clause, not in Python.
async def test_update_refuses_a_row_another_writer_moved(
    repository: ReferenceTaskRepository,
) -> None:
    task = _task()
    await repository.add(task)
    stale_view = task

    # **LOGIC_STEP**: The other writer lands first, moving updated_at forward.
    somebody_else = replace(
        task,
        title="Written by somebody else",
        updated_at=task.updated_at + timedelta(seconds=1),
    )
    assert await repository.update(somebody_else, expected_updated_at=task.updated_at) is not None

    mine = replace(
        stale_view,
        title="Mine",
        updated_at=stale_view.updated_at + timedelta(seconds=2),
    )
    stored = await repository.update(mine, expected_updated_at=stale_view.updated_at)

    assert stored is None
    reloaded = await repository.get(task.id)
    assert reloaded is not None
    assert reloaded.title == "Written by somebody else"


# FUNCTION: test_update_of_an_unknown_identifier_is_a_miss
# SUMMARY: Verify a write against a row that does not exist reports a miss rather than raising.
async def test_update_of_an_unknown_identifier_is_a_miss(
    repository: ReferenceTaskRepository,
) -> None:
    absent = _task()

    assert await repository.update(absent, expected_updated_at=absent.updated_at) is None
