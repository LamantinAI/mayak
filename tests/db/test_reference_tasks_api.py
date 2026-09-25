# FILE: tests/db/test_reference_tasks_api.py
# SUMMARY: The reference vertical over HTTP, in process, on the real repository: status codes, filters, conflicts.
# The assembled application with its service on the db tier's pool, called through ASGI on the
# tier's event loop — a TestClient would run the app on a loop of its own, where the pool cannot be
# used. The built image, its entrypoint and its migrations are left to tests/functional.

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from psycopg_pool import AsyncConnectionPool

from project.application.reference_task_service import MAX_LIST_LIMIT, ReferenceTaskService
from project.domain.reference_task import MAX_DETAILS_LENGTH, MAX_TITLE_LENGTH, ReferenceTask
from project.infrastructure.persistence.reference_task_repository import ReferenceTaskRepository


@asynccontextmanager
async def _serve(app: FastAPI, repository: ReferenceTaskRepository) -> AsyncIterator[AsyncClient]:
    app.state.services["reference_task_service"] = ReferenceTaskService(repository=repository)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# The race staged where it happens: the service holds a row the table no longer has. Real SQL
# on both sides, so the outcome is decided by the WHERE clause, not by an imitation of it.
class _SomebodyElseActsAfterTheRead(ReferenceTaskRepository):
    def __init__(
        self, pool: AsyncConnectionPool, act: Callable[[ReferenceTask], Awaitable[None]]
    ) -> None:
        super().__init__(pool)
        self._act: Callable[[ReferenceTask], Awaitable[None]] | None = act

    async def get(self, task_id: str) -> ReferenceTask | None:
        task = await super().get(task_id)
        if task is not None and self._act is not None:
            act, self._act = self._act, None
            await act(task)
        return task


async def test_a_created_task_answers_201_and_reads_back(
    fastapi_app: FastAPI, db_pool: AsyncConnectionPool
) -> None:
    async with _serve(fastapi_app, ReferenceTaskRepository(db_pool)) as client:
        created = await client.post(
            "/reference-tasks", json={"title": "Draft", "details": "Both ways"}
        )
        read = await client.get(f"/reference-tasks/{created.json()['id']}")

    assert created.status_code == 201
    assert read.status_code == 200 and read.json() == created.json()


async def test_an_unknown_or_malformed_id_answers_404_in_the_shared_envelope(
    fastapi_app: FastAPI, db_pool: AsyncConnectionPool
) -> None:
    async with _serve(fastapi_app, ReferenceTaskRepository(db_pool)) as client:
        for task_id in (str(uuid4()), "does-not-exist"):
            response = await client.get(f"/reference-tasks/{task_id}")
            assert response.status_code == 404, task_id
            assert response.json()["error"]["status_code"] == 404
        missing = await client.patch(f"/reference-tasks/{uuid4()}", json={"title": "Anything"})

    assert missing.status_code == 404


async def test_the_list_answers_the_status_it_was_asked_for(
    fastapi_app: FastAPI, db_pool: AsyncConnectionPool
) -> None:
    async with _serve(fastapi_app, ReferenceTaskRepository(db_pool)) as client:
        kept = (await client.post("/reference-tasks", json={"title": "Pending"})).json()["id"]
        done = (await client.post("/reference-tasks", json={"title": "Done"})).json()["id"]
        await client.patch(f"/reference-tasks/{done}", json={"status": "done"})

        # A status other than the default. Asked only for the default, an endpoint
        # that ignored the parameter answered the same page, and only the 422 tests noticed.
        by_status = {
            status: (await client.get("/reference-tasks", params={"status": status})).json()
            for status in ("done", "pending")
        }

    assert [item["id"] for item in by_status["done"]["items"]] == [done]
    assert by_status["done"]["count"] == 1
    assert [item["id"] for item in by_status["pending"]["items"]] == [kept]


# The DTOs declare types only, so every refusal here is the domain's or the service's, reached over
# HTTP: a check gone missing below shows as a 2xx or a 500, never as a 422 from the framework.
async def test_a_request_outside_the_rules_answers_422_and_writes_nothing(
    fastapi_app: FastAPI, db_pool: AsyncConnectionPool
) -> None:
    too_long = "x" * (MAX_TITLE_LENGTH + 1)
    long_details = "x" * (MAX_DETAILS_LENGTH + 1)
    async with _serve(fastapi_app, ReferenceTaskRepository(db_pool)) as client:
        created = (await client.post("/reference-tasks", json={"title": "Original"})).json()
        path = f"/reference-tasks/{created['id']}"
        refused = [
            await client.post("/reference-tasks", json={"title": " "}),
            await client.post("/reference-tasks", json={"title": too_long}),
            await client.post("/reference-tasks", json={"title": "t", "details": long_details}),
            await client.patch(path, json={}),
            await client.patch(path, json={"title": None}),
            await client.patch(path, json={"title": too_long}),
            await client.patch(path, json={"details": long_details}),
            await client.patch(path, json={"status": None}),
            await client.patch(path, json={"status": "archived"}),
            await client.get("/reference-tasks", params={"status": "archived"}),
            await client.get("/reference-tasks", params={"limit": 0}),
            await client.get("/reference-tasks", params={"limit": MAX_LIST_LIMIT + 1}),
        ]
        stored = (await client.get("/reference-tasks")).json()

    assert [response.status_code for response in refused] == [422] * len(refused)
    assert stored["items"] == [created]
    # Each refusal names its field, as a 422 from the framework did while the DTOs held the bounds.
    fields = [
        [detail["field"] for detail in response.json()["error"].get("details", [])]
        for response in refused
    ]
    assert fields == (
        [["title"]] * 2
        + [["details"]]
        + [[]]
        + [["title"]] * 2
        + [["details"]]
        + [["status"]] * 3
        + [["limit"]] * 2
    )


