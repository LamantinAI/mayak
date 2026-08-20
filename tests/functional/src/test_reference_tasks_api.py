# FILE: tests/functional/src/test_reference_tasks_api.py
# SUMMARY: Functional proof that the reference vertical answers over real HTTP against a real
# database — the assembled application, the running migrations, and the psycopg driver together.
# NOTE: The unit suite for this vertical runs against an in-memory repository, so it cannot see a
# missing migration, a column too narrow for the DTO, or a driver type that never reaches the
# domain. This file is where those failures show up. Verticals add their API suites here.

from uuid import uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from utils.helpers import SendRequest

# **LOGIC_STEP**: Marked at module level so `-m e2e` selects the whole functional suite.
# The markers were declared in pytest.ini and worn by nothing, so the selector they exist
# for returned an empty set.
pytestmark = pytest.mark.e2e


# FUNCTION: test_create_then_read_round_trip
# SUMMARY: Verify a task created over HTTP is readable back with the same identity.
@pytest.mark.asyncio
async def test_create_then_read_round_trip(
    make_post_request: SendRequest, make_get_request: SendRequest
) -> None:
    created = await make_post_request(
        "/reference-tasks",
        {"title": "Functional round trip", "details": "Created over HTTP"},
    )

    assert created["status"] == 201
    task_id = created["body"]["id"]

    loaded = await make_get_request(f"/reference-tasks/{task_id}")

    assert loaded["status"] == 200
    assert loaded["body"]["id"] == task_id
    assert loaded["body"]["title"] == "Functional round trip"
    assert loaded["body"]["details"] == "Created over HTTP"
    # **LOGIC_STEP**: The identifier must arrive as a JSON string. psycopg returns uuid.UUID for
    # this column, and the conversion in row_to_reference_task is the only thing standing between
    # that and a serialization failure. The unit suite cannot observe it.
    assert isinstance(loaded["body"]["id"], str)


# FUNCTION: test_created_task_is_persisted_in_the_database
# SUMMARY: Verify the row reaches PostgreSQL rather than only the response body.
@pytest.mark.asyncio
async def test_created_task_is_persisted_in_the_database(
    make_post_request: SendRequest,
    postgres_connection: AsyncConnection[TupleRow],
) -> None:
    created = await make_post_request("/reference-tasks", {"title": "Persisted"})
    task_id = created["body"]["id"]

    async with postgres_connection.cursor() as cursor:
        await cursor.execute("SELECT title, status FROM reference_tasks WHERE id = %s", (task_id,))
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == "Persisted"
    assert row[1] == "pending"


# FUNCTION: test_unknown_identifier_returns_404
# SUMMARY: Verify the domain exception reaches the client as 404 through the assembled app.
@pytest.mark.asyncio
async def test_unknown_identifier_returns_404(make_get_request: SendRequest) -> None:
    response = await make_get_request(f"/reference-tasks/{uuid4()}")

    assert response["status"] == 404
    assert response["body"]["error"]["status_code"] == 404


# FUNCTION: test_list_filters_by_status_and_orders_newest_first
# SUMMARY: Verify filtering and ordering survive the real SQL query and the real index.
@pytest.mark.asyncio
async def test_list_filters_by_status_and_orders_newest_first(
    make_post_request: SendRequest,
    make_get_request: SendRequest,
    postgres_connection: AsyncConnection[TupleRow],
) -> None:
    first = (await make_post_request("/reference-tasks", {"title": "First"}))["body"]["id"]
    second = (await make_post_request("/reference-tasks", {"title": "Second"}))["body"]["id"]
    # **LOGIC_STEP**: Move one task out of the default status directly in the database. The API
    # exposes no transition yet, and inventing one just to test the filter would test the wrong
    # thing — the filter, not the transition, is what this case is about.
    await postgres_connection.execute(
        "UPDATE reference_tasks SET status = 'done' WHERE id = %s", (first,)
    )
    await postgres_connection.commit()

    response = await make_get_request("/reference-tasks", {"status": "pending"})

    assert response["status"] == 200
    assert [item["id"] for item in response["body"]["items"]] == [second]
    assert response["body"]["count"] == 1


# FUNCTION: test_unknown_status_returns_422
# SUMMARY: Verify the service-level status rule is enforced on the deployed application too.
@pytest.mark.asyncio
async def test_unknown_status_returns_422(make_get_request: SendRequest) -> None:
    response = await make_get_request("/reference-tasks", {"status": "archived"})

    assert response["status"] == 422
    assert "Unknown status" in response["body"]["error"]["message"]


# FUNCTION: test_title_longer_than_the_column_is_rejected
# SUMMARY: Verify the DTO bound rejects oversized titles instead of letting PostgreSQL raise.
@pytest.mark.asyncio
async def test_title_longer_than_the_column_is_rejected(
    make_post_request: SendRequest,
) -> None:
    # **LOGIC_STEP**: The column is String(200). Without the matching max_length on the request
    # model this returns 500 from inside the driver — the exact class of defect a functional run
    # exists to catch, and one no mocked repository can reproduce.
    response = await make_post_request("/reference-tasks", {"title": "x" * 201})

    assert response["status"] == 422
