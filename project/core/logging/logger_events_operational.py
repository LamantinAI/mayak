# FILE: project/core/logging/logger_events_operational.py
# SUMMARY: Semantic logging mixin implementing external-call, user-input, system, metric, and warning helpers.

import logging
from typing import Any, Optional

from project.core.logging.context import increment_span_stat
from project.core.logging.enums import EventType
from project.core.logging.logger_events_base import SemanticLoggerEventContract
from project.core.logging.logger_types import LogPayload, LogValue

# ATTRIBUTE: _TRUNCATED_FINISH_REASONS (frozenset[str])
# SUMMARY: finish_reason values meaning the provider stopped because it hit a limit, not because
# it was done — a completion cut off mid-JSON by the output-token or tool-schema cap.
# NOTE: Without this, a response truncated this way reads as an ordinary successful `llm.call`:
# `success=True`, INFO level, nothing distinguishing it from a normal reply — the
# response_metadata langchain hands back on every call already carries `finish_reason`, and
# nothing reads it. An agent in a live-run experiment on this template spent the whole stage of
# that run diagnosing a tool-argument Decimal field the model never finished writing, because
# the log said the call succeeded. "length" is the one OpenAI-compatible value this means; the
# others (`stop`, `tool_calls`, `content_filter`, `function_call`) are normal completions and
# stay INFO.
_TRUNCATED_FINISH_REASONS = frozenset({"length"})


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

        # **LOGIC_STEP**: `finish_reason` travels in through `**extra` (llm_service_live.py reads
        # it off the response's `response_metadata`) rather than as its own keyword, because a mock
        # response and any provider that answers without one must not be forced to pass None
        # through a required argument. Reading it back out of `payload` — after `extra` has been
        # folded in — is what lets this one check cover a truncated call whatever else the caller
        # sent alongside it.
        # **LOGIC_STEP**: `extra` is typed LogValue, which admits a list and a dict, and a
        # membership test against a frozenset raises TypeError on an unhashable one — a provider
        # returning a malformed `finish_reason` would then crash the logging call rather than the
        # request it was describing. Only a string can name a stop reason, so anything else is
        # simply not one.
        finish_reason = payload.get("finish_reason")
        truncated = (
            success
            and isinstance(finish_reason, str)
            and finish_reason in _TRUNCATED_FINISH_REASONS
        )

        level = logging.WARNING if (not success or truncated) else logging.INFO
        msg = f"LLM call to {model}: {'ok' if success else 'failed'} in {round(duration_ms, 1)}ms"
        if truncated:
            # **LOGIC_STEP**: Visible in the message itself, not only in a field a reader has to
            # know to look for — the whole point is that this stopped reading as an ordinary
            # success.
            msg = f"{msg} (truncated: finish_reason={finish_reason})"
        self.log_event(
            EventType.LLM_CALL,
            msg,
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