# Verify a patch touches only its fields, moves the token, clears on null, and does not block the next.
# Three patches in a row, because the defects that break the token show on the second: a
# token that never moves, a write conditioned on the new timestamp, or a mapper that swaps the two
# stamps each leave the first patch looking fine and answer 409 to the next.
async def test_each_patch_changes_what_it_sent_and_the_next_one_still_lands(
    fastapi_app: FastAPI, db_pool: AsyncConnectionPool
) -> None:
    async with _serve(fastapi_app, ReferenceTaskRepository(db_pool)) as client:
        created = (
            await client.post("/reference-tasks", json={"title": "Original", "details": "Keep me"})
        ).json()
        path = f"/reference-tasks/{created['id']}"
        started = await client.patch(path, json={"status": "in_progress"})
        cleared = await client.patch(path, json={"details": None})
        longest = await client.patch(path, json={"title": "x" * MAX_TITLE_LENGTH})
        stored = (await client.get(path)).json()

    assert [r.status_code for r in (started, cleared, longest)] == [200, 200, 200]
    assert started.json()["title"] == "Original" and started.json()["details"] == "Keep me"
    assert datetime.fromisoformat(started.json()["updated_at"]) > datetime.fromisoformat(
        created["updated_at"]
    )
    assert cleared.json()["details"] is None and cleared.json()["status"] == "in_progress"
    assert stored == longest.json() and stored["details"] is None


async def _somebody_writes(pool: AsyncConnectionPool, task: ReferenceTask) -> None:
    other = replace(
        task, title="Somebody else's", updated_at=task.updated_at + timedelta(seconds=1)
    )
    await ReferenceTaskRepository(pool).update(other, expected_updated_at=task.updated_at)


async def _somebody_deletes(pool: AsyncConnectionPool, task: ReferenceTask) -> None:
    async with pool.connection() as connection:
        await connection.execute("DELETE FROM reference_tasks WHERE id = %s", (task.id,))


# From inside the UPDATE a row written since the read and a deleted one are the same zero rows; the
# service's second read tells the caller which — 409, re-read and retry, or 404, the row is gone.
@pytest.mark.parametrize(
    ("act", "status", "title_after"),
    [(_somebody_writes, 409, "Somebody else's"), (_somebody_deletes, 404, None)],
)
async def test_a_patch_whose_row_changed_since_the_read_answers_409_or_404(
    fastapi_app: FastAPI,
    db_pool: AsyncConnectionPool,
    act: Callable[[AsyncConnectionPool, ReferenceTask], Awaitable[None]],
    status: int,
    title_after: str | None,
) -> None:
    async with _serve(fastapi_app, ReferenceTaskRepository(db_pool)) as client:
        task_id = (await client.post("/reference-tasks", json={"title": "Original"})).json()["id"]
    racing = _SomebodyElseActsAfterTheRead(db_pool, lambda task: act(db_pool, task))
    async with _serve(fastapi_app, racing) as client:
        response = await client.patch(f"/reference-tasks/{task_id}", json={"title": "Mine"})
        after = await client.get(f"/reference-tasks/{task_id}")

    assert response.status_code == status and response.json()["error"]["status_code"] == status
    assert (after.json()["title"] if after.status_code == 200 else None) == title_after


# Verify every path to a second open title — create, rename, reopen by status alone — is refused.
# The status-only PATCH is the one bench2's service-side check never looked at: it validated a
# title when one was sent, and reopening a closed task sends none. The index sees the row, not the
# request, so every path is covered by the same rule.
async def test_a_request_that_would_open_a_second_task_with_a_title_answers_409(
    fastapi_app: FastAPI, db_pool: AsyncConnectionPool
) -> None:
    async with _serve(fastapi_app, ReferenceTaskRepository(db_pool)) as client:
        closed = (await client.post("/reference-tasks", json={"title": "Plan"})).json()["id"]
        await client.patch(f"/reference-tasks/{closed}", json={"status": "done"})
        reused = await client.post("/reference-tasks", json={"title": "PLAN"})
        other = (await client.post("/reference-tasks", json={"title": "Other"})).json()["id"]
        refused = [
            await client.post("/reference-tasks", json={"title": "plan"}),
            await client.patch(f"/reference-tasks/{other}", json={"title": "Plan"}),
            await client.patch(f"/reference-tasks/{closed}", json={"status": "pending"}),
        ]
        after = [(await client.get(f"/reference-tasks/{i}")).json() for i in (closed, other)]

    assert reused.status_code == 201
    assert [response.status_code for response in refused] == [409, 409, 409]
    assert refused[0].json()["error"]["status_code"] == 409
    assert (after[0]["status"], after[1]["title"]) == ("done", "Other")
