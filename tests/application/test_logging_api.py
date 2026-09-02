# FILE: tests/application/test_logging_api.py
# SUMMARY: Unit tests for the semantic logging helper API surface.

import asyncio
import inspect
import logging
from pathlib import Path

import pytest

from project.core.logging import get_logger
from project.core.logging.enums import EventType
from project.core.logging.logger import SemanticLogger


# CLASS: tests.application.test_logging_api.TestLoggingApi
# SUMMARY: Verify the logging helper API exposes summary-only user-input logging.
class TestLoggingApi:
    # FUNCTION: test_log_user_input_signature_uses_message_summary
    # SUMMARY: Verify the helper no longer accepts raw message_text and exposes message_summary instead.
    @pytest.mark.unit
    def test_log_user_input_signature_uses_message_summary(self) -> None:
        signature = inspect.signature(SemanticLogger.log_user_input)

        assert "message_summary" in signature.parameters
        assert "message_text" not in signature.parameters

    # FUNCTION: test_log_user_input_emits_message_summary_payload
    # SUMMARY: Verify the helper forwards only structural summaries into the semantic event payload.
    @pytest.mark.unit
    def test_log_user_input_emits_message_summary_payload(self, log_capture: list[dict]) -> None:
        logger = get_logger("tests.application.test_logging_api")

        logger.log_user_input(
            user_id="user-1",
            input_type="chat",
            message_summary={"length": 5},
        )

        assert log_capture[0]["kwargs"]["data"]["message_summary"] == {"length": 5}
        assert "message_text" not in log_capture[0]["kwargs"]["data"]

    # FUNCTION: test_production_code_does_not_use_removed_message_text_keyword
    # SUMMARY: Verify the removed raw-text logging keyword is absent from production Python code.
    @pytest.mark.unit
    def test_production_code_does_not_use_removed_message_text_keyword(self) -> None:
        root_dir = Path(__file__).resolve().parents[2]
        violations = []
        for path in sorted((root_dir / "project").rglob("*.py")):
            content = path.read_text(encoding="utf-8")
            if "message_text=" in content:
                violations.append(path)

        assert violations == []


# CLASS: tests.application.test_logging_api.TestChildSpanCostsNothingWhenFiltered
# SUMMARY: Verify a child span skips the work behind its own DEBUG events, without losing the
# things the root span's summary reads: the child count, the error count and the error event.
# NOTE: This guard is what makes it affordable to wrap every repository method in a span. Measured
# on this machine: a child span cost 12821 ns with debug on and 2784 ns with it off, because both
# lifecycle events were assembled in full and then dropped by `logging`, and the stack walk that
# fed them ran too. Three spans per request is 0.8 % of one core at 1000 rps.
class TestChildSpanCostsNothingWhenFiltered:
    # FUNCTION: test_child_span_emits_nothing_when_debug_is_off
    # SUMMARY: Verify no start/finish event is built for a child span at production level.
    @pytest.mark.unit
    def test_child_span_emits_nothing_when_debug_is_off(
        self, log_capture: list[dict], caplog: pytest.LogCaptureFixture
    ) -> None:
        logger = get_logger("tests.application.test_logging_api.filtered")
        caplog.set_level(logging.INFO, logger="tests.application.test_logging_api.filtered")

        # **LOGIC_STEP**: Asserted inside the root span. Outside it, the root's own finish and
        # summary land in the capture too — the first version of this test read them as the child's.
        with logger.span("http_request", root=True):
            log_capture.clear()
            with logger.span("db.reference_task.get", task_id="x"):
                pass

            assert [event["kwargs"].get("event_id") for event in log_capture] == []

    # FUNCTION: test_child_span_emits_both_events_when_debug_is_on
    # SUMMARY: Verify turning DEBUG on restores the full trace — the guard is a filter, not a delete.
    @pytest.mark.unit
    def test_child_span_emits_both_events_when_debug_is_on(
        self, log_capture: list[dict], caplog: pytest.LogCaptureFixture
    ) -> None:
        logger = get_logger("tests.application.test_logging_api.verbose")
        caplog.set_level(logging.DEBUG, logger="tests.application.test_logging_api.verbose")

        with logger.span("http_request", root=True):
            log_capture.clear()
            with logger.span("db.reference_task.get", task_id="x"):
                pass

            assert [event["kwargs"].get("event_id") for event in log_capture] == [
                "span.start",
                "span.finish",
            ]

    # FUNCTION: test_a_filtered_child_span_still_reports_its_error
    # SUMMARY: Verify the error branch survives the guard, resolving the caller it never resolved.
    @pytest.mark.unit
    def test_a_filtered_child_span_still_reports_its_error(
        self, log_capture: list[dict], caplog: pytest.LogCaptureFixture
    ) -> None:
        logger = get_logger("tests.application.test_logging_api.failing")
        caplog.set_level(logging.INFO, logger="tests.application.test_logging_api.failing")

        with logger.span("http_request", root=True):
            log_capture.clear()
            with pytest.raises(ValueError):
                with logger.span("db.reference_task.get", task_id="x"):
                    raise ValueError("query blew up")

        errors = [event for event in log_capture if event["kwargs"].get("event_id") == "span.error"]
        assert len(errors) == 1
        # **LOGIC_STEP**: The caller is resolved lazily inside the except branch, and it must still
        # name the frame that opened the span — not a frame inside logger.py or contextlib.
        caller = errors[0]["kwargs"]["_caller"]
        assert caller is not None
        assert Path(caller[0]).name == "test_logging_api.py"

    # FUNCTION: test_a_filtered_child_span_is_still_counted_by_its_root
    # SUMMARY: Verify child_span_count keeps counting spans whose own events were filtered out.
    @pytest.mark.unit
    def test_a_filtered_child_span_is_still_counted_by_its_root(
        self, log_capture: list[dict], caplog: pytest.LogCaptureFixture
    ) -> None:
        logger = get_logger("tests.application.test_logging_api.counted")
        caplog.set_level(logging.INFO, logger="tests.application.test_logging_api.counted")

        with logger.span("http_request", root=True):
            for _ in range(3):
                with logger.span("db.reference_task.get", task_id="x"):
                    pass

        summaries = [
            event for event in log_capture if event["kwargs"].get("event_id") == "request.summary"
        ]
        assert len(summaries) == 1
        assert summaries[0]["kwargs"]["data"]["child_span_count"] == 3


