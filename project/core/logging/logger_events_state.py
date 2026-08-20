# FILE: project/core/logging/logger_events_state.py
# SUMMARY: Semantic logging mixin implementing state, data, decision, and validation helper methods.

import logging
from typing import Any, Optional

from project.core.logging.enums import EventType
from project.core.logging.logger_events_base import SemanticLoggerEventContract
from project.core.logging.logger_types import LogPayload, LogValue


# CLASS: project.core.logging.logger_events_state.SemanticLoggerStateEventsMixin
# SUMMARY: Mixin implementing state snapshots, data operation events, and decision logging helpers.
class SemanticLoggerStateEventsMixin:
    # FUNCTION: log_state_change
    # SUMMARY: Log changes to entity state with detailed diff information.
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

    # FUNCTION: log_state_snapshot
    # SUMMARY: Log a snapshot of the current state for debugging or auditing.
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

    # FUNCTION: log_data_load
    # SUMMARY: Log data loading operations from various sources.
    def log_data_load(
        self: SemanticLoggerEventContract,
        source: str,
        *,
        record_count: int,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        payload: LogPayload = {"source": source, "record_count": record_count}
        payload.update(extra)
        self.log_event(
            EventType.DATA_LOAD,
            f"Loaded {record_count} records from {source}",
            level=logging.DEBUG,
            event_id=f"data.load.{source}",
            _caller=_caller,
            data=payload,
        )

    # FUNCTION: log_data_transform
    # SUMMARY: Log data transformation operations with input and output metrics.
    def log_data_transform(
        self: SemanticLoggerEventContract,
        operation: str,
        *,
        records_input: int,
        records_output: int,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        payload: LogPayload = {
            "operation": operation,
            "records_input": records_input,
            "records_output": records_output,
        }
        payload.update(extra)
        self.log_event(
            EventType.DATA_TRANSFORM,
            f"Transformed {records_input} -> {records_output} in {operation}",
            level=logging.DEBUG,
            event_id=f"data.transform.{operation}",
            _caller=_caller,
            data=payload,
        )

    # FUNCTION: log_data_save
    # SUMMARY: Log data saving operations to various destinations.
    def log_data_save(
        self: SemanticLoggerEventContract,
        destination: str,
        *,
        record_count: int,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        payload: LogPayload = {"destination": destination, "record_count": record_count}
        payload.update(extra)
        self.log_event(
            EventType.DATA_SAVE,
            f"Saved {record_count} records to {destination}",
            level=logging.DEBUG,
            event_id=f"data.save.{destination}",
            _caller=_caller,
            data=payload,
        )

    # FUNCTION: log_decision
    # SUMMARY: Log business logic decisions with reasoning and alternatives.
    def log_decision(
        self: SemanticLoggerEventContract,
        decision_point: str,
        chosen_path: str,
        *,
        reason: str,
        alternatives_considered: Optional[list[str]] = None,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        payload: dict[str, Any] = {
            "decision_point": decision_point,
            "chosen_path": chosen_path,
            "reason": reason,
        }
        if alternatives_considered is not None:
            payload["alternatives_considered"] = alternatives_considered
        payload.update(extra)
        self.log_event(
            EventType.DECISION,
            f"Decision at {decision_point}: chose '{chosen_path}'",
            level=logging.DEBUG,
            event_id=f"decision.{decision_point}",
            _caller=_caller,
            data=payload,
        )

    # FUNCTION: log_validation
    # SUMMARY: Log validation results with pass or fail status.
    def log_validation(
        self: SemanticLoggerEventContract,
        validator: str,
        *,
        passed: bool,
        failed_fields: Optional[list[str]] = None,
        **extra: LogValue,
    ) -> None:
        _caller = self._resolve_caller()
        payload: LogPayload = {"validator": validator, "passed": passed}
        if failed_fields is not None:
            payload["failed_fields"] = failed_fields
        payload.update(extra)
        level = logging.DEBUG if passed else logging.WARNING
        self.log_event(
            EventType.VALIDATION,
            f"Validation {validator} {'passed' if passed else 'failed'}",
            level=level,
            event_id=f"validation.{validator}",
            _caller=_caller,
            data=payload,
        )
