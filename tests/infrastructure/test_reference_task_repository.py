# FILE: tests/infrastructure/test_reference_task_repository.py
# SUMMARY: Fast checks for the row -> domain mapper; the driver contract itself is proven functionally.
# NOTE: These tests hand the mapper the types psycopg really produces. They cannot prove that psycopg
# produces them — that is what tests/functional/src/test_reference_task_repository.py is for.

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from project.domain.reference_task import ReferenceTask
from project.core.logging.logger import get_logger
from project.infrastructure.persistence.reference_task_repository import (
    _SELECT_BY_ID,
    _SELECT_BY_STATUS,
    _UPDATE_BY_ID,
    ReferenceTaskRepository,
    row_to_reference_task,
)


# CLASS: tests.infrastructure.test_reference_task_repository.TestRowToReferenceTask
# SUMMARY: Verify driver-native values are normalized to the types the domain declares.
class TestRowToReferenceTask:
    # FUNCTION: test_uuid_identifier_becomes_string
    # SUMMARY: Verify a uuid.UUID from the driver is converted, not passed through.
    @pytest.mark.unit
    def test_uuid_identifier_becomes_string(self) -> None:
        identifier = UUID("11111111-2222-3333-4444-555555555555")
        created_at = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)

        task = row_to_reference_task(
            {
                "id": identifier,
                "title": "Reference task",
                "details": None,
                "status": "pending",
                "created_at": created_at,
                "updated_at": created_at,
            }
        )

        assert isinstance(task, ReferenceTask)
        assert task.id == "11111111-2222-3333-4444-555555555555"
        assert isinstance(task.id, str)

    # FUNCTION: test_optional_details_survive_as_none
    # SUMMARY: Verify a NULL details column stays None rather than becoming the string "None".
    @pytest.mark.unit
    def test_optional_details_survive_as_none(self) -> None:
        task = row_to_reference_task(
            {
                "id": UUID(int=1),
                "title": "Reference task",
                "details": None,
                "status": "done",
                "created_at": datetime(2026, 8, 5, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 8, 5, tzinfo=timezone.utc),
            }
        )

        assert task.details is None


# FUNCTION: _pool_returning
# SUMMARY: Build a pool double whose cursor answers with the given rows and records its calls.
# INPUT: rows (list[dict[str, Any]]): Rows the cursor should hand back.
# OUTPUT: (tuple[Any, MagicMock]): Pool double and the cursor mock its calls land on.
def _pool_returning(rows: list[dict[str, Any]]) -> tuple[Any, MagicMock]:
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=rows[0] if rows else None)
    cursor.fetchall = AsyncMock(return_value=rows)

    @asynccontextmanager
    async def _cursor(**_: Any) -> AsyncIterator[MagicMock]:
        yield cursor

    connection = MagicMock()
    connection.cursor = _cursor
    # **LOGIC_STEP**: `execute` answers with a cursor, because the write path reads `rowcount` off
    # it to report what it wrote. A bare AsyncMock returns another mock, and a span asserting on
    # its output then compares against a mock's repr.
    connection.execute = AsyncMock(return_value=cursor)
    cursor.rowcount = 1

    @asynccontextmanager
    async def _connection() -> AsyncIterator[MagicMock]:
        yield connection

    pool = MagicMock()
    pool.connection = _connection
    return pool, cursor


# FUNCTION: _finish_event
# SUMMARY: Pull one span's finish event out of the capture, failing loudly when the span is absent.
# INPUT: captured (list[dict]): Events collected by the log_capture fixture.
# INPUT: span_name (str): The span whose finish event is wanted, e.g. "db.reference_task.get".
# OUTPUT: (dict): The captured `span.finish` event for that span.
def _finish_event(captured: list[dict], span_name: str) -> dict:
    matches = [
        event
        for event in captured
        if event["kwargs"].get("event_id") == "span.finish"
        and event["kwargs"].get("name") == span_name
    ]
    assert len(matches) == 1, (
        f"expected exactly one finish event for {span_name}, got "
        f"{[event['kwargs'].get('name') for event in captured]}"
    )
    return matches[0]


