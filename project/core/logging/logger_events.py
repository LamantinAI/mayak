# FILE: project/core/logging/logger_events.py
# SUMMARY: Semantic logging facade mixin that composes the smaller event-helper families.

from project.core.logging.logger_events_errors import SemanticLoggerIssueEventsMixin
from project.core.logging.logger_events_operational import (
    SemanticLoggerOperationalEventsMixin,
)
from project.core.logging.logger_events_state import SemanticLoggerStateEventsMixin


# CLASS: project.core.logging.logger_events.SemanticLoggerEventsMixin
# SUMMARY: Facade mixin composing state, operational, and issue-specific semantic logging helpers.
class SemanticLoggerEventsMixin(
    SemanticLoggerStateEventsMixin,
    SemanticLoggerOperationalEventsMixin,
    SemanticLoggerIssueEventsMixin,
):
    pass
