# FILE: tests/application/test_client_errors_are_not_service_errors.py
# SUMMARY: A 4xx is the application working. This pins that it is recorded that way — at WARNING,
# under `client_error.*` — and that a 5xx still is not.
#
# Until 2026-09-06 every handled exception went through log_error at ERROR whatever status it
# produced, so a client's typo wrote the same line a failing service does: an operator grepping
# ERROR met request validation, and `make format-trace`, which counts events whose id starts with
# `error.` or `critical.`, reported a healthy trace as one with errors in it. CLAUDE.md already
# said `client_error` is "4xx and routine"; the log did not.

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from project.domain.exceptions import (
    AuthenticationError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    ProjectError,
    UpstreamAuthenticationError,
    ValidationError,
    is_client_rejection,
)
from project.core.logging import get_logger
from project.infrastructure.api.exception_handlers import _project_error_status


# FUNCTION: _wire_raising_route
# SUMMARY: Register a route on the assembled app that raises the given exception.
def _wire_raising_route(app: FastAPI, path: str, exc: Exception) -> None:
    async def _raise() -> dict[str, str]:
        raise exc

    app.add_api_route(path, _raise, methods=["GET"])


# FUNCTION: _events_from
# SUMMARY: Pull the event ids and levels of every issue record out of the capture.
# OUTPUT: (list[tuple[str, int]]): (event_id, level) for each record carrying an event id.
def _events_from(log_capture: list[dict]) -> list[tuple[str, Any]]:
    return [
        (event["kwargs"]["event_id"], event["kwargs"].get("level"))
        for event in log_capture
        if event["kwargs"].get("event_id")
    ]


# CLASS: tests.application.test_client_errors_are_not_service_errors.TestAClientErrorReadsAsOne
# SUMMARY: Verify the level and the event id follow the status the request is answered with.
class TestAClientErrorReadsAsOne:
    # FUNCTION: test_a_domain_error_answered_4xx_is_a_client_error
    # SUMMARY: Verify a 404 and a 422 are recorded at WARNING under client_error.
    @pytest.mark.integration
    @pytest.mark.parametrize(
        ("exception", "expected_status"),
        [
            (NotFoundError("missing"), 404),
            (ValidationError("bad input"), 422),
            (ConflictError("duplicate name"), 409),
        ],
    )
    async def test_a_domain_error_answered_4xx_is_a_client_error(
        self,
        fastapi_app: FastAPI,
        async_client: AsyncClient,
        log_capture: list[dict],
        exception: Exception,
        expected_status: int,
    ) -> None:
        path = f"/__test_client_error_{expected_status}"
        _wire_raising_route(fastapi_app, path, exception)
        log_capture.clear()

        response = await async_client.get(path)

        assert response.status_code == expected_status
        issues = [event for event in _events_from(log_capture) if "error" in event[0]]
        assert issues, "the rejection was not recorded at all"
        # **LOGIC_STEP**: The prefix is what `make format-trace` counts on, and the level is what
        # an operator greps. Both have to move together, so both are asserted.
        assert all(event_id.startswith("client_error.") for event_id, _ in issues), issues
        assert all(level == logging.WARNING for _, level in issues), issues

    # FUNCTION: test_a_domain_error_answered_5xx_is_still_an_error
    # SUMMARY: Verify the change did not quieten the failures that are the service's own.
    @pytest.mark.integration
    async def test_a_domain_error_answered_5xx_is_still_an_error(
        self, fastapi_app: FastAPI, async_client: AsyncClient, log_capture: list[dict]
    ) -> None:
        _wire_raising_route(
            fastapi_app, "/__test_upstream_failure", ExternalServiceError("provider down")
        )
        log_capture.clear()

        response = await async_client.get("/__test_upstream_failure")

        assert response.status_code == 502
        issues = [event for event in _events_from(log_capture) if "error" in event[0]]
        assert any(
            event_id.startswith("error.") and level == logging.ERROR for event_id, level in issues
        ), issues

    # FUNCTION: test_a_malformed_request_body_is_a_client_error
    # SUMMARY: Verify request validation — the most common 4xx of all — is recorded as one.
    @pytest.mark.integration
    async def test_a_malformed_request_body_is_a_client_error(
        self, fastapi_app: FastAPI, async_client: AsyncClient, log_capture: list[dict]
    ) -> None:
        log_capture.clear()

        response = await async_client.post("/reference-tasks", json={"title": ""})

        assert response.status_code == 422
        issues = [event for event in _events_from(log_capture) if "error" in event[0]]
        assert issues, "a rejected body was not recorded at all"
        assert all(event_id.startswith("client_error.") for event_id, _ in issues), issues


