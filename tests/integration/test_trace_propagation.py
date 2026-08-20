# FILE: tests/integration/test_trace_propagation.py
# SUMMARY: Verify the middleware extracts request id from inbound trace headers and echoes it.

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient


def _wire_ok_route(app: FastAPI, path: str) -> None:
    """Register a non-/health route on the assembled app so middleware runs unsampled."""

    async def _ok() -> dict[str, str]:
        return {"status": "ok"}

    app.add_api_route(path, _ok, methods=["GET"])


# CLASS: tests.integration.test_trace_propagation.TestTracePropagation
# SUMMARY: Trace headers supplied by the client surface in the response. Uses a non-/health endpoint
# because middleware short-circuits on /health when sampling decides not to log.
class TestTracePropagation:
    # FUNCTION: test_valid_uuid_request_id_echoed_in_response
    # SUMMARY: A UUID4-shaped X-Request-ID is preserved through the middleware to the response header.
    @pytest.mark.integration
    async def test_valid_uuid_request_id_echoed_in_response(
        self, fastapi_app: FastAPI, async_client: AsyncClient
    ) -> None:
        _wire_ok_route(fastapi_app, "/__trace_echo")
        trace_id = "12345678-1234-4abc-9def-0123456789ab"
        response = await async_client.get("/__trace_echo", headers={"X-Request-ID": trace_id})
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == trace_id

    # FUNCTION: test_traceparent_extracted_as_request_id
    # SUMMARY: A W3C traceparent header surfaces its 32-hex trace_id segment as the response X-Request-ID.
    @pytest.mark.integration
    async def test_traceparent_extracted_as_request_id(
        self, fastapi_app: FastAPI, async_client: AsyncClient
    ) -> None:
        _wire_ok_route(fastapi_app, "/__trace_w3c")
        traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        response = await async_client.get("/__trace_w3c", headers={"traceparent": traceparent})
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "0af7651916cd43dd8448eb211c80319c"
