# FILE: tests/db/test_reference_task_repository.py
# SUMMARY: The reference repository's SQL, run against a real PostgreSQL and judged by what it returns.
# The suite a mock cannot replace. A mocked pool returns whatever its author imagined; only the
# real driver shows that a uuid column arrives as uuid.UUID and a timestamptz as an aware datetime,
# and only a real query shows that its filter, order, page bound and write condition do what the
# text says. It replaced a mocked-pool suite that pinned each query as literal text (ADR-010): the
# pin caught an edit to the text, this catches a query that returns the wrong rows.

import asyncio
import logging
import multiprocessing
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from project.core.logging.logger import get_logger
from project.domain.exceptions import ConflictError
from project.domain.reference_task import ReferenceTask
from project.infrastructure.persistence.reference_task_repository import ReferenceTaskRepository

_NOW = datetime.now(timezone.utc)


@pytest.fixture
def repository(db_pool: AsyncConnectionPool) -> ReferenceTaskRepository:
    return ReferenceTaskRepository(db_pool)


# A task with a fresh id; title and details differ so an exchanged pair of columns shows.
# created_at equals updated_at, as at every real insert — so a mapper that swapped the two
# is invisible to a plain round trip and is caught by the update test, where they differ. The
# title is unique unless one is given: two open tasks may not share one.
def _task(status: str = "pending", minutes_ago: int = 0, title: str | None = None) -> ReferenceTask:
    stamp = _NOW - timedelta(minutes=minutes_ago)
    task_id = str(uuid4())
    return ReferenceTask(
        id=task_id,
        title=title or f"Reference task {task_id[:8]}",
        details="What the reference task is about",
        status=status,
        created_at=stamp,
        updated_at=stamp,
    )


async def test_a_stored_task_reads_back_whole_and_in_domain_types(
    repository: ReferenceTaskRepository,
) -> None:
    task = _task()
    await repository.add(task)

    loaded = await repository.get(task.id)

    # The whole object: an INSERT, a mapper or a RETURNING list that exchanged two
    # columns of one type passes any check that happens to skip one of them. The id is compared as
    # str because psycopg hands back uuid.UUID, and the mapper is what converts it.
    assert loaded == task
    assert isinstance(loaded.id, str)


async def test_an_id_is_found_in_any_spelling_uuid_accepts_and_a_malformed_one_is_a_miss(
    repository: ReferenceTaskRepository,
) -> None:
    task = _task()
    await repository.add(task)

    # PostgreSQL rejects `urn:uuid:...` that Python accepts, and answers a malformed
    # literal with an exception that reaches the client as 500; normalize_task_id is what turns
    # both into what they are — the same task, or no task.
    assert await repository.get(f"urn:uuid:{task.id}") == task
    assert await repository.get(UUID(task.id).hex.upper()) == task
    assert await repository.get("does-not-exist") is None
    assert await repository.get(str(uuid4())) is None


async def test_list_filters_by_status_newest_first_within_the_page(
    repository: ReferenceTaskRepository,
) -> None:
    # Inserted oldest first, the reverse of the expected answer, so a query that
    # lost its ORDER BY returns them in insertion order and fails; the `done` row is the newest of
    # all, so a filter that let it through changes the first element.
    pending = [_task(minutes_ago=minutes) for minutes in (3, 2, 1)]
    for task in [*pending, _task(status="done")]:
        await repository.add(task)

    assert await repository.list_by_status("pending") == pending[::-1]
    assert await repository.list_by_status("pending", limit=2) == pending[:0:-1]


async def test_an_update_lands_only_while_the_row_is_the_one_that_was_read(
    repository: ReferenceTaskRepository,
) -> None:
    task = _task()
    await repository.add(task)
    first = replace(
        task, title="First", details=None, status="done", updated_at=_NOW + timedelta(seconds=1)
    )

    assert await repository.update(first, expected_updated_at=task.updated_at) == first
    assert await repository.get(task.id) == first
    # The same stale read again — the deterministic version of a lost update. A
    # condition that matched the id alone, or `>=` the token, would overwrite `first` here.
    stale = replace(task, title="Stale", updated_at=_NOW + timedelta(seconds=2))
    assert await repository.update(stale, expected_updated_at=task.updated_at) is None
    assert await repository.get(task.id) == first
    assert await repository.update(_task(), expected_updated_at=_NOW) is None