# CLASS: tests.application.test_client_errors_are_not_service_errors.TestARejectionInsideANestedSpanIsNotAFailure
# SUMMARY: The hole the class above did not cover: exception_handlers.py has judged 4xx-vs-5xx
# correctly since 2026-09-06, but a ConflictError raised INSIDE a nested span — a repository call,
# an application-layer span; see project/infrastructure/persistence/reference_task_repository.py
# for the copyable pattern every vertical's own db.* span follows — never reached that handler
# first. project.core.logging.logger.span()'s own `except Exception` branch saw it, and until
# 2026-09-08 judged every exception the same way: ERROR level, full traceback, whatever it was
# going to become. Two independent agents building real projects on this template each lost the
# live-run stage of their session to that trace — a duplicate-name 409 that read exactly like a
# crash. No vertical shipped by this template raises a ConflictError from inside a span yet, so
# this test wires the shape by hand, the same way it appears in a project that copied the pattern.
class TestARejectionInsideANestedSpanIsNotAFailure:
    # FUNCTION: test_a_conflict_raised_inside_a_span_carries_no_error_level_and_no_traceback
    # SUMMARY: A request that ends 409 must show no ERROR-level record and no captured traceback.
    @pytest.mark.integration
    async def test_a_conflict_raised_inside_a_span_carries_no_error_level_and_no_traceback(
        self,
        fastapi_app: FastAPI,
        async_client: AsyncClient,
        log_capture: list[dict],
    ) -> None:
        probe_logger = get_logger(
            "tests.application.test_client_errors_are_not_service_errors.probe"
        )

        # **LOGIC_STEP**: Mirrors db.reference_task.add: a span around one unit of work that turns
        # out to conflict with what is already stored, and raises from inside the `with` block —
        # not after it, which is the shape that put the exception in front of span()'s own except
        # clause before exception_handlers.py ever saw it.
        async def _raise_conflict_inside_a_span() -> dict[str, str]:
            with probe_logger.span("db.probe.add"):
                raise ConflictError("a task with this name already exists")

        fastapi_app.add_api_route(
            "/__test_conflict_inside_span", _raise_conflict_inside_a_span, methods=["GET"]
        )
        log_capture.clear()

        response = await async_client.get("/__test_conflict_inside_span")

        assert response.status_code == 409
        span_error_events = [
            event["kwargs"]
            for event in log_capture
            if event["kwargs"].get("event_id") == "span.error"
        ]
        assert span_error_events, "the nested span's rejection was not recorded at all"
        # **LOGIC_STEP**: The two facts a reader actually greps for — ERROR level and a captured
        # traceback (exc_info) — asserted directly, not inferred from a mark elsewhere.
        assert all(kwargs.get("level") != logging.ERROR for kwargs in span_error_events), (
            span_error_events
        )
        assert all(not kwargs.get("exc_info") for kwargs in span_error_events), span_error_events
        # **LOGIC_STEP**: The field trace_formatter.py reads to render this WARNING as a rejection
        # rather than as a cancelled/interrupted span — see SpanNode.client_rejection there.
        assert all(kwargs.get("client_rejection") is True for kwargs in span_error_events), (
            span_error_events
        )
        # **LOGIC_STEP**: No event of ANY kind for this request carries ERROR — not the span, not
        # whatever exception_handlers.py writes once the exception reaches it a moment later.
        assert all(event["kwargs"].get("level") != logging.ERROR for event in log_capture), (
            log_capture
        )
        # **LOGIC_STEP**: And the request's own summary counts no error. Levels were fixed first
        # and this counter was left behind, so `request.summary` still said error_count=1 and
        # `make format-trace` still printed `errors=1` over a request the same trace calls a
        # client_error. Found by an independent review of this branch on 2026-09-08.
        summaries = [
            event["kwargs"]["data"]
            for event in log_capture
            if event["kwargs"].get("event_id") == "request.summary"
        ]
        assert summaries, "the request wrote no summary at all"
        assert summaries[-1]["error_count"] == 0, summaries[-1]


# CLASS: tests.application.test_client_errors_are_not_service_errors.TestBothCallSitesJudgeAlike
# SUMMARY: Verify the span and the exception handler cannot disagree about what counts as routine.
# NOTE: They used to decide separately — the span through `is_client_rejection`, the handler
# through `_project_error_status(exc) >= 500`. Both gave the same answers, which is exactly why a
# drift would have gone unnoticed: adding a domain error mapped to 503 would have made the handler
# call it a failure and the span still call it routine, and the log would carry both verdicts for
# one exception. The handler now asks the same function; this pins the equivalence that made the
# swap safe, so a future status mapping cannot quietly break it.
class TestBothCallSitesJudgeAlike:
    # FUNCTION: test_every_domain_error_gets_one_verdict
    # SUMMARY: Verify rejection and sub-500 status agree for every shipped ProjectError subclass.
    # OUTPUT: (None): None.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "error",
        [
            ProjectError("plain"),
            ValidationError("invalid"),
            NotFoundError("missing"),
            ConflictError("duplicate"),
            AuthenticationError("denied"),
            ExternalServiceError("upstream down"),
            UpstreamAuthenticationError("upstream key rejected"),
        ],
        ids=lambda error: type(error).__name__,
    )
    def test_every_domain_error_gets_one_verdict(self, error: ProjectError) -> None:
        assert is_client_rejection(error) is (_project_error_status(error) < 500)
