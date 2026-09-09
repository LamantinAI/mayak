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
        trace_ids, failed, cancelled = trace_inventory(_log_with_failed_then_ok())

        assert trace_ids == [_FAILED_TRACE, _OK_TRACE]
        assert failed == {_FAILED_TRACE}
        assert cancelled == set()

    # FUNCTION: test_note_warns_about_hidden_failed_trace
    # SUMMARY: Verify rendering the healthy last trace still surfaces the earlier failure.
    @pytest.mark.unit
    def test_note_warns_about_hidden_failed_trace(self) -> None:
        trace_ids, failed, _ = trace_inventory(_log_with_failed_then_ok())

        note = render_inventory_note(trace_ids, failed, _OK_TRACE)

        assert "1 with errors" in note
        assert _FAILED_TRACE[:8] in note
        assert "--all" in note

    # FUNCTION: test_note_is_empty_when_nothing_is_hidden
    # SUMMARY: Verify a single-trace log produces no noise.
    @pytest.mark.unit
    def test_note_is_empty_when_nothing_is_hidden(self) -> None:
        assert render_inventory_note([_OK_TRACE], set(), _OK_TRACE) == ""


_CANCELLED_TRACE = "cccccccc3333"


# FUNCTION: _log_with_cancelled_then_ok
# SUMMARY: Build a log where the FIRST request was cancelled at shutdown and the LAST succeeded.
# OUTPUT: (list[str]): NDJSON lines, shaped as logger.span writes them for an interruption.
def _log_with_cancelled_then_ok() -> list[str]:
    return [
        _line(
            seq=1,
            trace_id=_CANCELLED_TRACE,
            event_id="span.start",
            span_id="s3",
            span_name="http_request",
            parent_span_id=None,
            data={"method": "POST", "path": "/slow"},
        ),
        # **LOGIC_STEP**: WARNING, no traceback — the shape _emit_interrupted_span produces.
        _line(
            seq=2,
            trace_id=_CANCELLED_TRACE,
            level="WARNING",
            event_id="span.error",
            span_id="s3",
            span_name="http_request",
            parent_span_id=None,
            duration_ms=30000.0,
            data={"exception_type": "CancelledError", "error_message": ""},
        ),
        _line(
            seq=3,
            trace_id=_CANCELLED_TRACE,
            level="WARNING",
            event_id="request.summary",
            data={"child_span_count": 0, "error_count": 0, "outcome": "cancelled"},
        ),
        *_log_with_failed_then_ok()[4:],
    ]


# CLASS: tests.application.test_trace_formatter_failure_visibility.TestCancelledIsNotFailed
# SUMMARY: Verify a cancelled request is shown as stopped, in the tree and in the inventory note.
# NOTE: logger.span writes an interruption as span.error at WARNING and a summary of `cancelled`,
# and the renderer used to treat both as a failure: the same ✗ as a 500 on the root line, and
# "1 with errors" in the note for a request the server was told to stop — until the WARNING level
# was introduced to keep the two apart.
class TestCancelledIsNotFailed:
    # FUNCTION: test_root_span_is_marked_stopped_not_failed
    # SUMMARY: Verify the root line carries ⊘ and the exception type, never ✗.
    @pytest.mark.unit
    def test_root_span_is_marked_stopped_not_failed(self) -> None:
        rendered = format_trace_for_llm(_log_with_cancelled_then_ok(), trace_id=_CANCELLED_TRACE)
        root_line = next(line for line in rendered.splitlines() if "http_request" in line)

        assert "⊘ CancelledError" in root_line
        assert "✗" not in root_line

    # FUNCTION: test_inventory_names_the_cancelled_trace_separately
    # SUMMARY: Verify the cancelled trace is in its own set and not among the failures.
    @pytest.mark.unit
    def test_inventory_names_the_cancelled_trace_separately(self) -> None:
        trace_ids, failed, cancelled = trace_inventory(_log_with_cancelled_then_ok())

        assert trace_ids == [_CANCELLED_TRACE, _OK_TRACE]
        assert failed == set()
        assert cancelled == {_CANCELLED_TRACE}

    # FUNCTION: test_note_says_cancelled_rather_than_with_errors
    # SUMMARY: Verify the hidden-trace note counts the cancellation under its own word.
    @pytest.mark.unit
    def test_note_says_cancelled_rather_than_with_errors(self) -> None:
        trace_ids, failed, cancelled = trace_inventory(_log_with_cancelled_then_ok())

        note = render_inventory_note(trace_ids, failed, _OK_TRACE, cancelled)

        assert "1 cancelled" in note
        assert "with errors" not in note
        assert "--all" in note

    # FUNCTION: test_a_handled_interruption_in_a_child_does_not_fail_the_request
    # SUMMARY: Verify a child stopped at WARNING under a root that answered OK keeps the ✓.
    @pytest.mark.unit
    def test_a_handled_interruption_in_a_child_does_not_fail_the_request(self) -> None:
        # **LOGIC_STEP**: The root has no error of its own here, so its mark comes from the
        # trace-wide failure scan — the one place that used to count every span.error as a
        # failure whatever its level.
        lines = [
            _line(
                seq=1,
                trace_id=_OK_TRACE,
                event_id="span.start",
                span_id="s2",
                span_name="http_request",
                parent_span_id=None,
                data={"method": "GET", "path": "/health"},
            ),
            _line(
                seq=2,
                trace_id=_OK_TRACE,
                level="WARNING",
                event_id="span.error",
                span_id="s5",
                span_name="llm.call",
                parent_span_id="s2",
                duration_ms=1.0,
                data={"exception_type": "CancelledError"},
            ),
            *_log_with_failed_then_ok()[5:],
        ]

        rendered = format_trace_for_llm(lines, trace_id=_OK_TRACE)
        root_line = next(line for line in rendered.splitlines() if "http_request" in line)

        assert "✓" in root_line
        assert "✗" not in root_line

    # FUNCTION: test_a_trace_that_failed_before_it_was_cancelled_counts_as_failed
    # SUMMARY: Verify an ERROR-level span.error keeps the trace among the failures.
    @pytest.mark.unit
    def test_a_trace_that_failed_before_it_was_cancelled_counts_as_failed(self) -> None:
        lines = _log_with_cancelled_then_ok()
        lines.insert(
            1,
            _line(
                seq=1,
                trace_id=_CANCELLED_TRACE,
                level="ERROR",
                event_id="span.error",
                span_id="s4",
                span_name="db.reference_task.get",
                parent_span_id="s3",
                duration_ms=1.0,
                data={"exception_type": "OperationalError"},
            ),
        )

        trace_ids, failed, cancelled = trace_inventory(lines)

        assert failed == {_CANCELLED_TRACE}
        assert cancelled == set()

    # FUNCTION: test_all_traces_view_contains_both_requests
    # SUMMARY: Verify the --all rendering path covers every HTTP trace in the file.
    @pytest.mark.unit
    def test_all_traces_view_contains_both_requests(self) -> None:
        rendered = format_all_traces_for_llm(_log_with_failed_then_ok())

        assert "/orders" in rendered
        assert "/health" in rendered
        assert "unexpected_server_error" in rendered


