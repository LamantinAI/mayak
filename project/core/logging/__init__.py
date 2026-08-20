# FILE: project/core/logging/__init__.py
# SUMMARY: AI-optimized semantic logging system with structured NDJSON output, event classification and context propagation.

from project.core.logging.config import get_logging_config, setup_logging
from project.core.logging.file_manager import create_run_log_path, rotate_log_files
from project.core.logging.context import (
    get_current_error_id,
    get_span_stats,
    get_trace_id,
    increment_span_stat,
    init_span_stats,
    reset_current_error_id,
    reset_span_stats,
    reset_trace_id,
    set_current_error_id,
    set_trace_id,
)
from project.core.logging.enums import EventType
from project.core.logging.logger import SemanticLogger, SpanContext, get_logger
from project.core.logging.logger_types import CallerOverrideFilter
from project.core.logging.redaction import (
    summarize_chat_messages,
    summarize_mapping,
    summarize_payload,
    summarize_text,
)
from project.core.logging.trace_formatter import (
    format_all_traces_for_llm,
    format_trace_for_llm,
    prepend_trace_summary,
)

__all__ = [
    "CallerOverrideFilter",
    "EventType",
    "create_run_log_path",
    "SemanticLogger",
    "SpanContext",
    "get_current_error_id",
    "get_logger",
    "get_logging_config",
    "get_span_stats",
    "get_trace_id",
    "increment_span_stat",
    "init_span_stats",
    "reset_current_error_id",
    "reset_span_stats",
    "reset_trace_id",
    "rotate_log_files",
    "set_current_error_id",
    "set_trace_id",
    "setup_logging",
    "summarize_chat_messages",
    "summarize_mapping",
    "summarize_payload",
    "summarize_text",
    "format_all_traces_for_llm",
    "format_trace_for_llm",
    "prepend_trace_summary",
]