async def test_two_tasks_cannot_share_an_id(repository: ReferenceTaskRepository) -> None:
    task = _task()
    await repository.add(task)

    with pytest.raises(psycopg.errors.UniqueViolation):
        await repository.add(replace(task, title="Another task"))


# Verify each method's span is written at INFO inside a request and says what happened.
# Inside a request span, because a span with no parent is itself the root and a root is
# written at INFO whatever its own level — outside this block the level check passes with or
# without `level=logging.INFO`. The outcome, because a query that matched nothing and one that
# worked read the same in a trace that records only duration.
async def test_every_query_leaves_its_outcome_in_a_span_production_can_see(
    repository: ReferenceTaskRepository, log_capture: list[dict], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    task = _task()
    with get_logger("tests.db.request").span("http_request", root=True):
        await repository.add(task)
        await repository.get(task.id)
        await repository.get(str(uuid4()))
        await repository.list_by_status("pending")
        await repository.update(
            replace(task, updated_at=_NOW + timedelta(seconds=1)), task.updated_at
        )
        await repository.update(task, task.updated_at)

    finished = [
        (event["kwargs"]["name"], event["kwargs"]["level"], event["kwargs"]["data"].get("output"))
        for event in log_capture
        if event["kwargs"].get("event_id") == "span.finish"
        and event["kwargs"]["name"].startswith("db.")
    ]
    assert finished == [
        ("db.reference_task.add", logging.INFO, {"rows_written": 1}),
        ("db.reference_task.get", logging.INFO, {"row_found": True}),
        ("db.reference_task.get", logging.INFO, {"row_found": False}),
        ("db.reference_task.list_by_status", logging.INFO, {"row_count": 1}),
        ("db.reference_task.update", logging.INFO, {"row_written": True}),
        ("db.reference_task.update", logging.INFO, {"row_written": False}),
    ]


# Verify the open-title rule: a second open task conflicts, a closed one frees its title.
# Fifty newer tasks first, so the one that holds the title is far past the first page — bench2
# measured a rule checked in the service against one page of the list, and it let this through.
async def test_only_one_open_task_may_carry_a_title_whatever_its_case(
    repository: ReferenceTaskRepository,
) -> None:
    first = _task(title="Plan", minutes_ago=60)
    await repository.add(first)
    for _ in range(50):
        await repository.add(_task())

    for duplicate in (_task(title="plan"), _task(status="in_progress", title="PLAN")):
        with pytest.raises(ConflictError):
            await repository.add(duplicate)
    closed = replace(first, status="done", updated_at=_NOW)
    await repository.update(closed, expected_updated_at=first.updated_at)
    await repository.add(_task(title="Plan"))


# In a process of its own: look for the title, wait until the other process has looked too, then write.
# The window a check-then-write implementation leaves open, held open on purpose by the
# barrier: both processes have read "free" before either writes. Only something outside the two
# processes can refuse one of the writes — an in-process lock cannot, which is how bench2's
# asyncio.Lock let 28 of 30 duplicates through.
def _add_once_both_saw_the_title_free(url: str, title: str, barrier: Any, results: Any) -> None:
    async def run() -> tuple[str, int, bool]:
        pool = AsyncConnectionPool(
            url, min_size=1, max_size=1, open=False, kwargs={"autocommit": True}
        )
        await pool.open(wait=True, timeout=30.0)
        try:
            repository = ReferenceTaskRepository(pool)
            free = all(task.title != title for task in await repository.list_by_status("pending"))
            barrier.wait(timeout=30)
            try:
                await repository.add(_task(title=title))
            except ConflictError:
                return ("conflict", os.getpid(), free)
            return ("added", os.getpid(), free)
        finally:
            await pool.close()

    results.put(asyncio.run(run()))


async def test_two_processes_that_both_saw_a_title_free_cannot_both_open_it(
    database_url: str, repository: ReferenceTaskRepository
) -> None:
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(2), context.Queue()
    workers = [
        context.Process(
            target=_add_once_both_saw_the_title_free,
            args=(database_url, "Shared", barrier, results),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    outcomes = [await asyncio.to_thread(results.get, True, 60) for _ in workers]
    for worker in workers:
        await asyncio.to_thread(worker.join, 60)

    assert sorted(outcome for outcome, _, _ in outcomes) == ["added", "conflict"]
    assert all(saw_free for _, _, saw_free in outcomes)
    assert len({pid for _, pid, _ in outcomes} - {os.getpid()}) == 2
    assert [task.title for task in await repository.list_by_status("pending")] == ["Shared"]
