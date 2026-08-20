# FILE: project/core/logging/formatters.py
# SUMMARY: NDJSON formatter for structured logging with semantic event classification and LLM-friendly output.

import itertools
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any

import orjson

from project.core.logging.context import get_trace_id
from project.core.logging.redaction import redact_traceback
from project.core.serialization import safe_serialize

# ATTRIBUTE: _PROJECT_ROOT (str)
# SUMMARY: Absolute repository root used to compute compact relative file paths in log payloads.
# Pre-compute project root for relative path calculation
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

# ATTRIBUTE: _seq_counter (itertools.count)
# SUMMARY: Monotonically increasing sequence counter for guaranteed log ordering within a process.
_seq_counter = itertools.count(1)


# ==================== NDJSON FORMATTER ====================


# CLASS: project.core.logging.formatters.NDJSONFormatter
# SUMMARY: NDJSON formatter producing one JSON object per line with flat keys for LLM-friendly parsing.
# EXTENDS: logging.Formatter
class NDJSONFormatter(logging.Formatter):
    # FUNCTION: project/core/logging/formatters/NDJSONFormatter/format
    # SUMMARY: Formats the log record into a single NDJSON line with flat structure.
    def format(self, record: logging.LogRecord) -> str:
        # **LOGIC_STEP**: Build base record with sequence number, pid, timestamp, level, logger name.
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        entry: dict[str, Any] = {
            "seq": next(_seq_counter),
            "pid": os.getpid(),
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # **LOGIC_STEP**: Add trace_id from ContextVar if present.
        trace_id = get_trace_id()
        if trace_id:
            entry["trace_id"] = trace_id

        # **LOGIC_STEP**: Add span information if present.
        span = getattr(record, "span", None)
        if span and isinstance(span, dict):
            if "span_id" in span:
                entry["span_id"] = span["span_id"]
            if "name" in span:
                entry["span_name"] = span["name"]

        # **LOGIC_STEP**: Compute relative source location from record.
        try:
            rel_path = os.path.relpath(record.pathname, _PROJECT_ROOT)
        except ValueError:
            rel_path = record.pathname
        entry["file"] = rel_path
        entry["line"] = record.lineno
        entry["func"] = record.funcName

        # **LOGIC_STEP**: Namespace payload under "data" key to avoid key collisions.
        payload = getattr(record, "payload", None)
        if payload:
            data = safe_serialize(payload)
            if isinstance(data, dict):
                # **LOGIC_STEP**: Promote event_id from data to top-level for fast filtering.
                if "event_id" in data:
                    entry["event_id"] = data.pop("event_id")
                # **LOGIC_STEP**: Promote business-context and tracing fields to top-level
                # for fast indexing in log aggregators (ELK, Loki, Datadog).
                for _ctx_key in (
                    "session_id",
                    "user_id",
                    "parent_span_id",
                    "duration_ms",
                ):
                    if _ctx_key in data:
                        entry[_ctx_key] = data.pop(_ctx_key)
                # **LOGIC_STEP**: Remove fields already promoted to top-level to avoid duplication.
                for _dup_key in ("span_id", "name", "event_type"):
                    data.pop(_dup_key, None)
                # **LOGIC_STEP**: Strip request-scoped routing fields from non-root events.
                # Root span events (parent_span_id=null) and request.summary keep them;
                # all other events within a trace rely on trace_id for correlation.
                if trace_id and isinstance(data, dict):
                    _is_root = "parent_span_id" in entry and entry["parent_span_id"] is None
                    _is_summary = entry.get("event_id") == "request.summary"
                    if not (_is_root or _is_summary):
                        for _req_key in ("request_id", "method", "path"):
                            data.pop(_req_key, None)
                # **LOGIC_STEP**: Last-resort scrub for free-form text fields built outside
                # the semantic logger, which redacts them at the source.
                for _text_key in ("exception_message", "message"):
                    _text_value = data.get(_text_key)
                    if isinstance(_text_value, str):
                        data[_text_key] = redact_traceback(_text_value)
            entry["data"] = data

        # **LOGIC_STEP**: Add exception traceback if present.
        if record.exc_info and record.exc_info[1] is not None:
            raw_tb = "".join(traceback.format_exception(*record.exc_info))
            entry["exc_traceback"] = redact_traceback(raw_tb)

        # **LOGIC_STEP**: Serialize to single-line JSON via orjson.
        return orjson.dumps(entry, option=orjson.OPT_NON_STR_KEYS).decode("utf-8")
