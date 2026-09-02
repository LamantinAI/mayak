# FILE: tests/functional/src/test_migration_lock.py
# SUMMARY: Prove that two processes migrating the same fresh database at once both succeed.
# NOTE: This is the regression test for the defect measured on 2026-09-02: entrypoint.sh runs
# `alembic upgrade head` in every container, so two replicas started together against a database
# that has never been migrated both tried to create `alembic_version`, and one died with
# `psycopg.errors.UniqueViolation` on `pg_type_typname_nsp_index`. Reproduced four times before the
# fix; compose ships no restart policy, so that replica stayed down.
#
# It has to live here and it has to spawn real processes. The lock is one line in alembic/env.py —
# a module that runs its migrations at import time, so no unit test can import it — and what is
# being asserted is that two SESSIONS serialise, which a single process cannot demonstrate at all.
# Removing the advisory lock leaves every other test in this repository green, this one included in
# spirit but not in fact: it is the only check that turns red.

import asyncio
import os
from typing import AsyncGenerator

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql

from settings import postgres_settings

# **LOGIC_STEP**: Marked at module level so `-m e2e` selects it with the rest of the suite.
pytestmark = pytest.mark.e2e

# ATTRIBUTE: _PROBE_DATABASE (str)
# SUMMARY: Throwaway database this test migrates from empty, so the race is the first-ever migration.
# NOTE: A fresh database, not the suite's own: the collision is over CREATE TABLE alembic_version,
# which only happens when nothing has migrated yet. The suite's database is already at head by the
# time any test runs, so racing there would prove nothing.
_PROBE_DATABASE = "mayak_migration_lock_probe"

# ATTRIBUTE: _REPLICAS (int)
# SUMMARY: How many processes migrate at once. Two is the reported topology; more only slows the test.
_REPLICAS = 2


# FUNCTION: _admin_connection
# SUMMARY: Open an autocommit connection to the suite's database for CREATE/DROP DATABASE.
# OUTPUT: (psycopg.AsyncConnection): Connection outside any transaction.
# NOTE: autocommit is required, not stylistic: PostgreSQL refuses CREATE DATABASE inside a
# transaction block, and psycopg opens one on the first statement otherwise.
async def _admin_connection() -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(postgres_settings.database_url, autocommit=True)


# FUNCTION: probe_database
# SUMMARY: Create an empty database for the race and drop it afterwards.
# OUTPUT: (AsyncGenerator[str, None]): Name of the database to migrate.
@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def probe_database() -> AsyncGenerator[str, None]:
    connection = await _admin_connection()
    try:
        await connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(_PROBE_DATABASE))
        )
        await connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(_PROBE_DATABASE))
        )
    finally:
        await connection.close()
    try:
        yield _PROBE_DATABASE
    finally:
        cleanup = await _admin_connection()
        try:
            await cleanup.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(_PROBE_DATABASE))
            )
        finally:
            await cleanup.close()


# FUNCTION: _run_alembic_upgrade
# SUMMARY: Run `alembic upgrade head` against the probe database in its own process.
# INPUT: database (str): Database name the process should migrate.
# OUTPUT: (tuple[int, str]): Exit code and the combined output, for the failure message.
async def _run_alembic_upgrade(database: str) -> tuple[int, str]:
    environment = dict(os.environ, POSTGRES_DB=database)
    process = await asyncio.create_subprocess_exec(
        "python",
        "-m",
        "alembic",
        "-c",
        "alembic.ini",
        "upgrade",
        "head",
        cwd="/app",
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    return process.returncode or 0, output.decode(errors="replace")


# FUNCTION: test_two_processes_migrating_at_once_both_succeed
# SUMMARY: Verify the advisory lock serialises concurrent migrations instead of letting one crash.
@pytest.mark.asyncio
async def test_two_processes_migrating_at_once_both_succeed(probe_database: str) -> None:
    results = await asyncio.gather(
        *(_run_alembic_upgrade(probe_database) for _ in range(_REPLICAS))
    )

    # **LOGIC_STEP**: Both codes, and the output of whichever failed. Without the lock exactly one
    # of these is 1 and its output carries the UniqueViolation, so printing it is what tells the
    # next reader that this is the migration race rather than a broken fixture.
    codes = [code for code, _ in results]
    assert codes == [0] * _REPLICAS, "\n\n".join(
        f"exit {code}:\n{output}" for code, output in results if code != 0
    )

    # **LOGIC_STEP**: Succeeding is not enough — the schema has to be at head afterwards. A lock
    # that made both processes skip the work quietly would satisfy the assertion above.
    connection = await psycopg.AsyncConnection.connect(
        postgres_settings.database_url.replace(f"/{postgres_settings.db}", f"/{probe_database}"),
        autocommit=True,
    )
    try:
        cursor = await connection.execute("SELECT count(*) FROM alembic_version")
        row = await cursor.fetchone()
    finally:
        await connection.close()

    assert row is not None and row[0] == 1
