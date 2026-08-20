# FILE: tests/application/test_request_summary_outcome.py
# SUMMARY: Regression tests proving request.summary reports the real result of a request.
#
# These exist because a boolean `success` reported true for every handled error. Starlette turns a
# handled exception into a Response inside ExceptionMiddleware, which sits below AILoggingMiddleware,
# so the logging span never saw the exception. A reader filtering the log for failures found none
# even while an upstream service returned 502 all day. A unit test of the classifier alone would not
# have caught that — the lie lived in the wiring — so these cases drive the real ASGI stack.

import asyncio
from typing import Any, AsyncGenerator, Optional

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from project.core.logging.enums import RequestOutcome
from project.core.logging.logger import classify_request_outcome
from project.domain.exceptions import ExternalServiceError, NotFoundError


# CLASS: tests.application.test_request_summary_outcome._Payload
# SUMMARY: Request body used to trigger FastAPI's own validation failure.
class _Payload(BaseModel):
    number: int


# FUNCTION: _probe_router
# SUMMARY: Build a router whose endpoints raise each error shape the classifier has to tell apart.
def _probe_router() -> APIRouter:
    router = APIRouter()

    @router.post("/probe/validate")
    async def _validate(payload: _Payload) -> dict[str, int]:
        return {"number": payload.number}

    @router.get("/probe/missing")
    async def _missing() -> dict[str, str]:
        raise NotFoundError("no such thing")

    @router.get("/probe/upstream")
    async def _upstream() -> dict[str, str]:
        raise ExternalServiceError("the model provider timed out")

    @router.get("/probe/crash")
    async def _crash() -> dict[str, str]:
        raise RuntimeError("unhandled")

    return router


# FUNCTION: _last_summary
# SUMMARY: Return the payload of the most recent request.summary entry in a log_capture list.
def _last_summary(captured: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = [
        entry["kwargs"].get("data") or {}
        for entry in captured
        if entry["kwargs"].get("event_id") == "request.summary"
    ]
    assert summaries, "no request.summary event was emitted"
    return dict(summaries[-1])


# FUNCTION: probe_client
# SUMMARY: Drive the real application stack in-process, with app exceptions surfaced as 500s.
@pytest.fixture
async def probe_client(fastapi_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    # **LOGIC_STEP**: raise_app_exceptions=False keeps the unhandled-exception case a real 500
    # response instead of re-raising into the test, which is what a client would see.
    fastapi_app.include_router(_probe_router())
    transport = ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://probe") as client:
        yield client


# FUNCTION: test_classifier_maps_status_ranges
# SUMMARY: The pure mapping from status code to outcome, including the no-status case.
@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (None, RequestOutcome.OK),
        (200, RequestOutcome.OK),
        (302, RequestOutcome.OK),
        (399, RequestOutcome.OK),
        (400, RequestOutcome.CLIENT_ERROR),
        (404, RequestOutcome.CLIENT_ERROR),
        (499, RequestOutcome.CLIENT_ERROR),
        (500, RequestOutcome.SERVER_ERROR),
        (502, RequestOutcome.SERVER_ERROR),
    ],
)
def test_classifier_maps_status_ranges(
    status_code: Optional[int], expected: RequestOutcome
) -> None:
    assert classify_request_outcome(status_code) is expected


# FUNCTION: test_upstream_failure_is_reported_as_server_error
# SUMMARY: The case the old boolean got wrong — a 502 raised as a handled domain error.
async def test_upstream_failure_is_reported_as_server_error(
    probe_client: AsyncClient, log_capture: list[dict[str, Any]]
) -> None:
    response = await probe_client.get("/probe/upstream")

    summary = _last_summary(log_capture)
    assert response.status_code == 502
    assert summary["outcome"] == RequestOutcome.SERVER_ERROR.value
    assert summary["status_code"] == 502


# FUNCTION: test_handled_client_errors_are_not_server_errors
# SUMMARY: 404 and 422 stay routine so the reader is not drowned in false alarms.
@pytest.mark.parametrize(
    ("method", "path", "body", "expected_status"),
    [
        ("GET", "/no-such-route", None, 404),
        ("GET", "/probe/missing", None, 404),
        ("POST", "/probe/validate", {"number": "not a number"}, 422),
    ],
)
async def test_handled_client_errors_are_not_server_errors(
    probe_client: AsyncClient,
    log_capture: list[dict[str, Any]],
    method: str,
    path: str,
    body: Optional[dict[str, Any]],
    expected_status: int,
) -> None:
    response = await probe_client.request(method, path, json=body)

    summary = _last_summary(log_capture)
    assert response.status_code == expected_status
    assert summary["outcome"] == RequestOutcome.CLIENT_ERROR.value
    assert summary["status_code"] == expected_status


# FUNCTION: test_unhandled_exception_is_reported_as_server_error
# SUMMARY: An exception escaping the span keeps its own path to SERVER_ERROR.
async def test_unhandled_exception_is_reported_as_server_error(
    probe_client: AsyncClient, log_capture: list[dict[str, Any]]
) -> None:
    response = await probe_client.get("/probe/crash")

    summary = _last_summary(log_capture)
    assert response.status_code == 500
    assert summary["outcome"] == RequestOutcome.SERVER_ERROR.value
    assert summary["error_count"] == 1


