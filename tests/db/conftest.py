# FILE: tests/db/conftest.py
# SUMMARY: The db tier: tests that run real SQL against a real PostgreSQL, inside `make test`.
# NOTE: Why this tier exists. Before it, the fast suite could see a query only as text: a test
# pinned the clause as a literal string (ADR-010) and anything the pin did not spell out — an
# INSERT column list, a migration that disagreed with the ORM — was caught by `make test-e2e`
# alone, which builds two images and which agents run last if at all. Measured on the reference
# vertical (the mutation baseline in docs/mutations/, 2026-09-24): six SQL defects were caught in the
# fast suite only by a text pin, two only in e2e. Here the query runs, so it is judged by what
# it returns.
#
# What a test in this directory gets:
# - its own PostgreSQL, started for this checkout by tests/db/stack.py (or TEST_DATABASE_URL);
# - a database created empty for the session and migrated with `alembic upgrade head`, then
#   `alembic check` run against it — the same commands scripts/validate_migrations.py runs, so a
#   migration is verified here, from nothing, the way a first deploy runs it;
# - `db_pool`: a pool over that schema with every application table emptied first.
# The tier never touches the application's own database. With POSTGRES_ENABLED=false the project
# has declared it has no database, and these tests are deselected — saying so in the summary.

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import AsyncConnectionPool
from pytest_asyncio import is_async_test

from scripts.validate_migrations import (
    ROOT_DIR,
    MigrationIssue,
    collect_migration_issues,
    postgres_is_enabled,
)

from . import stack

# ATTRIBUTE: _TIER_DIR (Path)
# SUMMARY: This directory; a collected test under it belongs to the db tier.
_TIER_DIR = Path(__file__).resolve().parent


# ATTRIBUTE: _DESELECTED_KEY (pytest.StashKey[int])
# SUMMARY: How many db-tier tests were deselected, for the summary line.
_DESELECTED_KEY = pytest.StashKey[int]()


# FUNCTION: _environment
# SUMMARY: Run a block with extra environment variables, then restore the environment exactly.
# NOTE: Exactly, not just the keys set here: postgres_is_enabled() and the migration gate both call
# load_dotenv, which copies every key of .env into os.environ. Left in place, that would hand the
# rest of the suite whatever the developer's .env says, which the root conftest works to prevent.
@contextmanager
def _environment(overrides: dict[str, str]) -> Iterator[None]:
    saved = dict(os.environ)
    os.environ.update(overrides)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


# FUNCTION: pytest_collection_modifyitems
# SUMMARY: Mark the tier's tests, run them on one event loop, or deselect them when the project has no database.
# NOTE: Decided here, at collection, because tests/conftest.py pins POSTGRES_ENABLED=true inside
# every test — by the time a fixture runs, the project's own answer is no longer visible.
# The session event loop is what lets one connection pool serve every test in the tier; a pool
# opened on one loop cannot be used from another.
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    tier = [item for item in items if _TIER_DIR in Path(item.path).resolve().parents]
    if not tier:
        return
    with _environment({}):
        enabled = postgres_is_enabled()
    if not enabled:
        config.hook.pytest_deselected(items=tier)
        items[:] = [item for item in items if item not in tier]
        config.stash[_DESELECTED_KEY] = len(tier)
        return
    session_loop = pytest.mark.asyncio(loop_scope="session")
    for item in tier:
        item.add_marker(pytest.mark.db)
        if is_async_test(item):
            item.add_marker(session_loop, append=False)


# FUNCTION: pytest_terminal_summary
# SUMMARY: Say that the db tier did not run and why, instead of letting the count shrink silently.
def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    deselected = terminalreporter.config.stash.get(_DESELECTED_KEY, 0)
    if deselected:
        terminalreporter.write_line(
            f"db tier: {deselected} tests not run — POSTGRES_ENABLED=false declares no database"
        )


# FUNCTION: database_url
# SUMMARY: Start or find the tier's PostgreSQL and give the session a new, empty database on it.
# OUTPUT: (str): Connection URL of the database the tier owns.
@pytest.fixture(scope="session")
def database_url() -> str:
    # **LOGIC_STEP**: Failed outside the `except`, so the report is the explanation alone rather
    # than the same text twice under "During handling of the above exception".
    failure: str | None
    try:
        url = stack.ensure_up()
    except RuntimeError as exc:
        failure = str(exc)
    else:
        failure = _recreate_database(url)
    if failure:
        pytest.fail(failure, pytrace=False)
    return url


# ATTRIBUTE: _DATABASE_SUFFIX (str)
# SUMMARY: Suffix a database name must carry before this tier will drop it.
_DATABASE_SUFFIX = "_test"


