# FILE: tests/integration/test_exception_to_http_status.py
# SUMMARY: End-to-end mapping of ProjectError subclasses to HTTP status codes via the FastAPI exception handler pipeline.

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from project.domain.exceptions import (
    AuthenticationError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    UpstreamAuthenticationError,
    ValidationError,
)


def _wire_raising_route(app: FastAPI, path: str, exc: Exception) -> None:
    """Register a route on the assembled app that raises the given exception."""

    async def _raise() -> dict[str, str]:
        raise exc

    app.add_api_route(path, _raise, methods=["GET"])


# CLASS: tests.integration.test_exception_to_http_status.TestExceptionToHTTPStatus
# SUMMARY: Each ProjectError subclass maps to its declared HTTP status code via the FastAPI exception handler.
class TestExceptionToHTTPStatus:
    # FUNCTION: test_validation_error_maps_to_422
    @pytest.mark.integration
    async def test_validation_error_maps_to_422(
        self, fastapi_app: FastAPI, async_client: AsyncClient
    ) -> None:
        _wire_raising_route(fastapi_app, "/__test_raise_validation", ValidationError("bad input"))
        response = await async_client.get("/__test_raise_validation")
        assert response.status_code == 422

    # FUNCTION: test_not_found_error_maps_to_404
    @pytest.mark.integration
    async def test_not_found_error_maps_to_404(
        self, fastapi_app: FastAPI, async_client: AsyncClient
    ) -> None:
        _wire_raising_route(fastapi_app, "/__test_raise_not_found", NotFoundError("missing"))
        response = await async_client.get("/__test_raise_not_found")
        assert response.status_code == 404

    # FUNCTION: test_conflict_error_maps_to_409
    @pytest.mark.integration
    async def test_conflict_error_maps_to_409(
        self, fastapi_app: FastAPI, async_client: AsyncClient
    ) -> None:
        _wire_raising_route(fastapi_app, "/__test_raise_conflict", ConflictError("conflict"))
        response = await async_client.get("/__test_raise_conflict")
        assert response.status_code == 409

    # FUNCTION: test_external_service_error_maps_to_502
    @pytest.mark.integration
    async def test_external_service_error_maps_to_502(
        self, fastapi_app: FastAPI, async_client: AsyncClient
    ) -> None:
        _wire_raising_route(
            fastapi_app, "/__test_raise_external", ExternalServiceError("provider unavailable")
        )
        response = await async_client.get("/__test_raise_external")
        assert response.status_code == 502

    # FUNCTION: test_upstream_authentication_error_maps_to_502_not_401
    # SUMMARY: A provider refusing OUR key is an upstream failure, and the caller is told so.
    # NOTE: The distinction this pins is who is being asked to act. 401 tells the caller their own
    # credentials are wrong and invites them to re-authenticate; a dead provider key is not
    # something they can re-authenticate their way out of, and the message must not describe our
    # configuration to them either. The type stays distinct for our logs and tests; the answer on
    # the wire is the same 502 any other upstream failure gets.
    @pytest.mark.integration
    async def test_upstream_authentication_error_maps_to_502_not_401(
        self, fastapi_app: FastAPI, async_client: AsyncClient
    ) -> None:
        _wire_raising_route(
            fastapi_app,
            "/__test_raise_upstream_auth",
            UpstreamAuthenticationError("LLM provider rejected this service's credentials"),
        )

        response = await async_client.get("/__test_raise_upstream_auth")

        assert response.status_code == 502
        # **LOGIC_STEP**: And the wording the caller sees is the generic upstream one, not the
        # exception's own text — error_utils only lets the caller-facing error types speak for
        # themselves, and this is not one of them.
        assert "credentials" not in response.text

    # FUNCTION: test_authentication_error_maps_to_401
    @pytest.mark.integration
    async def test_authentication_error_maps_to_401(
        self, fastapi_app: FastAPI, async_client: AsyncClient
    ) -> None:
        _wire_raising_route(fastapi_app, "/__test_raise_auth", AuthenticationError("no token"))
        response = await async_client.get("/__test_raise_auth")
        assert response.status_code == 401
