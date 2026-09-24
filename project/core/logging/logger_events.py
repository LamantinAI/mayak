# FILE: project/core/logging/logger_events.py
# SUMMARY: Semantic logging facade mixin that composes the smaller event-helper families.

from project.core.logging.logger_events_errors import SemanticLoggerIssueEventsMixin
from project.core.logging.logger_events_operational import (
    SemanticLoggerOperationalEventsMixin,
)
from project.core.logging.logger_events_state import SemanticLoggerStateEventsMixin


class SemanticLoggerEventsMixin(
    SemanticLoggerStateEventsMixin,
    SemanticLoggerOperationalEventsMixin,
    SemanticLoggerIssueEventsMixin,
):
    pass