# FUNCTION: _recreate_database
# SUMMARY: Drop and create the URL's database, so every session migrates from nothing.
# OUTPUT: (str | None): Why it could not be done, or None when the database is new and empty.
# NOTE: From nothing, every session. The first version kept the database between runs and only
# upgraded it, and the mutation baseline caught what that costs: an edit to a migration that had
# already run — String(200) narrowed to String(100) in 001 — left `upgrade head` with nothing to
# do and `alembic check` comparing the models against the old column, green. A migration is
# verified only by running it on an empty database, which is also what production's first deploy
# does. Two migrations take about a second; a project with a long history pays more, and squashing
# is the answer to that, not a database that remembers.
#
# The suffix check is the guard on TEST_DATABASE_URL: this drops a database, and a URL copied from
# .env by mistake must fail here rather than empty somebody's data.
def _recreate_database(url: str) -> str | None:
    params = conninfo_to_dict(url)
    name = str(params["dbname"])
    if not name.endswith(_DATABASE_SUFFIX):
        return (
            f"The db tier drops and recreates its database every session, so it refuses "
            f"{name!r}: the name must end in {_DATABASE_SUFFIX!r}. Point TEST_DATABASE_URL at a "
            "database that exists only for these tests."
        )
    try:
        with psycopg.connect(
            make_conninfo(url, dbname="postgres"), autocommit=True, connect_timeout=10
        ) as connection:
            database = sql.Identifier(name)
            connection.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(database))
            connection.execute(sql.SQL("CREATE DATABASE {}").format(database))
    except psycopg.OperationalError as exc:
        return (
            f"The db tier cannot reach PostgreSQL at {params.get('host')}:{params.get('port')}: "
            f"{exc}\nFor this checkout's own database leave TEST_DATABASE_URL unset: the tier "
            "starts one (`uv run python tests/db/stack.py up` does the same by hand)."
        )
    return None


# FUNCTION: migration_issues
# SUMMARY: Build the schema with `alembic upgrade head`, then run `alembic check` against it.
# OUTPUT: (list[MigrationIssue]): What `alembic check` reported; empty when head matches the ORM.
# NOTE: Through scripts/validate_migrations.py, the gate's own implementation, pointed at this
# database — not a second way of running Alembic that could disagree with the gate. A failed
# upgrade stops the tier here, since no test can run without the schema; a failed check does not,
# and test_migrations_match_models.py reports it as the one red test it is.
@pytest.fixture(scope="session")
def migration_issues(database_url: str) -> list[MigrationIssue]:
    params = conninfo_to_dict(database_url)
    with _environment(
        {
            "POSTGRES_ENABLED": "true",
            "POSTGRES_HOST": str(params.get("host", "localhost")),
            "POSTGRES_PORT": str(params.get("port", "5432")),
            "POSTGRES_USER": str(params.get("user", "")),
            "POSTGRES_PASSWORD": str(params.get("password", "")),
            "POSTGRES_DB": str(params["dbname"]),
        }
    ):
        issues = collect_migration_issues(ROOT_DIR, require_database=True)
    blocking = [
        issue for issue in issues if issue.command_name != "check" and issue.severity == "error"
    ]
    if blocking:
        pytest.fail(
            "The db tier could not build the schema:\n"
            + "\n".join(f"{issue.rule_id}: {issue.message}" for issue in blocking),
            pytrace=False,
        )
    return issues


# FUNCTION: _session_pool
# SUMMARY: One pool for the whole tier, configured like the application's own.
# OUTPUT: (AsyncGenerator[AsyncConnectionPool, None]): The open pool.
# NOTE: autocommit=True because project/core/composition_root.py opens the application's pool that
# way, and a repository tested on a pool that wraps every call in a transaction is tested under
# conditions it never meets. One pool per session, not per test: opening and closing a pool per test
# is what made a hand-rolled suite spend most of its time on teardown (bench2, 2026-09).
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _session_pool(
    database_url: str, migration_issues: list[MigrationIssue]
) -> AsyncGenerator[AsyncConnectionPool, None]:
    pool = AsyncConnectionPool(
        database_url, min_size=1, max_size=4, open=False, kwargs={"autocommit": True}
    )
    await pool.open(wait=True, timeout=30.0)
    try:
        yield pool
    finally:
        await pool.close()


# FUNCTION: _truncate_statement
# SUMMARY: One TRUNCATE naming every application table, built once the schema exists.
# OUTPUT: (sql.Composed | None): The statement, or None when the schema has no tables yet.
# NOTE: alembic_version is left alone: emptying it erases the record of which migrations ran, and
# the next session's upgrade then tries to create tables that already exist.
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _truncate_statement(_session_pool: AsyncConnectionPool) -> sql.Composed | None:
    async with _session_pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'alembic_version' ORDER BY tablename"
        )
        tables = [row[0] for row in await cursor.fetchall()]
    if not tables:
        return None
    return sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
        sql.SQL(", ").join(sql.Identifier(table) for table in tables)
    )


# FUNCTION: db_pool
# SUMMARY: The pool a db test works with, over a schema whose application tables are all empty.
# OUTPUT: (AsyncConnectionPool): The session pool, after one TRUNCATE.
# NOTE: Emptied before the test rather than after it, so a failing test leaves its rows behind for
# whoever opens the database to look.
@pytest_asyncio.fixture(loop_scope="session")
async def db_pool(
    _session_pool: AsyncConnectionPool, _truncate_statement: sql.Composed | None
) -> AsyncConnectionPool:
    if _truncate_statement is not None:
        async with _session_pool.connection() as connection:
            await connection.execute(_truncate_statement)
    return _session_pool