# CLASS: tests.infrastructure.test_reference_task_repository.TestQueriesAreParameterised
# SUMMARY: Verify caller input reaches the driver as bound parameters, never inside the SQL text.
# NOTE: This is the security property behind the two `nosec B608` markers in the repository, and
# the only part of it a test can hold: that the query text is the module constant and every
# caller-supplied value travels in the parameter tuple. Whether psycopg then binds correctly is
# the functional suite's job — a mock cannot answer that and does not pretend to.
class TestQueriesAreParameterised:
    # FUNCTION: test_get_binds_the_identifier_instead_of_formatting_it
    # SUMMARY: Verify `get` sends the constant query with the id as a parameter.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_binds_the_identifier_instead_of_formatting_it(self) -> None:
        task_id = str(uuid4())
        pool, cursor = _pool_returning([])

        await ReferenceTaskRepository(pool).get(task_id)

        cursor.execute.assert_awaited_once_with(_SELECT_BY_ID, (task_id,))
        assert task_id not in _SELECT_BY_ID
        # **LOGIC_STEP**: The clause spelled out, not just "the constant was sent". Comparing the
        # executed query against the same constant is a tautology — both sides move together, so
        # the query itself can say anything and this test stays green. See the note on the
        # status query below for the measurement that made this rule.
        assert _SELECT_BY_ID.split(" WHERE ", 1)[1] == "id = %s"

    # FUNCTION: test_list_binds_status_and_limit_instead_of_formatting_them
    # SUMMARY: Verify `list_by_status` sends the constant query with both values as parameters.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_binds_status_and_limit_instead_of_formatting_them(self) -> None:
        pool, cursor = _pool_returning([])

        await ReferenceTaskRepository(pool).list_by_status("pending", limit=7)

        cursor.execute.assert_awaited_once_with(_SELECT_BY_STATUS, ("pending", 7))
        assert "pending" not in _SELECT_BY_STATUS
        # **LOGIC_STEP**: Filter, ordering and page bound pinned as text, because nothing else here
        # can see them. Measured on 2026-08-12: reversing `DESC` to `ASC` in this very query left
        # `make quality-gates` green — this file included — and failed only in `make test-e2e`.
        # The two assertions above cannot catch it: one compares the query to itself, the other
        # only proves the status was not interpolated. Every vertical copies this file, so the
        # blind spot was copied with it.
        assert (
            _SELECT_BY_STATUS.split(" WHERE ", 1)[1]
            == "status = %s ORDER BY created_at DESC LIMIT %s"
        )

    # FUNCTION: test_get_maps_a_returned_row_through_the_domain_converter
    # SUMMARY: Verify a found row comes back as a domain object rather than the raw mapping.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_maps_a_returned_row_through_the_domain_converter(self) -> None:
        row = {
            "id": UUID(int=7),
            "title": "Reference task",
            "details": None,
            "status": "pending",
            "created_at": datetime(2026, 8, 6, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 8, 6, tzinfo=timezone.utc),
        }
        pool, _ = _pool_returning([row])

        task = await ReferenceTaskRepository(pool).get(str(UUID(int=7)))

        assert isinstance(task, ReferenceTask)
        assert isinstance(task.id, str)

    # FUNCTION: test_get_returns_none_when_no_row_matches
    # SUMMARY: Verify absence is None rather than an exception or an empty object.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_returns_none_when_no_row_matches(self) -> None:
        pool, _ = _pool_returning([])

        assert await ReferenceTaskRepository(pool).get(str(uuid4())) is None

    # FUNCTION: test_list_maps_every_row
    # SUMMARY: Verify each returned row is converted, not just the first.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_maps_every_row(self) -> None:
        rows = [
            {
                "id": UUID(int=index),
                "title": f"Task {index}",
                "details": None,
                "status": "pending",
                "created_at": datetime(2026, 8, 6, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 8, 6, tzinfo=timezone.utc),
            }
            for index in range(3)
        ]
        pool, _ = _pool_returning(rows)

        tasks = await ReferenceTaskRepository(pool).list_by_status("pending")

        assert len(tasks) == 3
        assert all(isinstance(task.id, str) for task in tasks)

    # FUNCTION: test_add_binds_every_column_value
    # SUMMARY: Verify the insert passes values as parameters rather than building a literal row.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_add_binds_every_column_value(self) -> None:
        pool, _ = _pool_returning([])
        task = ReferenceTask(
            id=str(uuid4()),
            title="Reference task",
            details="details",
            status="pending",
            created_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
            updated_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
        )

        await ReferenceTaskRepository(pool).add(task)

        # **LOGIC_STEP**: The connection, not a cursor, runs the insert — assert against the same
        # object the repository actually calls, or the test passes while checking nothing.
        async with pool.connection() as connection:
            sql, params = connection.execute.await_args.args

        assert "%s" in sql
        assert params == (
            task.id,
            task.title,
            task.details,
            task.status,
            task.created_at,
            task.updated_at,
        )

    # FUNCTION: test_update_binds_the_new_state_and_the_expected_timestamp
    # SUMMARY: Verify the conditional write sends every value as a parameter, in the order the
    # statement declares them, and that the condition carries the OLD timestamp, not the new one.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_binds_the_new_state_and_the_expected_timestamp(self) -> None:
        pool, cursor = _pool_returning([])
        expected_updated_at = datetime(2026, 8, 6, tzinfo=timezone.utc)
        task = ReferenceTask(
            id=str(UUID(int=9)),
            title="Changed",
            details="details",
            status="in_progress",
            created_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
            updated_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        )

        await ReferenceTaskRepository(pool).update(task, expected_updated_at=expected_updated_at)

        cursor.execute.assert_awaited_once_with(
            _UPDATE_BY_ID,
            (
                task.title,
                task.details,
                task.status,
                task.updated_at,
                task.id,
                expected_updated_at,
            ),
        )
        # **LOGIC_STEP**: The condition pinned as text, for the same reason the ORDER BY of the
        # status query is. Nothing else here can see it: dropping `AND updated_at = %s` turns this
        # into a blind overwrite that still stores the row, still returns it, and still passes both
        # assertions above — while losing a concurrent writer's update in production. The tail of
        # the statement is pinned too, because a RETURNING list that stops matching _COLUMNS gives
        # the row mapper a shape it cannot map.
        assert _UPDATE_BY_ID.split(" WHERE ", 1)[1] == (
            "id = %s AND updated_at = %s "
            "RETURNING id, title, details, status, created_at, updated_at"
        )
        assert _UPDATE_BY_ID.split(" SET ", 1)[1].split(" WHERE ", 1)[0] == (
            "title = %s, details = %s, status = %s, updated_at = %s"
        )

    # FUNCTION: test_update_returns_none_when_the_row_moved
    # SUMMARY: Verify a write that matches no row is reported as a miss rather than as success.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_returns_none_when_the_row_moved(self) -> None:
        pool, _ = _pool_returning([])
        task = ReferenceTask(
            id=str(UUID(int=9)),
            title="Changed",
            details=None,
            status="pending",
            created_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
            updated_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        )

        stored = await ReferenceTaskRepository(pool).update(
            task, expected_updated_at=datetime(2026, 8, 6, tzinfo=timezone.utc)
        )

        assert stored is None


