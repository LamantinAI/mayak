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
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)


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
        [(NotFoundError("missing"), 404), (ValidationError("bad input"), 422)],
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
