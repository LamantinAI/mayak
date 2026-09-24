# FILE: project/core/logging/logger_types.py
# SUMMARY: Shared structured payload aliases and span context types used across semantic logging modules.

import logging
import os
from typing import Any, Optional, TypeAlias


# Caller information tuple containing pathname, line number, and function name.
CallerInfo: TypeAlias = tuple[str, int, str]

LogScalar: TypeAlias = str | int | float | bool | None

# Recursive.
LogValue: TypeAlias = LogScalar | dict[str, Any] | list[Any]

LogPayload: TypeAlias = dict[str, LogValue]


# Yielded by span().
class SpanContext:
    def __init__(self, span_id: str):
        self.span_id = span_id
        self.output: LogPayload = {}
        # UUID assigned when an exception occurs within the span for cross-span error correlation.
        self.error_id: Optional[str] = None

    # For backward compatibility.
    def __str__(self) -> str:
        return self.span_id


class CallerOverrideFilter(logging.Filter):
    # Always True because the filter never suppresses records.
    def filter(self, record: logging.LogRecord) -> bool:
        # Check for caller override metadata and apply it to the log record when present.
        caller: Optional[CallerInfo] = getattr(record, "_caller_override", None)
        if caller is not None:
            record.pathname, record.lineno, record.funcName = caller
            record.filename = os.path.basename(caller[0])
        return True
