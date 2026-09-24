# FILE: project/core/logging/context.py
# SUMMARY: Context management for semantic logging using ContextVars.

import contextvars
from typing import Any, Optional

# ==================== CONTEXT VARS ====================

# SUMMARY: Mutable statistics payload collected for the currently active root span.
SpanStats = dict[str, int]

# SUMMARY: Flat metadata dictionary propagated through nested logging scopes.
ContextData = dict[str, Any]

# SUMMARY: ContextVar storing the flat metadata attached to the current execution scope.
_current_ctx: contextvars.ContextVar[ContextData] = contextvars.ContextVar(
    "ai_logger_ctx", default={}
)
# SUMMARY: ContextVar storing the active span identifier.
_current_span: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "ai_span_id", default=None
)
# SUMMARY: ContextVar storing the active span name.
_current_span_name: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "ai_span_name", default=None
)
# SUMMARY: ContextVar storing the active request trace identifier.
_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "ai_trace_id", default=None
)
# SUMMARY: ContextVar storing the active error identifier for causal linking.
_current_error_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "ai_error_id", default=None
)
# SUMMARY: ContextVar storing counters aggregated for the current root span.
_span_stats: contextvars.ContextVar[Optional[SpanStats]] = contextvars.ContextVar(
    "ai_span_stats", default=None
)

# ==================== CONTEXT MANAGEMENT ====================


# SUMMARY: Get current logging context from ContextVars.
def get_current_context() -> ContextData:
    # Retrieve current context from ContextVar.
    return _current_ctx.get() or {}


# SUMMARY: Get current span ID and name from ContextVars.
# OUTPUT: (tuple): Tuple of (span_id, span_name).
def get_current_span() -> tuple[Optional[str], Optional[str]]:
    # Retrieve current span ID and name from ContextVars.
    return _current_span.get(), _current_span_name.get()


# SUMMARY: Set logging context with new metadata.
def set_context(**kv: Any) -> contextvars.Token[ContextData]:
    # Merge current context with new metadata and set token.
    token = _current_ctx.set({**(get_current_context()), **kv})
    return token


# SUMMARY: Set span context with ID and name.
# OUTPUT: (tuple): Tuple of (span_token, name_token) for resetting later.
def set_span(
    span_id: str, span_name: str
) -> tuple[contextvars.Token[Optional[str]], contextvars.Token[Optional[str]]]:
    # Set span ID and name context variables.
    span_token = _current_span.set(span_id)
    name_token = _current_span_name.set(span_name)
    return span_token, name_token


# SUMMARY: Reset context to previous state using token.
# INPUT: token (contextvars.Token): Token from set_context call.
def reset_context(token: contextvars.Token[ContextData]) -> None:
    # Reset context to previous state.
    _current_ctx.reset(token)


# SUMMARY: Reset span context to previous state using tokens.
# INPUT: span_token (contextvars.Token): Token from set_span call.
# INPUT: name_token (contextvars.Token): Token from set_span call.
def reset_span(
    span_token: contextvars.Token[Optional[str]],
    name_token: contextvars.Token[Optional[str]],
) -> None:
    # Reset span context variables to previous state.
    _current_span.reset(span_token)
    _current_span_name.reset(name_token)


# ==================== TRACE ID ====================


# SUMMARY: Get current trace ID from ContextVar.
def get_trace_id() -> Optional[str]:
    # Retrieve current trace ID from ContextVar.
    return _trace_id.get()


# SUMMARY: Set trace ID for request-scoped correlation.
def set_trace_id(trace_id: str) -> contextvars.Token[Optional[str]]:
    # Set trace ID context variable and return reset token.
    return _trace_id.set(trace_id)


# SUMMARY: Reset trace ID to previous state using token.
# INPUT: token (contextvars.Token): Token from set_trace_id call.
def reset_trace_id(token: contextvars.Token[Optional[str]]) -> None:
    # Reset trace ID to previous state.
    _trace_id.reset(token)


# ==================== ERROR ID ====================


# SUMMARY: Get current error ID from ContextVar for causal linking.
def get_current_error_id() -> Optional[str]:
    # Retrieve current error ID from ContextVar.
    return _current_error_id.get()


# SUMMARY: Set error ID for causal linking between span errors and log_error calls.
def set_current_error_id(error_id: str) -> contextvars.Token[Optional[str]]:
    # Set error ID context variable and return reset token.
    return _current_error_id.set(error_id)


# SUMMARY: Reset error ID to previous state using token.
# INPUT: token (contextvars.Token): Token from set_current_error_id call.
def reset_current_error_id(token: contextvars.Token[Optional[str]]) -> None:
    # Reset error ID to previous state.
    _current_error_id.reset(token)


# ==================== SPAN STATS ====================


# SUMMARY: Get current span stats dictionary from ContextVar.
# OUTPUT: (Optional[dict]): Current span stats or None if not in a root span.
def get_span_stats() -> Optional[SpanStats]:
    # Retrieve current span stats from ContextVar.
    return _span_stats.get()


# SUMMARY: Initialize span stats for a root span with zeroed counters.
def init_span_stats() -> contextvars.Token[Optional[SpanStats]]:
    # Create new stats dict with zeroed counters and set ContextVar.
    return _span_stats.set(
        {
            "child_span_count": 0,
            "error_count": 0,
            "llm_calls": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
    )


# SUMMARY: Increment a counter in the current span stats.
# INPUT: value (int): Value to add (default 1).
def increment_span_stat(key: str, value: int = 1) -> None:
    # Get current stats and increment specified key if stats exist.
    stats = _span_stats.get()
    if stats is not None:
        stats[key] = stats.get(key, 0) + value


# SUMMARY: Reset span stats to previous state using token.
# INPUT: token (contextvars.Token): Token from init_span_stats call.
def reset_span_stats(token: contextvars.Token[Optional[SpanStats]]) -> None:
    # Reset span stats to previous state.
    _span_stats.reset(token)
