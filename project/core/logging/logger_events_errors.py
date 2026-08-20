# FILE: project/core/logging/logger_events_errors.py
# SUMMARY: Semantic logging mixin implementing error and critical failure helper methods.

import logging
from typing import Any, Optional

from project.core.logging.context import get_current_error_id
from project.core.logging.enums import EventType
from project.core.logging.logger_events_base import SemanticLoggerEventContract
from project.core.logging.logger_types import LogValue
from project.core.logging.redaction import redact_secrets


# CLASS: project.core.logging.logger_events_errors.SemanticLoggerIssueEventsMixin
# SUMMARY: Mixin implementing error and critical issue helpers with causal-link metadata support.
class SemanticLoggerIssueEventsMixin:
    # FUNCTION: log_error
    # SUMMARY: Log error events with exception details, context information, and causal linking.
    # INPUT: exception (Optional[Exception]): Actual exception object if available.
    # INPUT: caused_by (Optional[str]): Explicit error_id that caused this error.
    def log_error(
        self: SemanticLoggerEventContract,
        error_type: str,
        message: str,
        *,
        exception: Optional[Exception] = None,
        exc_info: bool = False,
        caused_by: Optional[str] = None,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        # **LOGIC_STEP**: Scrub secrets from caller-supplied text before it reaches any handler.
        message = redact_secrets(message)
        payload: dict[str, Any] = {"error_type": error_type, "message": message}
        if exception is not None:
            payload["exception_type"] = type(exception).__name__
            # **LOGIC_STEP**: Exception text is untrusted — clients embed keys and DSNs in it.
            payload["exception_message"] = redact_secrets(str(exception))

        related_id = caused_by or get_current_error_id()
        if related_id:
            payload["related_error_id"] = related_id

        payload.update(extra)
        self.log_event(
            EventType.ISSUE_ERROR,
            message,
            level=logging.ERROR,
            event_id=f"error.{error_type}",
            exc_info=exc_info,
            _caller=_caller,
            data=payload,
        )

    # FUNCTION: log_critical
    # SUMMARY: Log critical failure events with impact assessment and causal linking.
    # INPUT: exception (Optional[Exception]): Actual exception object if available.
    # INPUT: caused_by (Optional[str]): Explicit error_id that caused this critical failure.
    def log_critical(
        self: SemanticLoggerEventContract,
        failure_type: str,
        message: str,
        impact_scope: str,
        *,
        exception: Optional[Exception] = None,
        exc_info: bool = False,
        caused_by: Optional[str] = None,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        # **LOGIC_STEP**: Scrub secrets from caller-supplied text before it reaches any handler.
        message = redact_secrets(message)
        payload: dict[str, Any] = {
            "failure_type": failure_type,
            "message": message,
            "impact_scope": impact_scope,
        }
        if exception is not None:
            payload["exception_type"] = type(exception).__name__
            # **LOGIC_STEP**: Exception text is untrusted — clients embed keys and DSNs in it.
            payload["exception_message"] = redact_secrets(str(exception))

        related_id = caused_by or get_current_error_id()
        if related_id:
            payload["related_error_id"] = related_id

        payload.update(extra)
        self.log_event(
            EventType.ISSUE_CRITICAL,
            message,
            level=logging.CRITICAL,
            event_id=f"critical.{failure_type}",
            exc_info=exc_info,
            _caller=_caller,
            data=payload,
        )
