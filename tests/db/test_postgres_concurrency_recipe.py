# FILE: tests/db/test_postgres_concurrency_recipe.py
# SUMMARY: Exercise an exclusion constraint behind competing HTTP requests and a per-room lock.

import asyncio

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from psycopg.errors import ExclusionViolation
from psycopg_pool import AsyncConnectionPool


async def test_one_insert_wins_and_the_other_is_an_http_conflict(
    db_pool: AsyncConnectionPool,
) -> None:
    async with db_pool.connection() as connection:
        # The extension belongs to the database. A feature rollback drops only its own tables.
        await connection.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        await connection.execute("CREATE TABLE recipe_room (id integer PRIMARY KEY)")
        await connection.execute("CREATE INDEX recipe_room_gist ON recipe_room USING gist (id)")
    try:
        async with db_pool.connection() as connection:
            await connection.execute("""
                CREATE TABLE recipe_booking (
                    room_id integer NOT NULL REFERENCES recipe_room,
                    slot int4range NOT NULL,
                    CONSTRAINT recipe_no_overlap EXCLUDE USING gist (room_id WITH =, slot WITH &&)
                )
            """)
            await connection.execute("INSERT INTO recipe_room VALUES (1)")
        app = FastAPI()
        ready = asyncio.Barrier(2)

        @app.post("/rooms/{room_id}/bookings", status_code=201)
        async def book(room_id: int) -> dict[str, int]:
            async with db_pool.connection() as connection:
                await ready.wait()
                try:
                    async with connection.transaction():
                        await connection.execute("SET LOCAL lock_timeout = '3s'")
                        parent = await connection.execute(
                            "SELECT id FROM recipe_room WHERE id = %s FOR NO KEY UPDATE", (room_id,)
                        )
                        if await parent.fetchone() is None:
                            raise HTTPException(status_code=404)
                        await connection.execute(
                            "INSERT INTO recipe_booking VALUES (%s, int4range(10, 11, '[)'))",
                            (room_id,),
                        )
                except ExclusionViolation as error:
                    if error.diag.constraint_name != "recipe_no_overlap":
                        raise
                    raise HTTPException(status_code=409, detail="overlapping booking") from error
            return {"room_id": room_id}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first, second = await asyncio.wait_for(
                asyncio.gather(
                    client.post("/rooms/1/bookings"),
                    client.post("/rooms/1/bookings"),
                ),
                timeout=10,
            )
        assert sorted((first.status_code, second.status_code)) == [201, 409]
        async with db_pool.connection() as connection:
            result = await connection.execute("SELECT count(*) FROM recipe_booking")
            assert await result.fetchone() == (1,)
            await connection.execute(
                "ALTER TABLE recipe_booking ADD COLUMN cancelled_at timestamptz"
            )
            migrated = await connection.execute("SELECT cancelled_at FROM recipe_booking")
            assert await migrated.fetchone() == (None,)
    finally:
        async with db_pool.connection() as connection:
            await connection.execute("DROP TABLE IF EXISTS recipe_booking")
            index = await connection.execute("SELECT to_regclass('recipe_room_gist')::text")
            assert await index.fetchone() == ("recipe_room_gist",)
            await connection.execute("DROP TABLE recipe_room")