# CLASS: tests.application.test_logging_api.TestAnInterruptedSpanStillReportsItself
# SUMMARY: Verify a span cut short by cancellation writes its error and summary and re-raises as-is.
# NOTE: `except Exception` does not see asyncio.CancelledError, KeyboardInterrupt or SystemExit.
# A request cancelled by uvicorn's graceful-shutdown timeout therefore emitted span.start and
# nothing else, and the trace tree — built from span.finish and span.error — had no node for it.
# Measured on 2026-09-02: ['span.start'] against ['span.start', 'span.error', 'request.summary']
# for a RuntimeError in the same harness.
class TestAnInterruptedSpanStillReportsItself:
    # FUNCTION: test_an_interrupted_root_span_leaves_an_error_and_a_summary
    # SUMMARY: Verify the three events appear, at WARNING, and the same object comes back out.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "interruption", [asyncio.CancelledError, KeyboardInterrupt, SystemExit]
    )
    def test_an_interrupted_root_span_leaves_an_error_and_a_summary(
        self,
        log_capture: list[dict],
        caplog: pytest.LogCaptureFixture,
        interruption: type[BaseException],
    ) -> None:
        logger = get_logger("tests.application.test_logging_api.interrupted")
        caplog.set_level(logging.INFO, logger="tests.application.test_logging_api.interrupted")
        raised = interruption()

        with pytest.raises(interruption) as excinfo:
            with logger.span("http_request", root=True):
                raise raised

        # **LOGIC_STEP**: Identity, not type. asyncio tells its own cancellation apart by the
        # object it threw; a re-raise that wrapped or re-created it would make the task look
        # like it failed with an unrelated error.
        assert excinfo.value is raised
        assert [event["kwargs"].get("event_id") for event in log_capture] == [
            "span.start",
            "span.error",
            "request.summary",
        ]
        error, summary = log_capture[1], log_capture[2]
        assert error["kwargs"]["exception_type"] == interruption.__name__
        assert error["kwargs"]["level"] == logging.WARNING
        # **LOGIC_STEP**: event_type is what the NDJSON carries and what a reader filters on;
        # the numeric level alone would let issue.error ship under a WARNING and mark every
        # cancelled request as an application failure.
        assert error["event_type"] is EventType.ISSUE_WARNING
        assert summary["kwargs"]["data"]["outcome"] == "cancelled"
        assert summary["kwargs"]["level"] == logging.WARNING

    # FUNCTION: test_an_interrupted_child_span_is_reported_under_its_root
    # SUMMARY: Verify a child at production level still writes its error, and the root its summary.
    @pytest.mark.unit
    def test_an_interrupted_child_span_is_reported_under_its_root(
        self, log_capture: list[dict], caplog: pytest.LogCaptureFixture
    ) -> None:
        logger = get_logger("tests.application.test_logging_api.interrupted_child")
        caplog.set_level(
            logging.INFO, logger="tests.application.test_logging_api.interrupted_child"
        )

        with pytest.raises(asyncio.CancelledError):
            with logger.span("http_request", root=True):
                log_capture.clear()
                with logger.span("db.reference_task.get", task_id="x"):
                    raise asyncio.CancelledError()

        # **LOGIC_STEP**: The child's own start was filtered at INFO, as for any child span; its
        # error is not, because a WARNING passes the same filter the ERROR branch relies on.
        assert [event["kwargs"].get("event_id") for event in log_capture] == [
            "span.error",
            "span.error",
            "request.summary",
        ]
        assert log_capture[0]["kwargs"]["name"] == "db.reference_task.get"
        assert log_capture[0]["kwargs"]["parent_span_id"] is not None
        assert log_capture[1]["kwargs"]["name"] == "http_request"

    # FUNCTION: test_a_failing_log_cannot_replace_the_cancellation
    # SUMMARY: Verify an error raised while logging the interruption is dropped, not propagated.
    @pytest.mark.unit
    def test_a_failing_log_cannot_replace_the_cancellation(
        self, log_capture: list[dict], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logger = get_logger("tests.application.test_logging_api.interrupted_logging")

        def handler_already_closed(**_: object) -> None:
            raise RuntimeError("handler closed during shutdown")

        monkeypatch.setattr(logger, "_emit_interrupted_span", handler_already_closed)
        raised = asyncio.CancelledError()

        # **LOGIC_STEP**: Had the RuntimeError escaped, it would carry the CancelledError only as
        # __context__ and uvicorn would record a task that failed rather than one it cancelled.
        with pytest.raises(asyncio.CancelledError) as excinfo:
            with logger.span("http_request", root=True):
                raise raised

        assert excinfo.value is raised