# CLASS: tests.infrastructure.test_reference_task_repository.TestAnImpossibleIdIsAMiss
# SUMMARY: Verify a lookup by something that cannot be an id answers "not found", not an error.
# NOTE: The id column is a uuid. psycopg meets a malformed literal with InvalidTextRepresentation,
# which unwinds unhandled and reaches the client as 500 — measured on a live container, where
# `GET /reference-tasks/does-not-exist` answered "InternalServerError" for what is plainly a
# request for something that is not there.
class TestAnImpossibleIdIsAMiss:
    # FUNCTION: test_a_malformed_id_never_reaches_the_driver
    # SUMMARY: Verify the repository short-circuits instead of handing the value to psycopg.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "task_id", ["does-not-exist", "", "12345", "not a uuid at all", "'; DROP TABLE x; --"]
    )
    async def test_a_malformed_id_never_reaches_the_driver(self, task_id: str) -> None:
        pool, cursor = _pool_returning([])
        repository = ReferenceTaskRepository(pool)

        assert await repository.get(task_id) is None
        cursor.execute.assert_not_awaited()

    # FUNCTION: test_a_well_formed_id_still_queries
    # SUMMARY: Verify the guard rejects only what cannot match, and does not swallow real lookups.
    @pytest.mark.unit
    async def test_a_well_formed_id_still_queries(self) -> None:
        pool, cursor = _pool_returning([])
        repository = ReferenceTaskRepository(pool)

        await repository.get("3f2504e0-4f89-11d3-9a0c-0305e82c3301")

        # **LOGIC_STEP**: "The driver was called" is half the contract. A guard that reached the
        # driver with the id blanked out, or with the wrong one, would satisfy the call count and
        # nothing else — so the id that travelled is what this asserts.
        assert cursor.execute.await_count == 1
        assert cursor.execute.await_args.args[1] == ("3f2504e0-4f89-11d3-9a0c-0305e82c3301",)

    # FUNCTION: test_the_driver_receives_the_canonical_spelling
    # SUMMARY: Verify a form Python accepts but PostgreSQL does not is normalised, not forwarded.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "spelling",
        [
            "urn:uuid:3f2504e0-4f89-11d3-9a0c-0305e82c3301",
            "uuid:3f2504e0-4f89-11d3-9a0c-0305e82c3301",
            "{3f2504e0-4f89-11d3-9a0c-0305e82c3301}",
            "3F2504E04F8911D39A0C0305E82C3301",
        ],
    )
    async def test_the_driver_receives_the_canonical_spelling(self, spelling: str) -> None:
        # **LOGIC_STEP**: The two grammars differ. Python's uuid.UUID accepts a `urn:uuid:` scheme
        # prefix; PostgreSQL's uuid input does not, so a predicate that merely said "yes, that
        # parses" handed the driver a literal it rejects — the same 500 the guard was added to
        # remove, for a narrower and far less obvious set of inputs.
        pool, cursor = _pool_returning([])
        repository = ReferenceTaskRepository(pool)

        await repository.get(spelling)

        parameters = cursor.execute.await_args.args[1]
        assert parameters == ("3f2504e0-4f89-11d3-9a0c-0305e82c3301",)


