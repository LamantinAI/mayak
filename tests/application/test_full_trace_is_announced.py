# FILE: tests/application/test_full_trace_is_announced.py
# SUMMARY: Verify a service that records prompt and completion text says so while it boots, and
# that the text it records goes through the credential scrubber.
#
# NOTE: The flag is a data-retention decision wearing the clothes of a verbosity setting. Nothing
# announced it, so a project turned it on to debug an agent and left it on — which is how a
# customer's email address reached a log file. The warning and the scrubbing were added on
# 2026-09-06 with no test of their own; this is that test.

from __future__ import annotations

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
        self, log_capture: list[dict], test_settings: Any, tmp_path: Any, full_trace: bool
    ) -> None:
        test_settings.observability.full_trace_enabled = full_trace
        test_settings.project.log_dir = str(tmp_path)
        log_capture.clear()

        with (
            patch("project.launcher.main.get_settings", return_value=test_settings),
            patch("project.launcher.main.uvicorn.run", MagicMock()),
            patch("project.launcher.main.CompositionRoot") as root,
        ):
            root.return_value.build_application.return_value = MagicMock()
            main()

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