_REJECTED_TRACE = "dddddddd4444"


# FUNCTION: _log_with_rejected_then_ok
# SUMMARY: Build a log where the FIRST request was answered 4xx and the LAST one succeeded.
# OUTPUT: (list[str]): NDJSON lines, shaped as the exception handlers write a rejection.
def _log_with_rejected_then_ok() -> list[str]:
    return [
        _line(
            seq=1,
            trace_id=_REJECTED_TRACE,
            event_id="span.start",
            span_id="s4",
            span_name="http_request",
            parent_span_id=None,
            data={"method": "POST", "path": "/reference-tasks"},
        ),
        _line(
            seq=2,
            trace_id=_REJECTED_TRACE,
            level="WARNING",
            event_id="client_error.validation_error",
            span_id="s4",
            data={
                "error_type": "validation_error",
                "exception_type": "ValidationError",
                "message": "title must not be empty",
            },
        ),
        _line(
            seq=3,
            trace_id=_REJECTED_TRACE,
            event_id="span.finish",
            span_id="s4",
            span_name="http_request",
            parent_span_id=None,
            duration_ms=4.0,
            data={"output": {"status_code": 422}},
        ),
        _line(
            seq=4,
            trace_id=_REJECTED_TRACE,
            event_id="request.summary",
            data={
                "child_span_count": 0,
                "error_count": 0,
                "outcome": "client_error",
                "status_code": 422,
            },
        ),
        *_log_with_failed_then_ok()[4:],
    ]


# CLASS: tests.application.test_trace_formatter_failure_visibility.TestARejectionIsShownAndIsNotAFailure
# SUMMARY: Verify a 4xx keeps its cause in the tree while losing the mark of a failed request.
# NOTE: When these records moved from `error.*` to `client_error.*` the renderer stopped matching
# them, so the one line naming why the request was rejected was parsed and dropped — the reader
# was left with a shaped tree and no reason in it. The root line meanwhile still carried the ✗ of
# a 500, because any outcome that was not ok, unknown or cancelled counted as a failure.
class TestARejectionIsShownAndIsNotAFailure:
    # FUNCTION: test_the_rejection_reason_appears_in_the_tree
    # SUMMARY: Verify the client_error record is rendered as a leaf with its type and message.
    @pytest.mark.unit
    def test_the_rejection_reason_appears_in_the_tree(self) -> None:
        rendered = format_trace_for_llm(_log_with_rejected_then_ok(), trace_id=_REJECTED_TRACE)

        assert "validation_error" in rendered
        assert "title must not be empty" in rendered

    # FUNCTION: test_the_root_line_is_not_marked_failed
    # SUMMARY: Verify a 4xx does not print the mark a 500 prints.
    @pytest.mark.unit
    def test_the_root_line_is_not_marked_failed(self) -> None:
        rendered = format_trace_for_llm(_log_with_rejected_then_ok(), trace_id=_REJECTED_TRACE)
        root_line = next(line for line in rendered.splitlines() if "http_request" in line)

        assert "✗" not in root_line

    # FUNCTION: test_the_inventory_does_not_count_a_rejection_among_the_failures
    # SUMMARY: Verify the hidden-trace note does not report a rejected request as an error.
    @pytest.mark.unit
    def test_the_inventory_does_not_count_a_rejection_among_the_failures(self) -> None:
        trace_ids, failed, cancelled = trace_inventory(_log_with_rejected_then_ok())

        assert trace_ids == [_REJECTED_TRACE, _OK_TRACE]
        assert failed == set()
        assert cancelled == set()
