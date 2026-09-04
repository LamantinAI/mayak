# FILE: tests/functional/conftest.py
# SUMMARY: Pytest fixtures for functional E2E tests providing database connections, HTTP clients, and request helpers.

from typing import Any, AsyncGenerator

import httpx
import pytest_asyncio
from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from settings import test_settings, postgres_settings
from utils.helpers import ApiResponse, LoadTestData, SendRequest


# FUNCTION: postgres_connection
# SUMMARY: Create a session-scoped async connection to the test PostgreSQL database.
# OUTPUT: (AsyncGenerator[AsyncConnection[TupleRow], None]): Async database connection.
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def postgres_connection() -> AsyncGenerator[AsyncConnection[TupleRow], None]:
    conn = await AsyncConnection.connect(
        host=postgres_settings.host,
        port=postgres_settings.port,
        user=postgres_settings.user,
        password=postgres_settings.password.get_secret_value(),
        dbname=postgres_settings.db,
    )
    yield conn
    await conn.close()


# ATTRIBUTE: _BOOKKEEPING_TABLES (frozenset[str])
# SUMMARY: Tables that belong to the migration tool, not to the application under test.
# NOTE: alembic_version was being truncated with everything else, which erases the record of
# which migrations ran. The application does not notice — its schema is still there — so this
# stayed invisible until something asked Alembic what state the database was in, and got
# "nothing applied" followed by CREATE TABLE against tables that already exist.
_BOOKKEEPING_TABLES = frozenset({"alembic_version"})


# FUNCTION: _truncate_application_tables
# SUMMARY: Empty every public table except the migration bookkeeping ones.
# INPUT: connection (AsyncConnection[TupleRow]): Open connection to the test database.
async def _truncate_application_tables(connection: AsyncConnection[TupleRow]) -> None:
    async with connection.cursor() as cur:
        await cur.execute("""
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
        """)
        tables = [row[0] for row in await cur.fetchall() if row[0] not in _BOOKKEEPING_TABLES]

    for table in tables:
        await connection.execute(f"TRUNCATE TABLE {table} CASCADE;")


# FUNCTION: clean_database
# SUMMARY: Truncate all public application tables before and after each test for isolation.
# INPUT: postgres_connection (AsyncConnection[TupleRow]): Database connection fixture.
# OUTPUT: (AsyncGenerator[None, None]): Yields control between setup and teardown.
@pytest_asyncio.fixture(scope="function", loop_scope="session", autouse=True)
async def clean_database(
    postgres_connection: AsyncConnection[TupleRow],
) -> AsyncGenerator[None, None]:
    await postgres_connection.execute("SET session_replication_role = replica;")

    await _truncate_application_tables(postgres_connection)

    await postgres_connection.execute("SET session_replication_role = DEFAULT;")
    await postgres_connection.commit()

    yield

    await _truncate_application_tables(postgres_connection)

    await postgres_connection.commit()


# FUNCTION: http_client
# SUMMARY: Create a session-scoped async HTTP client for API requests.
# OUTPUT: (AsyncGenerator[httpx.AsyncClient, None]): Async HTTP client instance.
@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


# FUNCTION: make_post_request
# SUMMARY: Fixture factory for making POST requests to the test API.
# INPUT: http_client (httpx.AsyncClient): HTTP client fixture.
# OUTPUT: (Callable): Async function that sends POST and returns status/body dict.
@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def make_post_request(http_client: httpx.AsyncClient) -> SendRequest:
    async def inner(
        path: str, data: dict[str, Any] = {}, headers: dict[str, str] = {}
    ) -> ApiResponse:
        url = test_settings.service_url + path
        response = await http_client.post(url, json=data, headers=headers)
        body = (
            response.json()
            if response.content
            and response.headers.get("content-type", "").startswith("application/json")
            else response.text
        )
        return {"status": response.status_code, "body": body}

    return inner


