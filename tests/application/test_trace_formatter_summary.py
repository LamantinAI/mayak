# FILE: tests/application/test_trace_formatter_summary.py
# SUMMARY: The trace renderer must tell the truth about logs written before the outcome field existed.
#
# When request.summary switched from a boolean `success` to `outcome`, the renderer defaulted a
# missing `outcome` to OK. Every archived failure then rendered as OK — the same lie the new field
# was introduced to remove, moved from the writer into the reader. Archived logs are precisely what
# an agent opens when investigating something that already went wrong.

import json

import pytest

from project.core.logging.trace_formatter import format_trace_for_llm


def _trace(summary: dict[str, object]) -> list[str]:
    def event(event_id: str, **extra: object) -> str:
        payload: dict[str, object] = {
            "seq": 1,
            "ts": "2026-08-01T00:00:00+00:00",
            "level": "INFO",
            "logger": "test",
            "msg": "m",
            "trace_id": "t1",
            "event_id": event_id,
            "span_id": "s1",
            "name": "http_request",
            "span_name": "http_request",
        }
        payload.update(extra)
        return json.dumps(payload) + "\n"

    return [
        event("span.start", parent_span_id=None),
        event("span.finish", parent_span_id=None, duration_ms=5.0),
        event("request.summary", data=summary),
    ]


def _summary_line(summary: dict[str, object]) -> str:
    rendered = format_trace_for_llm(_trace(summary))
    lines = [line for line in rendered.splitlines() if "request.summary" in line]
    assert lines, f"no summary line rendered from {summary}"
    return lines[-1]


# The regression this file exists for.
@pytest.mark.unit
def test_legacy_failure_is_not_rendered_as_ok() -> None:
    line = _summary_line({"success": False, "error_count": 1, "child_span_count": 2})

    assert "OK" not in line
    assert "FAILED" in line


# The other half of the old format must keep its meaning.
@pytest.mark.unit
def test_legacy_success_still_reads_as_ok() -> None:
    assert "OK" in _summary_line({"success": True, "error_count": 0, "child_span_count": 2})


@pytest.mark.unit
def test_current_outcome_is_rendered_with_its_status() -> None:
    line = _summary_line(
        {"outcome": "server_error", "status_code": 502, "error_count": 0, "child_span_count": 1}
    )

    assert "SERVER_ERROR 502" in line


@pytest.mark.unit
def test_payload_with_neither_field_is_unknown_not_ok() -> None:
    line = _summary_line({"error_count": 0, "child_span_count": 0})

    assert "UNKNOWN" in line
    assert "OK" not in line
