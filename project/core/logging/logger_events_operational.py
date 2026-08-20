# FILE: project/core/logging/logger_events_operational.py
# SUMMARY: Semantic logging mixin implementing external-call, user-input, system, metric, and warning helpers.

import logging
from typing import Any, Optional

from project.core.logging.context import increment_span_stat
from project.core.logging.enums import EventType
from project.core.logging.logger_events_base import SemanticLoggerEventContract
from project.core.logging.logger_types import LogPayload, LogValue


# CLASS: project.core.logging.logger_events_operational.SemanticLoggerOperationalEventsMixin
# SUMMARY: Mixin implementing operational logging helpers for API, database, system, and LLM telemetry.
class SemanticLoggerOperationalEventsMixin:
    # FUNCTION: log_api_call
    # SUMMARY: Log external API interactions with performance metadata.
    def log_api_call(
        self: SemanticLoggerEventContract,
        endpoint: str,
        method: str,
        *,
        status_code: int,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        payload: LogPayload = {
            "endpoint": endpoint,
            "method": method,
            "status_code": status_code,
        }
        payload.update(extra)
        self.log_event(
            EventType.EXTERNAL_API,
            f"API call {method} {endpoint} returned {status_code}",
            level=logging.INFO,
            event_id=f"api.call.{method}.{endpoint}",
            _caller=_caller,
            data=payload,
        )

    # FUNCTION: log_database_operation
    # SUMMARY: Log database operations with impact metadata.
    def log_database_operation(
        self: SemanticLoggerEventContract,
        operation: str,
        database: str,
        *,
        affected_rows: Optional[int] = None,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        payload: LogPayload = {
            "operation": operation,
            "database": database,
        }
        if affected_rows is not None:
            payload["affected_rows"] = affected_rows
        payload.update(extra)
        self.log_event(
            EventType.DATABASE,
            f"Database {operation} on {database}",
            level=logging.INFO,
            event_id=f"database.{operation}.{database}",
            _caller=_caller,
            data=payload,
        )

    # FUNCTION: log_user_input
    # SUMMARY: Log user interaction events using only safe structural summaries.
    def log_user_input(
        self: SemanticLoggerEventContract,
        user_id: str,
        input_type: str,
        *,
        message_summary: Optional[dict[str, Any]] = None,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        payload: LogPayload = {"user_id": user_id, "input_type": input_type}
        if message_summary is not None:
            payload["message_summary"] = message_summary
        payload.update(extra)
        self.log_event(
            EventType.USER_INPUT,
            f"User input from {user_id}: {input_type}",
            level=logging.INFO,
            event_id=f"user.input.{input_type}",
            _caller=_caller,
            data=payload,
        )

    # FUNCTION: log_system_event
    # SUMMARY: Log system-level events for monitoring and auditing.
    def log_system_event(
        self: SemanticLoggerEventContract,
        event_name: str,
        category: str,
        *,
        old_value: Optional[LogValue] = None,
        new_value: Optional[LogValue] = None,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        payload: LogPayload = {"event_name": event_name, "category": category}
        if old_value is not None:
            payload["old_value"] = old_value
        if new_value is not None:
            payload["new_value"] = new_value
        payload.update(extra)
        self.log_event(
            EventType.SYSTEM_EVENT,
            f"System event: {event_name} ({category})",
            level=logging.INFO,
            event_id=f"system.{event_name}",
            _caller=_caller,
            data=payload,
        )

    # FUNCTION: log_metric
    # SUMMARY: Log a numeric metric value with optional unit and tags.
    def log_metric(
        self: SemanticLoggerEventContract,
        metric_name: str,
        value: int | float,
        *,
        unit: Optional[str] = None,
        tags: Optional[dict[str, Any]] = None,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        payload: dict[str, Any] = {"metric_name": metric_name, "value": value}
        if unit is not None:
            payload["unit"] = unit
        if tags is not None:
            payload["tags"] = tags
        payload.update(extra)
        self.log_event(
            EventType.METRIC,
            f"Metric {metric_name} = {value}",
            level=logging.INFO,
            event_id=f"metric.{metric_name}",
            _caller=_caller,
            data=payload,
        )

    # FUNCTION: log_llm_call
    # SUMMARY: Log an individual LLM invocation with timing, token usage, and success status.
    # INPUT: error (Optional[str]): Error message if the call failed.
    def log_llm_call(
        self: SemanticLoggerEventContract,
        model: str,
        *,
        duration_ms: float,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        success: bool = True,
        error: Optional[str] = None,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        increment_span_stat("llm_calls")
        if input_tokens:
            increment_span_stat("total_input_tokens", input_tokens)
        if output_tokens:
            increment_span_stat("total_output_tokens", output_tokens)

        payload: dict[str, Any] = {
            "model": model,
            "duration_ms": round(duration_ms, 3),
            "success": success,
        }
        if input_tokens is not None:
            payload["input_tokens"] = input_tokens
        if output_tokens is not None:
            payload["output_tokens"] = output_tokens
        if total_tokens is not None:
            payload["total_tokens"] = total_tokens
        if error is not None:
            payload["error"] = error
        payload.update(extra)

        level = logging.INFO if success else logging.WARNING
        self.log_event(
            EventType.LLM_CALL,
            f"LLM call to {model}: {'ok' if success else 'failed'} in {round(duration_ms, 1)}ms",
            level=level,
            event_id="llm.call",
            _caller=_caller,
            data=payload,
        )

    # FUNCTION: log_warning
    # SUMMARY: Log warning events for non-critical issues that need attention.
    def log_warning(
        self: SemanticLoggerEventContract,
        warning_type: str,
        message: str,
        *,
        affected_component: Optional[str] = None,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        payload: LogPayload = {"warning_type": warning_type, "message": message}
        if affected_component is not None:
            payload["affected_component"] = affected_component
        payload.update(extra)
        self.log_event(
            EventType.ISSUE_WARNING,
            message,
            level=logging.WARNING,
            event_id=f"warning.{warning_type}",
            _caller=_caller,
            data=payload,
        )