# FUNCTION: make_get_request
# SUMMARY: Fixture factory for making GET requests to the test API.
# INPUT: http_client (httpx.AsyncClient): HTTP client fixture.
# OUTPUT: (Callable): Async function that sends GET and returns status/body dict.
@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def make_get_request(http_client: httpx.AsyncClient) -> SendRequest:
    async def inner(path: str, params: dict[str, Any] = {}) -> ApiResponse:
        url = test_settings.service_url + path
        response = await http_client.get(url, params=params)
        body = (
            response.json()
            if response.content
            and response.headers.get("content-type", "").startswith("application/json")
            else response.text
        )
        return {"status": response.status_code, "body": body}

    return inner


# FUNCTION: make_put_request
# SUMMARY: Fixture factory for making PUT requests to the test API.
# INPUT: http_client (httpx.AsyncClient): HTTP client fixture.
# OUTPUT: (Callable): Async function that sends PUT and returns status/body dict.
@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def make_put_request(http_client: httpx.AsyncClient) -> SendRequest:
    async def inner(
        path: str, data: dict[str, Any] = {}, headers: dict[str, str] = {}
    ) -> ApiResponse:
        url = test_settings.service_url + path
        response = await http_client.put(url, json=data, headers=headers)
        body = (
            response.json()
            if response.content
            and response.headers.get("content-type", "").startswith("application/json")
            else response.text
        )
        return {"status": response.status_code, "body": body}

    return inner


# FUNCTION: make_patch_request
# SUMMARY: Fixture factory for making PATCH requests to the test API.
# INPUT: http_client (httpx.AsyncClient): HTTP client fixture.
# OUTPUT: (Callable): Async function that sends PATCH and returns status/body dict.
# NOTE: The kernel shipped POST, GET, PUT and DELETE helpers but no PATCH, so a vertical whose
# edit route is a partial update had nothing to call and wrote its own client by hand. It lives
# here rather than in one vertical's test module: the next partial-update route would repeat it.
@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def make_patch_request(http_client: httpx.AsyncClient) -> SendRequest:
    async def inner(
        path: str, data: dict[str, Any] = {}, headers: dict[str, str] = {}
    ) -> ApiResponse:
        url = test_settings.service_url + path
        response = await http_client.patch(url, json=data, headers=headers)
        body = (
            response.json()
            if response.content
            and response.headers.get("content-type", "").startswith("application/json")
            else response.text
        )
        return {"status": response.status_code, "body": body}

    return inner


# FUNCTION: make_delete_request
# NOTE: `response.content` is checked before the content-type sniff, here and in the four helpers
# above. FastAPI answers a 204 route — a handler returning `None` with `status_code=204`, which is
# what a DELETE endpoint usually is — with an EMPTY body and a `Content-Type: application/json`
# header, and `response.json()` on zero bytes raises `json.decoder.JSONDecodeError: Expecting
# value`, which reads like the caller's bug rather than this helper's. The reference vertical has
# no 204 route, so the first one a project adds is the first caller to reach it.
# SUMMARY: Fixture factory for making DELETE requests to the test API.
# INPUT: http_client (httpx.AsyncClient): HTTP client fixture.
# OUTPUT: (Callable): Async function that sends DELETE and returns status/body dict.
@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def make_delete_request(http_client: httpx.AsyncClient) -> SendRequest:
    async def inner(path: str) -> ApiResponse:
        url = test_settings.service_url + path
        response = await http_client.delete(url)
        body = (
            response.json()
            if response.content
            and response.headers.get("content-type", "").startswith("application/json")
            else response.text
        )
        return {"status": response.status_code, "body": body}

    return inner


# FUNCTION: load_test_data
# SUMMARY: Fixture factory for inserting test data into database tables.
# INPUT: postgres_connection (AsyncConnection[TupleRow]): Database connection fixture.
# OUTPUT: (Callable): Async function that inserts rows into the specified table.
@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def load_test_data(postgres_connection: AsyncConnection[TupleRow]) -> LoadTestData:
    async def inner(table_name: str, data: list[dict[str, Any]]) -> None:
        if not data:
            return

        columns = list(data[0].keys())
        placeholders = ", ".join([f"%({col})s" for col in columns])
        columns_str = ", ".join(columns)

        query = f"""
            INSERT INTO {table_name} ({columns_str})
            VALUES ({placeholders})
        """

        async with postgres_connection.cursor() as cur:
            await cur.executemany(query, data)
        await postgres_connection.commit()

    return inner
