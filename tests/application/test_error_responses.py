# FILE: tests/application/test_error_responses.py
# SUMMARY: What an error response carries: the headers its status asks for, a request id on a 500, a 500 for a misassembled app, a 413 for a body over the limit.

import uuid
from collections.abc import AsyncIterator, Generator

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient

from project.core.composition_root import CompositionRoot
from project.core.config import clear_settings_override, set_settings_override
from project.infrastructure.api.dependencies import _get_service
from project.infrastructure.api.exception_handlers import ExceptionHandlerManager
from tests.conftest import _FixtureSettings

_WELL_FORMED_ID = "an-inbound-request-id-0001"


# A client that gets the response a crash produces, as a real server would send it, instead of
# the exception re-raised into the test.
def _crash_tolerant(app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


class TestAnHttpExceptionKeepsItsHeaders:
    @pytest.mark.unit
    async def test_a_401_tells_the_client_how_to_authenticate(self, fastapi_app: FastAPI) -> None:
        async def _guarded() -> None:
            raise HTTPException(401, "sign in first", headers={"WWW-Authenticate": "Bearer"})

        fastapi_app.add_api_route("/__guarded", _guarded, methods=["GET"])
        async with _crash_tolerant(fastapi_app) as client:
            response = await client.get("/__guarded")

        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.unit
    async def test_a_429_says_when_to_retry(self, fastapi_app: FastAPI) -> None:
        async def _throttled() -> None:
            raise HTTPException(429, "slow down", headers={"Retry-After": "30"})

        fastapi_app.add_api_route("/__throttled", _throttled, methods=["GET"])
        async with _crash_tolerant(fastapi_app) as client:
            response = await client.get("/__throttled")

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "30"

    # Raised by Starlette's own router, so this goes through the Starlette handler, not FastAPI's.
    @pytest.mark.unit
    async def test_a_405_names_the_allowed_methods(self, async_client: AsyncClient) -> None:
        response = await async_client.post("/health/")

        assert response.status_code == 405
        assert "GET" in response.headers["Allow"]


class TestAFiveHundredCarriesItsRequestId:
    @pytest.mark.unit
    async def test_the_header_names_the_request_the_log_names(self, fastapi_app: FastAPI) -> None:
        async def _boom() -> None:
            raise RuntimeError("kaboom")

        fastapi_app.add_api_route("/__boom", _boom, methods=["GET"])
        async with _crash_tolerant(fastapi_app) as client:
            response = await client.get("/__boom", headers={"X-Request-ID": _WELL_FORMED_ID})

        assert response.status_code == 500
        assert response.headers["X-Request-ID"] == _WELL_FORMED_ID

    # A sampled-out health check skips the logging middleware, so nothing assigned it an id.
    @pytest.mark.unit
    async def test_a_request_the_middleware_skipped_gets_one_too(self) -> None:
        manager = ExceptionHandlerManager(FastAPI())
        request = Request({"type": "http", "method": "GET", "path": "/health", "headers": []})

        response = await manager._handle_generic_exception(request, RuntimeError("kaboom"))

        assert response.status_code == 500
        assert uuid.UUID(response.headers["X-Request-ID"]).version == 4


class TestAMisassembledAppIsAServerError:
    @pytest.mark.unit
    async def test_a_service_missing_from_the_registry_is_a_500(self, fastapi_app: FastAPI) -> None:
        def _missing(request: Request) -> object:
            return _get_service(request, "no_such_service", object)

        async def _route(service: object = Depends(_missing)) -> None:
            return None

        fastapi_app.add_api_route("/__misassembled", _route, methods=["GET"])
        async with _crash_tolerant(fastapi_app) as client:
            response = await client.get("/__misassembled")

        assert response.status_code == 500


_LIMIT = 64


class TestTheBodyLimit:
    # An app built with a small SERVER_MAX_BODY_BYTES, so the setting is what the middleware reads,
    # and a route that reads its body whole and reports its length.
    @pytest.fixture
    def small_limit_app(self) -> Generator[tuple[FastAPI, list[int]], None, None]:
        settings = _FixtureSettings()
        settings.server.max_body_bytes = _LIMIT
        set_settings_override(settings)
        read: list[int] = []
        try:
            app = CompositionRoot().build_application()

            async def _echo(request: Request) -> dict[str, int]:
                body = await request.body()
                read.append(len(body))
                return {"length": len(body)}

            app.add_api_route("/__echo", _echo, methods=["POST"])
            yield app, read
        finally:
            clear_settings_override()

    @pytest.mark.unit
    async def test_a_body_declared_over_the_limit_never_reaches_the_app(
        self, small_limit_app: tuple[FastAPI, list[int]]
    ) -> None:
        app, read = small_limit_app
        async with _crash_tolerant(app) as client:
            response = await client.post("/__echo", content=b"x" * (_LIMIT + 1))

        assert response.status_code == 413
        assert response.json()["error"]["status_code"] == 413
        assert uuid.UUID(response.headers["X-Request-ID"])
        assert read == []

    # No Content-Length: the limit holds while the body is read.
    @pytest.mark.unit
    async def test_a_chunked_body_over_the_limit_is_refused(
        self, small_limit_app: tuple[FastAPI, list[int]]
    ) -> None:
        async def _chunks() -> AsyncIterator[bytes]:
            for _ in range(3):
                yield b"x" * 40

        app, read = small_limit_app
        async with _crash_tolerant(app) as client:
            response = await client.post("/__echo", content=_chunks())

        assert response.status_code == 413
        assert read == []

    @pytest.mark.unit
    async def test_a_body_at_the_limit_is_read_whole(
        self, small_limit_app: tuple[FastAPI, list[int]]
    ) -> None:
        app, read = small_limit_app
        async with _crash_tolerant(app) as client:
            response = await client.post("/__echo", content=b"x" * _LIMIT)

        assert response.status_code == 200
        assert read == [_LIMIT]
