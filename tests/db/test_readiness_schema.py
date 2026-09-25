# FILE: tests/db/test_readiness_schema.py
# SUMMARY: Readiness against a real PostgreSQL: a schema at head, emptied by a downgrade, one migration behind, newer than the code.
# The probe's SQL runs here for real — ADR-010. Each case rewrites alembic_version's rows and puts
# the session's own back afterwards: the tier's truncation leaves that table alone on purpose.

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from psycopg_pool import AsyncConnectionPool

from project.infrastructure.api.endpoints import health

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "alembic"


async def _write_revisions(pool: AsyncConnectionPool, revisions: Iterable[str]) -> None:
    async with pool.connection() as connection:
        await connection.execute("DELETE FROM alembic_version")
        for revision in revisions:
            await connection.execute(
                "INSERT INTO alembic_version (version_num) VALUES (%s)", (revision,)
            )


@asynccontextmanager
async def _schema_recorded_at(
    pool: AsyncConnectionPool, revisions: Iterable[str]
) -> AsyncIterator[None]:
    async with pool.connection() as connection:
        cursor = await connection.execute("SELECT version_num FROM alembic_version")
        saved = [row[0] for row in await cursor.fetchall()]
    await _write_revisions(pool, revisions)
    try:
        yield
    finally:
        await _write_revisions(pool, saved)


def _one_before_head() -> str:
    script = ScriptDirectory(str(_MIGRATIONS_DIR))
    (head,) = script.get_heads()
    previous = script.get_revision(head).down_revision
    if not isinstance(previous, str):
        pytest.skip("the migration chain has a single revision; nothing is one behind it")
    return previous


async def test_a_database_at_head_is_ready(db_pool: AsyncConnectionPool) -> None:
    result = await health._check_database(db_pool)

    assert result["status"] == "healthy"


# What `alembic downgrade base` leaves: every table gone, alembic_version kept and empty.
async def test_an_emptied_version_table_is_not_migrated(db_pool: AsyncConnectionPool) -> None:
    async with _schema_recorded_at(db_pool, []):
        result = await health._check_database(db_pool)

    assert result["status"] == "unhealthy"
    assert "not migrated" in result["message"]


async def test_a_schema_one_migration_behind_is_not_ready(db_pool: AsyncConnectionPool) -> None:
    async with _schema_recorded_at(db_pool, [_one_before_head()]):
        result = await health._check_database(db_pool)

    assert result["status"] == "unhealthy"
    assert "behind" in result["message"]


# A rolling deploy's old replicas meet the new schema; unready, they would all leave rotation.
async def test_a_schema_newer_than_the_code_stays_ready(db_pool: AsyncConnectionPool) -> None:
    async with _schema_recorded_at(db_pool, ["a_revision_from_a_newer_deploy"]):
        result = await health._check_database(db_pool)

    assert result["status"] == "healthy"
    assert result["schema_revision"] == ["a_revision_from_a_newer_deploy"]