# FUNCTION: test_successful_request_reports_status_code
# SUMMARY: A healthy request carries its status code, which the old payload omitted entirely.
async def test_successful_request_reports_status_code(
    probe_client: AsyncClient, log_capture: list[dict[str, Any]]
) -> None:
    response = await probe_client.post("/probe/validate", json={"number": 7})

    summary = _last_summary(log_capture)
    assert response.status_code == 200
    assert summary["outcome"] == RequestOutcome.OK.value
    assert summary["status_code"] == 200


# FUNCTION: _summaries_for
# SUMMARY: Every request.summary payload captured for one span name.
# INPUT: captured (list[dict[str, Any]]): Entries collected by the log_capture fixture.
# INPUT: span_name (str): Name of the span whose summaries are wanted.
# OUTPUT: (list[dict[str, Any]]): Payloads, in emission order.
def _summaries_for(captured: list[dict[str, Any]], span_name: str) -> list[dict[str, Any]]:
    return [
        entry["kwargs"]["data"]
        for entry in captured
        if entry["kwargs"].get("event_id") == "request.summary"
        and (entry["kwargs"].get("data") or {}).get("span_name") == span_name
    ]


# CLASS: tests.application.test_request_summary_outcome.TestTheRequestSpanBeginsItsOwnTrace
# SUMMARY: Verify a request is observable even when something else already holds a span open.
# NOTE: Every test above drives the app through ASGITransport with nothing wrapping the call, which
# makes http_request a root span by accident. Production is the opposite: the launcher holds
# `application_lifecycle` open around the whole of `uvicorn.run`, asyncio copies that context into
# each request task, and the span inherited a parent. The suite was therefore structurally blind to
# the defect it looked like it covered — measured on a live container, seven requests produced zero
# request.summary events while these tests stayed green. These cases wrap the client call in a span
# on purpose, which is the only way to reproduce the deployed topology in-process.
class TestTheRequestSpanBeginsItsOwnTrace:
    # FUNCTION: test_summary_is_emitted_even_inside_a_wrapping_span
    # SUMMARY: Verify the request still reports itself when an outer span is already open.
    async def test_summary_is_emitted_even_inside_a_wrapping_span(
        self, probe_client: AsyncClient, log_capture: list[dict[str, Any]]
    ) -> None:
        from project.core.logging import get_logger

        with get_logger(__name__).span("application_lifecycle"):
            response = await probe_client.post("/probe/validate", json={"number": 7})

        # **LOGIC_STEP**: By name, not "the last one" — the wrapping span emits a summary of its
        # own after the block closes, and taking the last would silently read that instead.
        summaries = _summaries_for(log_capture, "http_request")
        assert len(summaries) == 1
        summary = summaries[0]
        assert response.status_code == 200
        assert summary["status_code"] == 200
        assert summary["outcome"] == RequestOutcome.OK.value

    # FUNCTION: test_the_request_span_is_a_trace_root
    # SUMMARY: Verify the emitted span.start carries no parent, which is what the renderer expects.
    async def test_the_request_span_is_a_trace_root(
        self, probe_client: AsyncClient, log_capture: list[dict[str, Any]]
    ) -> None:
        # **LOGIC_STEP**: This is the emitter half of the guard in
        # tests/application/test_trace_formatter_against_real_output.py. That file renders a
        # fixture; this one reads the shape off the running application, so the two cannot drift
        # into testing a shape nothing produces.
        from project.core.logging import get_logger

        with get_logger(__name__).span("application_lifecycle"):
            await probe_client.post("/probe/validate", json={"number": 1})

        starts = [
            entry["kwargs"]
            for entry in log_capture
            if entry["kwargs"].get("event_id") == "span.start"
            and entry["kwargs"].get("name") == "http_request"
        ]
        assert starts, "the request opened no http_request span"
        assert all(start["parent_span_id"] is None for start in starts)

    # FUNCTION: test_concurrent_requests_do_not_share_counters
    # SUMMARY: Verify each request reports its own span and error counts, not a running total.
    async def test_concurrent_requests_do_not_share_counters(
        self, probe_client: AsyncClient, log_capture: list[dict[str, Any]]
    ) -> None:
        # **LOGIC_STEP**: The counters live in one dict per root span. While the only root span was
        # the process-lifetime one, every concurrent request mutated the same dict — measured on a
        # live container as a summary whose duration_ms equalled process uptime. Ten at once, so a
        # shared bucket shows up as counts that climb instead of repeating.
        from project.core.logging import get_logger

        with get_logger(__name__).span("application_lifecycle"):
            await asyncio.gather(
                *(probe_client.post("/probe/validate", json={"number": n}) for n in range(10))
            )

        summaries = _summaries_for(log_capture, "http_request")
        assert len(summaries) == 10
        assert {summary["child_span_count"] for summary in summaries} == {0}
        assert {summary["error_count"] for summary in summaries} == {0}
