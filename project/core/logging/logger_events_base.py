# FILE: project/core/logging/logger_events_base.py
# SUMMARY: Structural contract every semantic-logging mixin depends on: caller resolution and log_event.

import logging
from typing import Optional, Protocol

from project.core.logging.enums import EventType
from project.core.logging.logger_types import CallerInfo, LogPayload, LogValue


class SemanticLoggerEventContract(Protocol):
    # For log-source attribution.
    # Returns caller pathname, line number, and function name.
    def _resolve_caller(self) -> CallerInfo: ...

    def log_event(
        self,
        event_type: EventType,
        msg: str,
        *,
        level: int = logging.INFO,
        exc_info: bool = False,
        event_id: Optional[str] = None,
        _caller: Optional[CallerInfo] = None,
        data: Optional[LogPayload] = None,
        **kwargs: LogValue,
    ) -> None: ...
