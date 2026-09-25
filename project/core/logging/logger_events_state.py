# FILE: project/core/logging/logger_events_state.py
# SUMMARY: Semantic logging mixin implementing state, data, decision, and validation helper methods.

import logging
from typing import Any, Optional

from project.core.logging.enums import EventType
from project.core.logging.logger_events_base import SemanticLoggerEventContract
from project.core.logging.logger_types import LogPayload, LogValue


class SemanticLoggerStateEventsMixin:
    def log_state_change(
        self: SemanticLoggerEventContract,
        entity: str,
        *,
        changes: dict[str, Any],
        change_reason: Optional[str] = None,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        payload: LogPayload = {"entity": entity, "changes": changes}
        if change_reason is not None:
            payload["change_reason"] = change_reason
        payload.update(extra)
        self.log_event(
            EventType.STATE_CHANGE,
            f"State changed: {entity}",
            level=logging.DEBUG,
            event_id=f"state.change.{entity}",
            _caller=_caller,
            data=payload,
        )

    def log_state_snapshot(
        self: SemanticLoggerEventContract,
        entity: str,
        *,
        snapshot: dict[str, Any],
        capture_reason: Optional[str] = None,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        payload: LogPayload = {"entity": entity, "snapshot": snapshot}
        if capture_reason is not None:
            payload["capture_reason"] = capture_reason
        payload.update(extra)
        self.log_event(
            EventType.STATE_SNAPSHOT,
            f"State snapshot: {entity}",
            level=logging.DEBUG,
            event_id=f"state.snapshot.{entity}",
            _caller=_caller,
            data=payload,
        )
