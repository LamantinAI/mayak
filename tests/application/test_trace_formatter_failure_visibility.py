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


def _line(**event: Any) -> str:
    return json.dumps(event)


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
        # The framework catches the exception outside the span, so this record —
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


class TestFailureIsVisible:
    @pytest.mark.unit
    def test_critical_event_appears_in_rendered_trace(self) -> None:
        rendered = format_trace_for_llm(_log_with_failed_then_ok(), trace_id=_FAILED_TRACE)

        assert "unexpected_server_error" in rendered
        assert "KeyError" in rendered
        assert "'customer_id'" in rendered

    @pytest.mark.unit
    def test_root_span_is_marked_failed_even_without_span_error(self) -> None:
        rendered = format_trace_for_llm(_log_with_failed_then_ok(), trace_id=_FAILED_TRACE)
        root_line = next(line for line in rendered.splitlines() if "http_request" in line)

        assert "✗" in root_line
        assert "✓" not in root_line

    @pytest.mark.unit
    def test_successful_trace_still_renders_as_success(self) -> None:
        rendered = format_trace_for_llm(_log_with_failed_then_ok(), trace_id=_OK_TRACE)
        root_line = next(line for line in rendered.splitlines() if "http_request" in line)

        assert "✓" in root_line


class TestHiddenTracesAreAnnounced:
    @pytest.mark.unit
    def test_inventory_lists_traces_and_marks_failures(self) -> None:
        trace_ids, failed, cancelled = trace_inventory(_log_with_failed_then_ok())

        assert trace_ids == [_FAILED_TRACE, _OK_TRACE]
        assert failed == {_FAILED_TRACE}
        assert cancelled == set()

    @pytest.mark.unit
    def test_note_warns_about_hidden_failed_trace(self) -> None:
        trace_ids, failed, _ = trace_inventory(_log_with_failed_then_ok())

        note = render_inventory_note(trace_ids, failed, _OK_TRACE)

        assert "1 with errors" in note
        assert _FAILED_TRACE[:8] in note
        assert "--all" in note

    @pytest.mark.unit
    def test_note_is_empty_when_nothing_is_hidden(self) -> None:
        assert render_inventory_note([_OK_TRACE], set(), _OK_TRACE) == ""


_CANCELLED_TRACE = "cccccccc3333"


# Returns NDJSON lines, shaped as logger.span writes them for an interruption.
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
        # WARNING, no traceback — the shape _emit_interrupted_span produces.
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


# logger.span writes an interruption as span.error at WARNING and a summary of `cancelled`,
# and the renderer used to treat both as a failure: the same ✗ as a 500 on the root line, and
# "1 with errors" in the note for a request the server was told to stop — until the WARNING level
# was introduced to keep the two apart.
class TestCancelledIsNotFailed:
    @pytest.mark.unit
    def test_root_span_is_marked_stopped_not_failed(self) -> None:
        rendered = format_trace_for_llm(_log_with_cancelled_then_ok(), trace_id=_CANCELLED_TRACE)
        root_line = next(line for line in rendered.splitlines() if "http_request" in line)

        assert "⊘ CancelledError" in root_line
        assert "✗" not in root_line

    @pytest.mark.unit
    def test_inventory_names_the_cancelled_trace_separately(self) -> None:
        trace_ids, failed, cancelled = trace_inventory(_log_with_cancelled_then_ok())

        assert trace_ids == [_CANCELLED_TRACE, _OK_TRACE]
        assert failed == set()
        assert cancelled == {_CANCELLED_TRACE}

    @pytest.mark.unit
    def test_note_says_cancelled_rather_than_with_errors(self) -> None:
        trace_ids, failed, cancelled = trace_inventory(_log_with_cancelled_then_ok())

        note = render_inventory_note(trace_ids, failed, _OK_TRACE, cancelled)

        assert "1 cancelled" in note
        assert "with errors" not in note
        assert "--all" in note

    @pytest.mark.unit
    def test_a_handled_interruption_in_a_child_does_not_fail_the_request(self) -> None:
        # The root has no error of its own here, so its mark comes from the
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

    # Verify the --all rendering path covers every HTTP trace in the file.
    @pytest.mark.unit
    def test_all_traces_view_contains_both_requests(self) -> None:
        rendered = format_all_traces_for_llm(_log_with_failed_then_ok())

        assert "/orders" in rendered
        assert "/health" in rendered
        assert "unexpected_server_error" in rendered


_REJECTED_TRACE = "dddddddd4444"


# Returns NDJSON lines, shaped as the exception handlers write a rejection.
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


# When these records moved from `error.*` to `client_error.*` the renderer stopped matching
# them, so the one line naming why the request was rejected was parsed and dropped — the reader
# was left with a shaped tree and no reason in it. The root line meanwhile still carried the ✗ of
# a 500, because any outcome that was not ok, unknown or cancelled counted as a failure.
class TestARejectionIsShownAndIsNotAFailure:
    @pytest.mark.unit
    def test_the_rejection_reason_appears_in_the_tree(self) -> None:
        rendered = format_trace_for_llm(_log_with_rejected_then_ok(), trace_id=_REJECTED_TRACE)

        assert "validation_error" in rendered
        assert "title must not be empty" in rendered

    @pytest.mark.unit
    def test_the_root_line_is_not_marked_failed(self) -> None:
        rendered = format_trace_for_llm(_log_with_rejected_then_ok(), trace_id=_REJECTED_TRACE)
        root_line = next(line for line in rendered.splitlines() if "http_request" in line)

        assert "✗" not in root_line

    @pytest.mark.unit
    def test_the_inventory_does_not_count_a_rejection_among_the_failures(self) -> None:
        trace_ids, failed, cancelled = trace_inventory(_log_with_rejected_then_ok())

        assert trace_ids == [_REJECTED_TRACE, _OK_TRACE]
        assert failed == set()
        assert cancelled == set()


