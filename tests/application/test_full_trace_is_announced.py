# FILE: tests/application/test_full_trace_is_announced.py
# SUMMARY: Verify a service that records prompt and completion text says so while it boots, and
# that the text it records goes through the credential scrubber.
#
# NOTE: The flag is a data-retention decision wearing the clothes of a verbosity setting. Nothing
# announced it, so a project turned it on to debug an agent and left it on — which is how a
# customer's email address reached a log file. The warning and the scrubbing shipped with no test
# of their own; this is that test.

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from langchain_core.messages import HumanMessage, SystemMessage

from project.infrastructure.agents.llm_service_live import _build_full_trace_extras
from project.launcher.main import main


# FUNCTION: _warning_types
# SUMMARY: The warning_type of every warning record in the capture.
def _warning_types(log_capture: list[dict]) -> list[str]:
    return [
        event["kwargs"]["data"]["warning_type"]
        for event in log_capture
        if event["kwargs"].get("event_id", "").startswith("warning.")
        and isinstance(event["kwargs"].get("data"), dict)
        and "warning_type" in event["kwargs"]["data"]
    ]


# CLASS: tests.application.test_full_trace_is_announced.TestTheServiceSaysWhatItIsRecording
# SUMMARY: Verify the startup warning follows the flag and nothing else.
class TestTheServiceSaysWhatItIsRecording:
    # FUNCTION: test_the_warning_follows_the_flag
    # SUMMARY: Verify the warning is written when full trace is on and never when it is off.
    @pytest.mark.unit
    @pytest.mark.parametrize("full_trace", [True, False])
    def test_the_warning_follows_the_flag(
        self,
        log_capture: list[dict],
        test_settings: Any,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
        full_trace: bool,
    ) -> None:
        # **LOGIC_STEP**: monkeypatch rather than assignment, because `test_settings` is shared for
        # the session and `main()` reconfigures the root logger against `log_dir`. Left as they
        # were, both outlive the test: a later test writing a real log record would append into a
        # torn-down tmp directory, and every log call after that prints a logging error to stderr.
        monkeypatch.setattr(test_settings.observability, "full_trace_enabled", full_trace)
        monkeypatch.setattr(test_settings.project, "log_dir", str(tmp_path))
        root_logger = logging.getLogger()
        handlers_before = list(root_logger.handlers)
        level_before = root_logger.level
        log_capture.clear()

        try:
            with (
                patch("project.launcher.main.get_settings", return_value=test_settings),
                patch("project.launcher.main.uvicorn.run", MagicMock()),
                patch("project.launcher.main.CompositionRoot") as root,
            ):
                root.return_value.build_application.return_value = MagicMock()
                main()
        finally:
            # **LOGIC_STEP**: Put back what was there, rather than only taking away what main()
            # added. setup_logging goes through dictConfig, which drops every existing root
            # handler before installing its own — so by the time this runs the handlers this test
            # inherited are already gone, and pruning alone would leave the process logging
            # nowhere for every test that follows.
            for handler in list(root_logger.handlers):
                root_logger.removeHandler(handler)
                if handler not in handlers_before:
                    handler.close()
            for handler in handlers_before:
                root_logger.addHandler(handler)
            root_logger.setLevel(level_before)

        announced = "full_trace_records_content" in _warning_types(log_capture)
        assert announced is full_trace


# CLASS: tests.application.test_full_trace_is_announced.TestTheRecordedTextIsScrubbed
# SUMMARY: Verify a credential pasted into a prompt or returned in a completion is redacted.
class TestTheRecordedTextIsScrubbed:
    # FUNCTION: test_a_credential_in_the_recorded_text_is_replaced
    # SUMMARY: Verify each of the three recorded fields goes through the scrubber.
    @pytest.mark.unit
    def test_a_credential_in_the_recorded_text_is_replaced(self) -> None:
        # allow-secret: an invented literal in the shape the scrubber matches, so that the
        # assertion below is about redaction rather than about the shape of a made-up string.
        secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"  # allow-secret: test placeholder
        extras = _build_full_trace_extras(
            True,
            [
                SystemMessage(content=f"You may use {secret} to authenticate."),
                HumanMessage(content=f"my key is {secret}"),
            ],
            completion_text=f"stored {secret}",
        )

        assert secret not in extras["system_prompt"]
        assert secret not in extras["user_message"]
        assert secret not in extras["completion_text"]

    # FUNCTION: test_nothing_is_recorded_while_the_flag_is_off
    # SUMMARY: Verify the hot path stays empty when full trace is disabled.
    @pytest.mark.unit
    def test_nothing_is_recorded_while_the_flag_is_off(self) -> None:
        extras = _build_full_trace_extras(
            False, [HumanMessage(content="anything")], completion_text="anything"
        )

        assert extras == {}
