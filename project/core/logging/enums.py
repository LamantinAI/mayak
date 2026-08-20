# FILE: project/core/logging/enums.py
# SUMMARY: Semantic event type definitions for AI classification of log entries.

from enum import Enum


# ENUM: project.core.logging.enums.EventType
# SUMMARY: Semantic event types for AI classification of log entries.
# ATTRIBUTE: FLOW_START (str)
# ATTRIBUTE: FLOW_END (str)
# ATTRIBUTE: STATE_CHANGE (str)
# ATTRIBUTE: STATE_SNAPSHOT (str)
# ATTRIBUTE: DATA_LOAD (str)
# ATTRIBUTE: DATA_TRANSFORM (str)
# ATTRIBUTE: DATA_SAVE (str)
# ATTRIBUTE: DECISION (str)
# ATTRIBUTE: VALIDATION (str)
# ATTRIBUTE: EXTERNAL_API (str)
# ATTRIBUTE: DATABASE (str)
# ATTRIBUTE: USER_INPUT (str)
# ATTRIBUTE: SYSTEM_EVENT (str)
# ATTRIBUTE: METRIC (str)
# ATTRIBUTE: ISSUE_WARNING (str)
# ATTRIBUTE: ISSUE_ERROR (str)
# ATTRIBUTE: ISSUE_CRITICAL (str)
# ATTRIBUTE: LLM_CALL (str)
# SUMMARY: Tracks individual LLM invocations with timing and token usage.
# ATTRIBUTE: REQUEST_SUMMARY (str)
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


# ENUM: project.core.logging.enums.RequestOutcome
# SUMMARY: How a root span ended, reported by request.summary so a reader can filter real failures.
# ATTRIBUTE: OK (str)
# SUMMARY: The request finished with a 2xx/3xx response, or the span carried no HTTP status at all.
# ATTRIBUTE: CLIENT_ERROR (str)
# SUMMARY: The request finished with a 4xx response — routine, logged at INFO, not an alarm.
# ATTRIBUTE: SERVER_ERROR (str)
# SUMMARY: The request finished with a 5xx response or the span raised — logged at ERROR.
class RequestOutcome(Enum):
    OK = "ok"
    CLIENT_ERROR = "client_error"
    SERVER_ERROR = "server_error"