# Five lines per trace: the request, one hidden-below-the-level tool span inside it, its summary.
def _uniform_log(traces: int) -> list[str]:
    lines: list[str] = []
    for n in range(traces):
        tid, root, tool = f"{n:08x}-uniform", f"r{n}", f"t{n}"
        common: dict[str, Any] = {"trace_id": tid, "parent_span_id": None}
        lines += [
            _line(**common, event_id="span.start", span_id=root, span_name="http_request"),
            _line(
                **{**common, "parent_span_id": root},
                event_id="span.start",
                span_id=tool,
                span_name="agent.tool.lookup",
                data={"input_params": {"query": f"q{n}"}},
            ),
            _line(
                **{**common, "parent_span_id": root},
                event_id="span.finish",
                span_id=tool,
                span_name="agent.tool.lookup",
                duration_ms=1.0,
            ),
            _line(
                **common,
                event_id="span.finish",
                span_id=root,
                span_name="http_request",
                duration_ms=2.0,
            ),
            _line(
                **common,
                event_id="request.summary",
                data={
                    "child_span_count": 1,
                    "filtered_child_span_count": 1,
                    "outcome": "ok",
                    "status_code": 200,
                },
            ),
        ]
    return lines


def _count_json_loads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls = [0]
    real = json.loads

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(json, "loads", counting)
    return calls


# Each trace used to re-parse the whole file: lines × (traces + 1) parses, 8 200 for these 200
# lines, four times as many when the traces double. One parse per line, whatever the count.
@pytest.mark.unit
@pytest.mark.parametrize("traces", [40, 80])
def test_all_traces_parse_each_line_once(monkeypatch: pytest.MonkeyPatch, traces: int) -> None:
    lines = _uniform_log(traces)
    calls = _count_json_loads(monkeypatch)

    rendered = format_all_traces_for_llm(lines)

    assert calls[0] <= len(lines)
    assert rendered.count("\n\n---\n\n") == traces - 1


# The all-traces view is the single view of each HTTP trace, in order of its first request start,
# joined — interleaved traces, failures, cancellations, rejections, legacy summaries and hidden
# spans included, with blank, broken and trace-less lines skipped.
@pytest.mark.unit
def test_all_traces_is_the_single_view_of_each_joined() -> None:
    rejected, cancelled = _log_with_rejected_then_ok()[:4], _log_with_cancelled_then_ok()[:3]
    legacy = [
        line.replace(_FAILED_TRACE, "eeeeeeee5555").replace(
            '"outcome": "failed"', '"success": false'
        )
        for line in _log_with_failed_then_ok()[:4]
    ]
    interleaved = [line for pair in zip(rejected, cancelled) for line in pair] + rejected[3:]
    noise = ["", "{broken", _line(seq=0, event_id="startup", msg="no trace"), "   "]
    lines = noise + interleaved + legacy + _uniform_log(2) + _log_with_failed_then_ok()

    singles = [format_trace_for_llm(lines, trace_id=tid) for tid in trace_inventory(lines)[0]]

    assert len(singles) == 7 and "FAILED (legacy)" in singles[2]
    assert format_all_traces_for_llm(lines) == "\n\n---\n\n".join(singles)


# A request start whose trace_id is not a string used to stop the whole view with TypeError, and a
# JSON line that is not an object with AttributeError; both are skipped like a broken line.
@pytest.mark.unit
def test_all_traces_skips_events_that_are_not_trace_events() -> None:
    odd = [_line(trace_id=7, event_id="span.start", span_id="x", span_name="http_request"), "[1]"]

    rendered = format_all_traces_for_llm(odd + _log_with_failed_then_ok())

    assert rendered == format_all_traces_for_llm(_log_with_failed_then_ok())
    assert _FAILED_TRACE[:8] in rendered and _OK_TRACE[:8] in rendered


@pytest.mark.unit
def test_shutdown_summary_parses_once_and_keeps_the_raw_lines(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from project.core.logging.trace_formatter import prepend_trace_summary

    lines = [line + "\n" for line in _uniform_log(400)]
    log = tmp_path / "app.ndjson"
    log.write_text("".join(lines), encoding="utf-8")
    expected = format_all_traces_for_llm(lines).split("\n")
    calls = _count_json_loads(monkeypatch)

    assert prepend_trace_summary(str(log)) is True

    assert calls[0] <= len(lines)
    written = log.read_text(encoding="utf-8").splitlines(keepends=True)
    header = [f"# {line}\n" for line in expected]
    assert written[2 : 2 + len(header)] == header
    assert written[-len(lines) :] == lines
