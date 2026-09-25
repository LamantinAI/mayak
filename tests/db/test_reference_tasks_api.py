# FILE: tests/db/test_reference_tasks_api.py
# SUMMARY: The reference vertical over HTTP, in process, on the real repository: status codes, filters, conflicts.
# NOTE: The application is the assembled one (CompositionRoot through the `fastapi_app` fixture) with
# its service pointed at the db tier's pool, and requests go through ASGI on the tier's event loop —
# a TestClient would run the app on a loop of its own, where the pool cannot be used. What this adds
# over tests/db/test_reference_task_repository.py is the service and the endpoint on top of real
# rows: which fields a PATCH touches, what an explicit null does, and what a race looks like to a
# client. The built image, its entrypoint and its migrations are left to tests/functional.

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from psycopg_pool import AsyncConnectionPool

from project.application.reference_task_service import MAX_LIST_LIMIT, ReferenceTaskService
from project.domain.reference_task import MAX_TITLE_LENGTH, ReferenceTask
from project.infrastructure.persistence.reference_task_repository import ReferenceTaskRepository


# FUNCTION: _serve
# SUMMARY: An HTTP client for the assembled application, its reference service over `repository`.
@asynccontextmanager
async def _serve(app: FastAPI, repository: ReferenceTaskRepository) -> AsyncIterator[AsyncClient]:
    app.state.services["reference_task_service"] = ReferenceTaskService(repository=repository)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# CLASS: tests.db.test_reference_tasks_api._SomebodyElseActsAfterTheRead
# SUMMARY: The real repository, except that another writer acts once, right after the first read.
# NOTE: The race staged where it happens: the service holds a row the table no longer has. Real SQL
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

        # **LOGIC_STEP**: A status other than the default. Asked only for the default, an endpoint
        # that ignored the parameter answered the same page, and only the 422 tests noticed.
        by_status = {
            status: (await client.get("/reference-tasks", params={"status": status})).json()
            for status in ("done", "pending")
        }
        unknown = await client.get("/reference-tasks", params={"status": "archived"})
        too_long = await client.get("/reference-tasks", params={"limit": MAX_LIST_LIMIT + 1})
        blank = await client.post("/reference-tasks", json={"title": " "})

    assert [item["id"] for item in by_status["done"]["items"]] == [done] and by_status["done"][
        "count"
    ] == 1
    assert [item["id"] for item in by_status["pending"]["items"]] == [kept]
    assert (unknown.status_code, too_long.status_code, blank.status_code) == (422, 422, 422)


# FUNCTION: test_each_patch_changes_what_it_sent_and_the_next_one_still_lands
# SUMMARY: Verify a patch touches only its fields, moves the token, clears on null, and does not block the next.
# NOTE: Three patches in a row, because the defects that break the token show on the second: a
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
        empty = await client.patch(path, json={})
        stored = (await client.get(path)).json()

    assert [r.status_code for r in (started, cleared, longest, empty)] == [200, 200, 200, 422]
    assert started.json()["title"] == "Original" and started.json()["details"] == "Keep me"
    assert datetime.fromisoformat(started.json()["updated_at"]) > datetime.fromisoformat(
        created["updated_at"]
    )
    assert cleared.json()["details"] is None and cleared.json()["status"] == "in_progress"
    assert stored == longest.json() and stored["details"] is None


async def test_a_patch_of_a_task_somebody_else_wrote_since_the_read_answers_409(
    fastapi_app: FastAPI, db_pool: AsyncConnectionPool
) -> None:
    async def write_first(task: ReferenceTask) -> None:
        other = replace(
            task,
            title="Written by somebody else",
            updated_at=task.updated_at + timedelta(seconds=1),
        )
        await ReferenceTaskRepository(db_pool).update(other, expected_updated_at=task.updated_at)

    async with _serve(fastapi_app, ReferenceTaskRepository(db_pool)) as client:
        task_id = (await client.post("/reference-tasks", json={"title": "Original"})).json()["id"]
    async with _serve(fastapi_app, _SomebodyElseActsAfterTheRead(db_pool, write_first)) as client:
        response = await client.patch(f"/reference-tasks/{task_id}", json={"title": "Mine"})
        stored = (await client.get(f"/reference-tasks/{task_id}")).json()

    assert response.status_code == 409 and response.json()["error"]["status_code"] == 409
    assert stored["title"] == "Written by somebody else"


async def test_a_patch_of_a_task_deleted_since_the_read_answers_404_not_409(
    fastapi_app: FastAPI, db_pool: AsyncConnectionPool
) -> None:
    # **LOGIC_STEP**: From inside the UPDATE a moved row and a deleted one are the same zero rows;
    # only the service's second read tells them apart, and telling the caller of a deleted task to
    # re-read and retry sends them after a row that will never come back.
    async def delete_first(task: ReferenceTask) -> None:
        async with db_pool.connection() as connection:
            await connection.execute("DELETE FROM reference_tasks WHERE id = %s", (task.id,))

    async with _serve(fastapi_app, ReferenceTaskRepository(db_pool)) as client:
        task_id = (await client.post("/reference-tasks", json={"title": "Original"})).json()["id"]
    async with _serve(fastapi_app, _SomebodyElseActsAfterTheRead(db_pool, delete_first)) as client:
        response = await client.patch(f"/reference-tasks/{task_id}", json={"title": "Mine"})

    assert response.status_code == 404


# FUNCTION: test_a_request_that_would_open_a_second_task_with_a_title_answers_409
# SUMMARY: Verify every path to a second open title — create, rename, reopen by status alone — is refused.
# NOTE: The status-only PATCH is the one bench2's service-side check never looked at: it validated a
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
