# FILE: tests/application/test_critical_event_trace_id.py
# SUMMARY: Regression guard: the unhandled-exception record must carry the request's trace_id.

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from project.core.logging.context import get_trace_id
from project.infrastructure.api import exception_handlers

_REQUEST_ID = "9f3ab2e4-e271-4f37-851f-e07a992ae3a7"


# CLASS: tests.application.test_critical_event_trace_id.TestCriticalEventTraceId
# SUMMARY: Verify trace correlation survives the unwind from the span to the exception handler.
class TestCriticalEventTraceId:
    # FUNCTION: test_critical_record_is_written_with_trace_id
    # SUMMARY: Verify the handler restores the trace context the middleware already reset.
    @pytest.mark.unit
    def test_critical_record_is_written_with_trace_id(
        self,
        fastapi_app: FastAPI,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # **LOGIC_STEP**: The middleware's `finally` resets the trace ContextVar while the
        # exception is still propagating outward, so by the time this handler runs the record
        # used to be written without a trace_id — and every trace reader drops such records,
        # which is why a 500 rendered as a tree with no cause in it.
        observed: dict[str, str | None] = {}
        original = exception_handlers.logger.log_critical

        def spy(*args: object, **kwargs: object) -> None:
            observed["trace_id"] = get_trace_id()
            original(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(exception_handlers.logger, "log_critical", spy)

        @fastapi_app.get("/_boom_trace_id")
        async def _boom() -> dict[str, str]:
            raise RuntimeError("kaboom")

        client = TestClient(fastapi_app, raise_server_exceptions=False)
        response = client.get("/_boom_trace_id", headers={"X-Request-ID": _REQUEST_ID})

        assert response.status_code == 500
        assert observed["trace_id"] == _REQUEST_ID
