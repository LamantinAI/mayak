# FILE: project/core/logging/logger_types.py
# SUMMARY: Shared structured payload aliases and span context types used across semantic logging modules.

import logging
import os
from typing import Any, Optional, TypeAlias


# SUMMARY: Caller information tuple containing pathname, line number, and function name.
CallerInfo: TypeAlias = tuple[str, int, str]

# SUMMARY: Primitive scalar value allowed inside structured logging payloads.
LogScalar: TypeAlias = str | int | float | bool | None

# SUMMARY: Recursive structured value allowed in semantic logging payloads.
LogValue: TypeAlias = LogScalar | dict[str, Any] | list[Any]

# SUMMARY: Mapping type used for structured semantic logging payloads.
LogPayload: TypeAlias = dict[str, LogValue]


# SUMMARY: Context object yielded by span(), carrying span_id and optional output metadata.
class SpanContext:
    # SUMMARY: Initialize SpanContext with span_id, empty output, and optional error_id.
    def __init__(self, span_id: str):
        self.span_id = span_id
        self.output: LogPayload = {}
        # SUMMARY: UUID assigned when an exception occurs within the span for cross-span error correlation.
        self.error_id: Optional[str] = None

    # SUMMARY: Return span_id as string representation for backward compatibility.
    def __str__(self) -> str:
        return self.span_id


# SUMMARY: Logging filter that overrides record source location from the caller override metadata.
class CallerOverrideFilter(logging.Filter):
    # SUMMARY: Override record pathname, line number, and function name from _caller_override if present.
    # OUTPUT: (bool): Always True because the filter never suppresses records.
    def filter(self, record: logging.LogRecord) -> bool:
        # Check for caller override metadata and apply it to the log record when present.
        caller: Optional[CallerInfo] = getattr(record, "_caller_override", None)
        if caller is not None:
            record.pathname, record.lineno, record.funcName = caller
            record.filename = os.path.basename(caller[0])
        return True
