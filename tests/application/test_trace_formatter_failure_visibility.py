# FILE: tests/application/test_trace_formatter_failure_visibility.py
# SUMMARY: Regression guards proving a failed request is visible in the rendered trace tree.

from __future__ import annotations

import json
from typing import Any

import pytest

from project.core.logging.trace_formatter import (
    format_all_traces_for_llm,
    format_trace_for_llm,
    render_inventory_note,
    trace_inventory,
)

_FAILED_TRACE = "aaaaaaaa1111"
_OK_TRACE = "bbbbbbbb2222"


# FUNCTION: _line
# SUMMARY: Render one NDJSON log line.
# OUTPUT: (str): Serialized event.
def _line(**event: Any) -> str:
    return json.dumps(event)


# FUNCTION: _log_with_failed_then_ok
# SUMMARY: Build a log where the FIRST request failed and the LAST one succeeded.
# OUTPUT: (list[str]): NDJSON lines.
def _log_with_failed_then_ok() -> list[str]:
    return [
        _line(
            seq=1,
            trace_id=_FAILED_TRACE,
            event_id="span.start",
            span_id="s1",
            span_name="http_request",
            parent_span_id=None,
            data={"method": "POST", "path": "/orders"},
        ),
        _line(
            seq=2,
            trace_id=_FAILED_TRACE,
            event_id="span.finish",
            span_id="s1",
            span_name="http_request",
            parent_span_id=None,
            duration_ms=12.0,
            data={"output": {"status_code": 500}},
        ),
        # **LOGIC_STEP**: The framework catches the exception outside the span, so this record —
        # the only one naming the cause — arrives after span.finish and outside any span.
        _line(
            seq=3,
            trace_id=_FAILED_TRACE,
            event_id="critical.unexpected_server_error",
            data={
                "failure_type": "unexpected_server_error",
                "exception_type": "KeyError",
                "exception_message": "'customer_id'",
            },
        ),
        _line(
            seq=4,
            trace_id=_FAILED_TRACE,
            event_id="request.summary",
            data={"child_span_count": 0, "error_count": 1, "outcome": "failed", "status_code": 500},
        ),
        _line(
            seq=5,
            trace_id=_OK_TRACE,
            event_id="span.start",
            span_id="s2",
            span_name="http_request",
            parent_span_id=None,
            data={"method": "GET", "path": "/health"},
        ),
        _line(
            seq=6,
            trace_id=_OK_TRACE,
            event_id="span.finish",
            span_id="s2",
            span_name="http_request",
            parent_span_id=None,
            duration_ms=3.0,
            data={"output": {"status_code": 200}},
        ),
        _line(
            seq=7,
            trace_id=_OK_TRACE,
            event_id="request.summary",
            data={"child_span_count": 0, "error_count": 0, "outcome": "ok", "status_code": 200},
        ),
    ]


# CLASS: tests.application.test_trace_formatter_failure_visibility.TestFailureIsVisible
# SUMMARY: Verify the cause of a 500 reaches the rendered tree instead of being dropped.
class TestFailureIsVisible:
    # FUNCTION: test_critical_event_appears_in_rendered_trace
    # SUMMARY: Verify the critical record is rendered rather than silently discarded.
    @pytest.mark.unit
    def test_critical_event_appears_in_rendered_trace(self) -> None:
        rendered = format_trace_for_llm(_log_with_failed_then_ok(), trace_id=_FAILED_TRACE)

        assert "unexpected_server_error" in rendered
        assert "KeyError" in rendered
        assert "'customer_id'" in rendered

    # FUNCTION: test_root_span_is_marked_failed_even_without_span_error
    # SUMMARY: Verify a 500 is not rendered with a success mark just because the span closed cleanly.
    @pytest.mark.unit
    def test_root_span_is_marked_failed_even_without_span_error(self) -> None:
        rendered = format_trace_for_llm(_log_with_failed_then_ok(), trace_id=_FAILED_TRACE)
        root_line = next(line for line in rendered.splitlines() if "http_request" in line)

        assert "✗" in root_line
        assert "✓" not in root_line

    # FUNCTION: test_successful_trace_still_renders_as_success
    # SUMMARY: Verify the failure detection does not mark healthy requests as failed.
    @pytest.mark.unit
    def test_successful_trace_still_renders_as_success(self) -> None:
        rendered = format_trace_for_llm(_log_with_failed_then_ok(), trace_id=_OK_TRACE)
        root_line = next(line for line in rendered.splitlines() if "http_request" in line)

        assert "✓" in root_line


# CLASS: tests.application.test_trace_formatter_failure_visibility.TestHiddenTracesAreAnnounced
# SUMMARY: Verify the default single-trace view says what it is not showing.
class TestHiddenTracesAreAnnounced:
    # FUNCTION: test_inventory_lists_traces_and_marks_failures
    # SUMMARY: Verify both traces are found and only the failed one is flagged.
    @pytest.mark.unit
    def test_inventory_lists_traces_and_marks_failures(self) -> None:
        trace_ids, failed = trace_inventory(_log_with_failed_then_ok())

        assert trace_ids == [_FAILED_TRACE, _OK_TRACE]
        assert failed == {_FAILED_TRACE}

    # FUNCTION: test_note_warns_about_hidden_failed_trace
    # SUMMARY: Verify rendering the healthy last trace still surfaces the earlier failure.
    @pytest.mark.unit
    def test_note_warns_about_hidden_failed_trace(self) -> None:
        trace_ids, failed = trace_inventory(_log_with_failed_then_ok())

        note = render_inventory_note(trace_ids, failed, _OK_TRACE)

        assert "1 with errors" in note
        assert _FAILED_TRACE[:8] in note
        assert "--all" in note

    # FUNCTION: test_note_is_empty_when_nothing_is_hidden
    # SUMMARY: Verify a single-trace log produces no noise.
    @pytest.mark.unit
    def test_note_is_empty_when_nothing_is_hidden(self) -> None:
        assert render_inventory_note([_OK_TRACE], set(), _OK_TRACE) == ""

    # FUNCTION: test_all_traces_view_contains_both_requests
    # SUMMARY: Verify the --all rendering path covers every HTTP trace in the file.
    @pytest.mark.unit
    def test_all_traces_view_contains_both_requests(self) -> None:
        rendered = format_all_traces_for_llm(_log_with_failed_then_ok())

        assert "/orders" in rendered
        assert "/health" in rendered
        assert "unexpected_server_error" in rendered
