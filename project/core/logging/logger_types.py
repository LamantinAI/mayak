# FILE: project/core/logging/logger_types.py
# SUMMARY: Shared structured payload aliases and span context types used across semantic logging modules.

import logging
import os
from typing import Any, Optional, TypeAlias


# ATTRIBUTE: CallerInfo (TypeAlias)
# SUMMARY: Caller information tuple containing pathname, line number, and function name.
CallerInfo: TypeAlias = tuple[str, int, str]

# ATTRIBUTE: LogScalar (TypeAlias)
# SUMMARY: Primitive scalar value allowed inside structured logging payloads.
LogScalar: TypeAlias = str | int | float | bool | None

# ATTRIBUTE: LogValue (TypeAlias)
# SUMMARY: Recursive structured value allowed in semantic logging payloads.
LogValue: TypeAlias = LogScalar | dict[str, Any] | list[Any]

# ATTRIBUTE: LogPayload (TypeAlias)
# SUMMARY: Mapping type used for structured semantic logging payloads.
LogPayload: TypeAlias = dict[str, LogValue]


# CLASS: project.core.logging.logger_types.SpanContext
# SUMMARY: Context object yielded by span(), carrying span_id and optional output metadata.
class SpanContext:
    # FUNCTION: __init__
    # SUMMARY: Initialize SpanContext with span_id, empty output, and optional error_id.
    def __init__(self, span_id: str):
        self.span_id = span_id
        self.output: LogPayload = {}
        # ATTRIBUTE: error_id (Optional[str])
        # SUMMARY: UUID assigned when an exception occurs within the span for cross-span error correlation.
        self.error_id: Optional[str] = None

    # FUNCTION: __str__
    # SUMMARY: Return span_id as string representation for backward compatibility.
    def __str__(self) -> str:
        return self.span_id


# CLASS: project.core.logging.logger_types.CallerOverrideFilter
# SUMMARY: Logging filter that overrides record source location from the caller override metadata.
# EXTENDS: logging.Filter
class CallerOverrideFilter(logging.Filter):
    # FUNCTION: filter
    # SUMMARY: Override record pathname, line number, and function name from _caller_override if present.
    # OUTPUT: (bool): Always True because the filter never suppresses records.
    def filter(self, record: logging.LogRecord) -> bool:
        # **LOGIC_STEP**: Check for caller override metadata and apply it to the log record when present.
        caller: Optional[CallerInfo] = getattr(record, "_caller_override", None)
        if caller is not None:
            record.pathname, record.lineno, record.funcName = caller
            record.filename = os.path.basename(caller[0])
        return True
