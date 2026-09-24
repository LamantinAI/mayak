# FILE: project/core/logging/enums.py
# SUMMARY: Semantic event type definitions for AI classification of log entries.

from enum import Enum


# SUMMARY: Semantic event types for AI classification of log entries.
# SUMMARY: Tracks individual LLM invocations with timing and token usage.
# SUMMARY: Aggregated summary emitted at the end of a root span.
class EventType(Enum):
    # Execution Flow
    FLOW_START = "flow.start"
    FLOW_END = "flow.end"

    # State Management
    STATE_CHANGE = "state.change"
    STATE_SNAPSHOT = "state.snapshot"

    # Data Operations
    DATA_LOAD = "data.load"
    DATA_TRANSFORM = "data.transform"
    DATA_SAVE = "data.save"

    # Decision & Logic
    DECISION = "decision"
    VALIDATION = "validation"

    # External Interactions
    EXTERNAL_API = "external.api"
    DATABASE = "database"

    # User & System
    USER_INPUT = "user.input"
    SYSTEM_EVENT = "system.event"

    # Metrics
    METRIC = "metric"

    # Issues
    ISSUE_WARNING = "issue.warning"
    ISSUE_ERROR = "issue.error"
    ISSUE_CRITICAL = "issue.critical"

    # LLM
    LLM_CALL = "llm.call"

    # Request lifecycle
    REQUEST_SUMMARY = "request.summary"


# SUMMARY: How a root span ended, reported by request.summary so a reader can filter real failures.
# SUMMARY: The request finished with a 2xx/3xx response, or the span carried no HTTP status at all.
# SUMMARY: The request finished with a 4xx response — routine, logged at INFO, not an alarm.
# SUMMARY: The request finished with a 5xx response or the span raised — logged at ERROR.
# SUMMARY: The span was cut short by something that is not an Exception — asyncio cancellation
# at uvicorn's graceful-shutdown timeout, KeyboardInterrupt, SystemExit, or GeneratorExit when
# a span's generator is closed before its body finished — and produced no HTTP result. Logged
# at WARNING: not the application's failure, but a request that did not finish.
class RequestOutcome(Enum):
    OK = "ok"
    CLIENT_ERROR = "client_error"
    SERVER_ERROR = "server_error"
    CANCELLED = "cancelled"