# CLASS: tests.infrastructure.test_reference_task_repository.TestEveryQueryGetsItsOwnSpan
# SUMMARY: Verify each repository method opens a named span and records the outcome, not just timing.
# NOTE: Without these the database is invisible to a trace. Measured: a request whose query
# returned the wrong rows rendered as `OK 200, 0 spans`, indistinguishable from a correct one. .agents/skills/add-vertical prescribes the same shape for every new vertical, so this is
# the test that keeps the reference honest about what it prescribes.
class TestEveryQueryGetsItsOwnSpan:
    # FUNCTION: test_a_query_span_is_written_at_the_level_production_runs_at
    # SUMMARY: Verify the database spans reach a log configured the way a deployment configures it.
    # NOTE: This is what the class could not see until 2026-09-06: every test here set the logger to
    # DEBUG, and a child span defaults to DEBUG, so the assertions passed against a level no
    # deployment uses. With APP_DEBUG=false the level is INFO, the spans were filtered, and an
    # operator reading a real trace saw the request with nothing inside it — the `0 spans` the
    # module's own note says these spans exist to prevent. The other tests in this class now assert
    # at INFO too; this one states the reason, so removing `level=logging.INFO` from the repository
    # is a red gate rather than a quieter log.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_a_query_span_is_written_at_the_level_production_runs_at(
        self, log_capture: list[dict], caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        pool, _ = _pool_returning([])

        # **LOGIC_STEP**: Inside a request span, which is what makes this test say anything. A
        # repository call with no parent span is itself the root, and a root span is written at
        # INFO whatever its own level — so the same assertion outside this block passes with or
        # without the fix. In a served request the parent is `http_request`, and the query span is
        # the child whose level decides whether an operator ever sees it.
        with get_logger("tests.infrastructure.request").span("http_request", root=True):
            await ReferenceTaskRepository(pool).get(str(uuid4()))

        finish = _finish_event(log_capture, "db.reference_task.get")
        assert finish["kwargs"]["level"] == logging.INFO
        assert finish["kwargs"]["data"]["output"] == {"row_found": False}

    # FUNCTION: test_get_records_whether_a_row_was_found
    # SUMMARY: Verify `db.reference_task.get` reports the miss that a duration alone would hide.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_records_whether_a_row_was_found(
        self, log_capture: list[dict], caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(
            logging.INFO, logger="project.infrastructure.persistence.reference_task_repository"
        )
        pool, _ = _pool_returning([])

        await ReferenceTaskRepository(pool).get(str(uuid4()))

        finish = _finish_event(log_capture, "db.reference_task.get")
        assert finish["kwargs"]["data"]["output"] == {"row_found": False}

    # FUNCTION: test_list_records_how_many_rows_came_back
    # SUMMARY: Verify `db.reference_task.list_by_status` reports the row count.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_records_how_many_rows_came_back(
        self, log_capture: list[dict], caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(
            logging.INFO, logger="project.infrastructure.persistence.reference_task_repository"
        )
        rows = [
            {
                "id": UUID(int=index),
                "title": "Reference task",
                "details": None,
                "status": "pending",
                "created_at": datetime(2026, 8, 11, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 8, 11, tzinfo=timezone.utc),
            }
            for index in range(1, 4)
        ]
        pool, _ = _pool_returning(rows)

        await ReferenceTaskRepository(pool).list_by_status("pending")

        finish = _finish_event(log_capture, "db.reference_task.list_by_status")
        assert finish["kwargs"]["data"]["output"] == {"row_count": 3}

    # FUNCTION: test_update_records_whether_the_row_was_still_there
    # SUMMARY: Verify `db.reference_task.update` reports which of the two outcomes it reached.
    # **LOGIC_STEP**: Both outcomes, because this span is the one whose two answers mean the most
    # different things: a write that landed, and a write that matched nothing because somebody
    # else got there first. Until 2026-09-06 no test looked this span up at all, so
    # `span.output["row_written"] = True` — the exact mutation the phase's own gate rule was
    # written to catch — left every gate green. The rule cannot see a span nobody mentions; this
    # test is what mentions it.
    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("rows", "expected"),
        [
            (
                [
                    {
                        "id": UUID(int=9),
                        "title": "Changed",
                        "details": None,
                        "status": "pending",
                        "created_at": datetime(2026, 8, 6, tzinfo=timezone.utc),
                        "updated_at": datetime(2026, 8, 7, tzinfo=timezone.utc),
                    }
                ],
                {"row_written": True},
            ),
            ([], {"row_written": False}),
        ],
    )
    async def test_update_records_whether_the_row_was_still_there(
        self,
        log_capture: list[dict],
        caplog: pytest.LogCaptureFixture,
        rows: list[dict],
        expected: dict,
    ) -> None:
        caplog.set_level(
            logging.INFO, logger="project.infrastructure.persistence.reference_task_repository"
        )
        pool, _ = _pool_returning(rows)
        task = ReferenceTask(
            id=str(UUID(int=9)),
            title="Changed",
            details=None,
            status="pending",
            created_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
            updated_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        )

        await ReferenceTaskRepository(pool).update(
            task, expected_updated_at=datetime(2026, 8, 6, tzinfo=timezone.utc)
        )

        finish = _finish_event(log_capture, "db.reference_task.update")
        assert finish["kwargs"]["data"]["output"] == expected

    # FUNCTION: test_add_opens_a_span_of_its_own
    # SUMMARY: Verify the write path is traced too, carrying the id it wrote.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_add_opens_a_span_of_its_own(
        self, log_capture: list[dict], caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(
            logging.INFO, logger="project.infrastructure.persistence.reference_task_repository"
        )
        pool, _ = _pool_returning([])
        task = ReferenceTask(
            id=str(UUID(int=9)),
            title="Reference task",
            details=None,
            status="pending",
            created_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
            updated_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

        await ReferenceTaskRepository(pool).add(task)

        finish = _finish_event(log_capture, "db.reference_task.add")
        assert finish["kwargs"]["data"]["task_id"] == str(UUID(int=9))
        # **LOGIC_STEP**: What the write reported, not that it happened. A span that says nothing
        # about its outcome reads identically whether the row landed or not — which is what
        # `test.span_output_pinned` now refuses, and why this span gained an output at all.
        assert finish["kwargs"]["data"]["output"] == {"rows_written": 1}
