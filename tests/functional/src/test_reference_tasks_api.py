# FILE: tests/functional/src/test_reference_tasks_api.py
# SUMMARY: Smoke test of the reference vertical in the built image: HTTP into the container, rows in its database.
# Everything a query or a status code can get wrong is tested in tests/db, in `make test`. What
# only this suite sees is the application as it ships — the image, the entrypoint's `alembic
# upgrade head`, the served routes — so one path through each is enough here.

import pytest
from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from utils.helpers import SendRequest

pytestmark = pytest.mark.e2e


async def test_a_task_created_over_http_is_stored_read_back_and_updated(
    make_post_request: SendRequest,
    make_get_request: SendRequest,
    make_patch_request: SendRequest,
    postgres_connection: AsyncConnection[TupleRow],
) -> None:
    created = await make_post_request(
        "/reference-tasks", {"title": "Smoke", "details": "Over HTTP"}
    )
    task_id = created["body"]["id"]
    loaded = await make_get_request(f"/reference-tasks/{task_id}")
    # A PATCH, because it writes updated_at — the column the second migration adds.
    # An image whose entrypoint stopped at the first migration answers this one with a 500.
    patched = await make_patch_request(f"/reference-tasks/{task_id}", {"status": "done"})

    async with postgres_connection.cursor() as cursor:
        await cursor.execute("SELECT title, status FROM reference_tasks WHERE id = %s", (task_id,))
        row = await cursor.fetchone()

    assert (created["status"], loaded["status"], patched["status"]) == (201, 200, 200)
    assert loaded["body"] == created["body"]
    assert row == ("Smoke", "done")
