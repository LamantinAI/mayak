# FILE: project/core/logging/enums.py
# SUMMARY: Semantic event type definitions for AI classification of log entries.

from enum import Enum


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

    # One LLM invocation, with its timing and token usage.
    LLM_CALL = "llm.call"

    # The aggregate emitted when a root span ends.
    REQUEST_SUMMARY = "request.summary"


# How a root span ended, reported by request.summary so a reader can filter real failures.
# The request finished with a 2xx/3xx response, or the span carried no HTTP status at all.
# The request finished with a 4xx response — routine, logged at INFO, not an alarm.
# The request finished with a 5xx response or the span raised — logged at ERROR.
# The span was cut short by something that is not an Exception — asyncio cancellation
# at uvicorn's graceful-shutdown timeout, KeyboardInterrupt, SystemExit, or GeneratorExit when
# a span's generator is closed before its body finished — and produced no HTTP result. Logged
# at WARNING: not the application's failure, but a request that did not finish.
class RequestOutcome(Enum):
    OK = "ok"
    CLIENT_ERROR = "client_error"
    SERVER_ERROR = "server_error"
    CANCELLED = "cancelled"
