# FILE: tests/functional/src/test_reference_task_repository.py
# SUMMARY: Functional proof that the reference repository maps real psycopg rows onto domain types.
# NOTE: This is the suite that a mock cannot replace. A mocked pool returns whatever the test author
# imagined; only the real driver reveals that a uuid column arrives as uuid.UUID, a numeric as
# Decimal, and a timestamptz as an aware datetime. Verticals add their repository suites here.

from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool

from project.domain.reference_task import ReferenceTask
from project.infrastructure.persistence.reference_task_repository import ReferenceTaskRepository
from settings import postgres_settings

# **LOGIC_STEP**: Marked at module level so `-m e2e` selects the whole functional suite.
# The markers were declared in pytest.ini and worn by nothing, so the selector they exist
# for returned an empty set.
pytestmark = pytest.mark.e2e


# FUNCTION: repository
# SUMMARY: Provide a repository bound to a real pool against the functional test database.
# OUTPUT: (AsyncGenerator[ReferenceTaskRepository, None]): Repository over an open psycopg pool.
@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def repository() -> AsyncGenerator[ReferenceTaskRepository, None]:
    pool = AsyncConnectionPool(
        conninfo=postgres_settings.database_url,
        min_size=1,
        max_size=2,
        open=False,
    )
    await pool.open(wait=True, timeout=30.0)
    try:
        yield ReferenceTaskRepository(pool)
    finally:
        await pool.close()


# FUNCTION: _task
# SUMMARY: Build a domain task with a fresh identifier.
# OUTPUT: (ReferenceTask): Domain object ready to persist.
def _task(status: str = "pending", created_at: datetime | None = None) -> ReferenceTask:
    return ReferenceTask(
        id=str(uuid4()),
        title="Reference task",
        details=None,
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
    )


# FUNCTION: test_get_returns_domain_types_not_driver_types
# SUMMARY: Verify the identifier crosses the boundary as str, which is what the domain declares.
@pytest.mark.asyncio
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
    assert loaded.id == task.id
    assert loaded.title == task.title
    assert loaded.status == task.status


# FUNCTION: test_get_returns_none_for_unknown_identifier
# SUMMARY: Verify a missing row is reported as None rather than raising.
@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_identifier(
    repository: ReferenceTaskRepository,
) -> None:
    assert await repository.get(str(uuid4())) is None


# FUNCTION: test_list_by_status_filters_and_orders_newest_first
# SUMMARY: Verify the status filter and the created_at ordering hold against real SQL.
@pytest.mark.asyncio
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
@pytest.mark.asyncio
async def test_list_by_status_respects_limit(repository: ReferenceTaskRepository) -> None:
    now = datetime.now(timezone.utc)
    for offset in range(3):
        await repository.add(_task(created_at=now - timedelta(minutes=offset)))

    assert len(await repository.list_by_status("pending", limit=2)) == 2
