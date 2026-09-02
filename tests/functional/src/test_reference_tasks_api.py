# FILE: tests/functional/src/test_reference_tasks_api.py
# SUMMARY: Functional proof that the reference vertical answers over real HTTP against a real
# database — the assembled application, the running migrations, and the psycopg driver together.
# NOTE: The unit suite for this vertical runs against an in-memory repository, so it cannot see a
# missing migration, a column too narrow for the DTO, or a driver type that never reaches the
# domain. This file is where those failures show up. Verticals add their API suites here.

import asyncio
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


# FUNCTION: test_patch_changes_one_field_and_leaves_the_rest
# SUMMARY: Verify PATCH over real HTTP writes the change and returns the stored state.
@pytest.mark.asyncio
async def test_patch_changes_one_field_and_leaves_the_rest(
    make_post_request: SendRequest,
    make_patch_request: SendRequest,
    make_get_request: SendRequest,
) -> None:
    created = await make_post_request("/reference-tasks", {"title": "Before", "details": "Keep me"})
    task_id = created["body"]["id"]

    patched = await make_patch_request(f"/reference-tasks/{task_id}", {"status": "in_progress"})

    assert patched["status"] == 200
    assert patched["body"]["status"] == "in_progress"
    assert patched["body"]["details"] == "Keep me"
    # **LOGIC_STEP**: Read it back through a second request rather than trusting the response body.
    # RETURNING makes them the same row, and this is the check that proves it: a repository that
    # answered from the object it was handed instead of from the database would pass every unit
    # test and fail here.
    reloaded = await make_get_request(f"/reference-tasks/{task_id}")
    assert reloaded["body"]["status"] == "in_progress"
    assert reloaded["body"]["updated_at"] == patched["body"]["updated_at"]
    assert reloaded["body"]["updated_at"] > created["body"]["updated_at"]


# FUNCTION: test_two_sequential_patches_of_different_fields_both_survive
# SUMMARY: Verify a patch merges into the stored row instead of replacing it, so an earlier
# writer's field is still there after a later patch touches a different one.
@pytest.mark.asyncio
async def test_two_sequential_patches_of_different_fields_both_survive(
    make_post_request: SendRequest,
    make_patch_request: SendRequest,
    make_get_request: SendRequest,
) -> None:
    created = await make_post_request("/reference-tasks", {"title": "Shared", "details": None})
    task_id = created["body"]["id"]

    first = await make_patch_request(f"/reference-tasks/{task_id}", {"title": "Written first"})
    second = await make_patch_request(f"/reference-tasks/{task_id}", {"details": "Written second"})

    assert [first["status"], second["status"]] == [200, 200]
    reloaded = await make_get_request(f"/reference-tasks/{task_id}")
    assert reloaded["body"]["title"] == "Written first"
    assert reloaded["body"]["details"] == "Written second"


# FUNCTION: test_concurrent_patches_never_lose_a_write_that_reported_success
# SUMMARY: Verify two patches racing for the same task either both apply or one is refused — never
# both answering 200 while one of the two changes is gone.
# NOTE: This is the test the unit suite cannot write, and the one an agent-written vertical failed
# on 2026-09-02: its UPDATE matched on the id alone, so both requests reported success and the
# database kept only the later write. The two requests below patch DIFFERENT fields, which is what
# makes the loss invisible to a client — nothing about the payloads conflicts, only the states they
# were computed from. The assertion is the invariant rather than a fixed outcome, because the
# scheduler decides whether the two reads actually overlap: a refusal is correct, a silent loss is
# not.
@pytest.mark.asyncio
async def test_concurrent_patches_never_lose_a_write_that_reported_success(
    make_post_request: SendRequest,
    make_patch_request: SendRequest,
    make_get_request: SendRequest,
) -> None:
    created = await make_post_request("/reference-tasks", {"title": "Shared", "details": None})
    task_id = created["body"]["id"]

    changes = [{"title": "Written by A"}, {"details": "Written by B"}]
    results = await asyncio.gather(
        *(make_patch_request(f"/reference-tasks/{task_id}", change) for change in changes)
    )

    assert sorted(result["status"] for result in results) in ([200, 200], [200, 409])
    reloaded = await make_get_request(f"/reference-tasks/{task_id}")
    for change, result in zip(changes, results):
        if result["status"] == 200:
            field, value = next(iter(change.items()))
            assert reloaded["body"][field] == value


# FUNCTION: test_patch_with_an_explicit_null_empties_the_column
# SUMMARY: Verify a cleared field really lands as NULL in PostgreSQL, not as the string "None" and
# not as the old value kept by a service that could not tell absent from null.
@pytest.mark.asyncio
async def test_patch_with_an_explicit_null_empties_the_column(
    make_post_request: SendRequest,
    make_patch_request: SendRequest,
    make_get_request: SendRequest,
) -> None:
    created = await make_post_request(
        "/reference-tasks", {"title": "Has details", "details": "Remove me"}
    )
    task_id = created["body"]["id"]

    patched = await make_patch_request(f"/reference-tasks/{task_id}", {"details": None})

    assert patched["status"] == 200
    assert patched["body"]["details"] is None
    reloaded = await make_get_request(f"/reference-tasks/{task_id}")
    assert reloaded["body"]["details"] is None


# FUNCTION: test_patch_of_an_unknown_task_returns_404
# SUMMARY: Verify a patch against a missing identifier is a 404 on the deployed application too.
@pytest.mark.asyncio
async def test_patch_of_an_unknown_task_returns_404(make_patch_request: SendRequest) -> None:
    response = await make_patch_request(f"/reference-tasks/{uuid4()}", {"title": "Anything"})

    assert response["status"] == 404
