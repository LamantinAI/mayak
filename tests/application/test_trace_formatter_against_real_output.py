# FILE: tests/application/test_trace_formatter_against_real_output.py
# SUMMARY: The renderer must work on the event shape THIS application emits, and on the shape it
# used to emit, because logs written by the old one are still on disk.
#
# History, because it explains both fixtures. middleware.py opened `http_request` inside the
# process-lifetime `application_lifecycle` span, so every real request carried a non-None parent
# that was absent from its own trace — the parent has no trace_id and _parse_events drops it. The
# child matched neither branch of the root/attach decision and vanished: `make format-trace`
# printed "(no spans found)" for every real log while eleven green tests, all of which built their
# root with `parent_span_id=None`, said otherwise. The renderer was taught to treat "parent exists
# but is outside this trace" as a root, and this file was written to hold it to that.
#
# The emitter has since been fixed too: `http_request` is opened with `root=True`, so today's real
# shape IS `parent_span_id=None` — which is why the fixture below is parametrised over both. The
# nested case is not hypothetical history: an archived NDJSON file from before the fix renders
# through the same code path, and a vertical is free to open a span under a parent of its own.
# `tests/application/test_request_summary_outcome.py::test_the_request_span_is_a_trace_root` is the
# other half of the guard — it reads the shape off the running application, so this file cannot
# quietly go on testing a shape nothing emits.

import json

import pytest

from project.core.logging.trace_formatter import format_all_traces_for_llm, format_trace_for_llm

# ATTRIBUTE: _LIFECYCLE_SPAN_ID (str)
# SUMMARY: The parent http_request used to carry, which never reaches the renderer because the
# lifecycle span has no trace_id. Kept because logs in that shape still exist and must still render.
_LIFECYCLE_SPAN_ID = "90907caae88e461fb73ac62c912fdbdd"


# FUNCTION: _real_shape_trace
# SUMMARY: Build one trace in the shape project/infrastructure/api/middleware.py emits.
# INPUT: nested (bool): Emit the pre-fix shape — a parent that exists but is outside this trace.
# OUTPUT: (list[str]): NDJSON lines for a single HTTP request.
def _real_shape_trace(
    trace_id: str = "efcb3e95-f427-4d98-b230-d3d751243eb5",
    span_id: str = "61b7d7c3",
    failed: bool = False,
    nested: bool = False,
) -> list[str]:
    parent = _LIFECYCLE_SPAN_ID if nested else None

    def event(event_id: str, seq: int, **extra: object) -> str:
        payload: dict[str, object] = {
            "seq": seq,
            "ts": "2026-08-07T11:08:52+00:00",
            "level": "INFO",
            "logger": "test",
            "msg": "m",
            "trace_id": trace_id,
            "span_id": span_id,
            "span_name": "http_request",
            "event_id": event_id,
        }
        payload.update(extra)
        return json.dumps(payload) + "\n"

    lines = [
        event(
            "span.start",
            1,
            # **LOGIC_STEP**: The two facts this fixture exists for — the parent link, whichever
            # shape it takes, and the request details nested under input_params.
            parent_span_id=parent,
            data={"input_params": {"method": "GET", "path": "/reference-tasks/{id}"}},
        ),
    ]
    if failed:
        lines.append(
            event(
                "error.project_error",
                2,
                level="ERROR",
                data={
                    "exception_type": "NotFoundError",
                    "exception_message": "Note 'x' does not exist",
                },
            )
        )
    lines.append(event("span.finish", 3, parent_span_id=parent, duration_ms=9.4))
    return lines


# CLASS: tests.application.test_trace_formatter_against_real_output.TestRendersTheShapeTheAppEmits
# SUMMARY: Verify both the current shape and the archived one render into a tree.
class TestRendersTheShapeTheAppEmits:
    # FUNCTION: test_the_request_span_renders_as_a_root_in_either_shape
    # SUMMARY: Verify the renderer produces a tree instead of "(no spans found)".
    @pytest.mark.unit
    @pytest.mark.parametrize("nested", [False, True], ids=["today", "archived-log"])
    def test_the_request_span_renders_as_a_root_in_either_shape(self, nested: bool) -> None:
        # **LOGIC_STEP**: `today` is what the fixed emitter writes — parent_span_id=None.
        # `archived-log` is what every NDJSON file written before the fix contains, and
        # `make format-trace <old-file>` still has to render it.
        rendered = format_trace_for_llm(_real_shape_trace(nested=nested))

        assert "no spans found" not in rendered
        assert "http_request" in rendered

    # FUNCTION: test_the_header_names_the_endpoint
    # SUMMARY: Verify method and path are read from where middleware.py writes them.
    @pytest.mark.unit
    def test_the_header_names_the_endpoint(self) -> None:
        # **LOGIC_STEP**: Without this the trace is identified by a hex id alone, and an agent
        # holding four of them cannot tell which request it is looking at.
        rendered = format_trace_for_llm(_real_shape_trace())

        assert "GET /reference-tasks/{id}" in rendered

    # FUNCTION: test_the_failure_and_its_message_survive_into_the_tree
    # SUMMARY: Verify the exception type and text are what the agent reads, not a bare mark.
    @pytest.mark.unit
    def test_the_failure_and_its_message_survive_into_the_tree(self) -> None:
        rendered = format_trace_for_llm(_real_shape_trace(failed=True))

        assert "NotFoundError" in rendered
        assert "does not exist" in rendered

    # FUNCTION: test_an_unhandled_exception_renders_its_own_frame
    # SUMMARY: Verify the location reaches the reader, not only the exception type.
    @pytest.mark.unit
    def test_an_unhandled_exception_renders_its_own_frame(self) -> None:
        # **LOGIC_STEP**: The traceback was in the log all along, on span.error's `exc_traceback`,
        # and the renderer dropped it. An agent then had "KeyError" and 24 frames to grep. The
        # vendored frame below is in the fixture on purpose: it must not win over the project one.
        traceback_text = (
            "Traceback (most recent call last):\n"
            '  File "/repo/.venv/lib/python3.13/site-packages/starlette/x.py", line 9, in call\n'
            "    await self.app(scope)\n"
            '  File "/repo/project/infrastructure/persistence/reference_task_repository.py", '
            "line 60, in row_to_reference_task\n"
            '    status=row["staus"],\n'
            "KeyError: 'staus'\n"
        )
        lines = [
            _real_shape_trace()[0],
            _real_shape_trace()[0].replace('"span.start"', '"span.error"').rstrip("\n")[:-1]
            + ', "exc_traceback": '
            + json.dumps(traceback_text)
            + ', "data": {"exception_type": "KeyError"}}\n',
        ]

        rendered = format_trace_for_llm(lines)

        assert "project/infrastructure/persistence/reference_task_repository.py:60" in rendered
        assert "row_to_reference_task" in rendered
        assert "site-packages" not in rendered

    # FUNCTION: test_all_traces_renders_every_one_of_them
    # SUMMARY: Verify the multi-trace path does not answer "(no traces rendered)".
    @pytest.mark.unit
    def test_all_traces_renders_every_one_of_them(self) -> None:
        lines = _real_shape_trace(trace_id="aaaa1111", span_id="s1") + _real_shape_trace(
            trace_id="bbbb2222", span_id="s2", failed=True
        )

        rendered = format_all_traces_for_llm(lines)

        assert "no traces rendered" not in rendered
        assert "aaaa1111" in rendered
        assert "bbbb2222" in rendered
