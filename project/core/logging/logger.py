# FILE: project/core/logging/logger.py
# SUMMARY: Core semantic logging adapter implementing caller resolution, context propagation, and span lifecycle management.

import contextlib
import functools
import logging
import os
import sys
import time
import uuid
from collections.abc import Generator, MutableMapping
from contextlib import contextmanager
from types import FrameType
from typing import Any, Optional, cast

from project.domain.exceptions import describe_rejection, is_client_rejection
from project.core.logging.context import (
    get_current_context,
    get_current_span,
    get_span_stats,
    increment_span_stat,
    init_span_stats,
    reset_context,
    reset_current_error_id,
    reset_span,
    reset_span_stats,
    set_context,
    set_current_error_id,
    set_span,
)
from project.core.logging.enums import EventType, RequestOutcome
from project.core.logging.logger_events import SemanticLoggerEventsMixin
from project.core.logging.logger_types import (
    CallerInfo,
    LogPayload,
    LogValue,
    SpanContext,
)

# Used by _resolve_caller to skip internal frames.
_LOGGING_DIR = os.path.normpath(os.path.dirname(os.path.abspath(__file__)))

# Skipped by _resolve_caller since @contextmanager adds an extra frame.
_CONTEXTLIB_FILE = os.path.normpath(os.path.abspath(contextlib.__file__))


def _extract_status_code(output: LogPayload) -> Optional[int]:
    raw = output.get("status_code")
    return raw if isinstance(raw, int) else None


# Starlette handles exceptions below the logging middleware. Classify HTTP outcomes by
# status because handled 4xx and 502 never escape the span. Non-HTTP spans have no status.
def classify_request_outcome(status_code: Optional[int]) -> RequestOutcome:
    # Treat a missing status as a non-HTTP span, which never fails on its own.
    if status_code is None or status_code < 400:
        return RequestOutcome.OK
    if status_code < 500:
        return RequestOutcome.CLIENT_ERROR
    return RequestOutcome.SERVER_ERROR


_OUTCOME_WORDS: dict[RequestOutcome, str] = {
    RequestOutcome.OK: "completed",
    RequestOutcome.CLIENT_ERROR: "rejected",
    RequestOutcome.SERVER_ERROR: "failed",
    RequestOutcome.CANCELLED: "cancelled",
}


class SemanticLogger(SemanticLoggerEventsMixin, logging.LoggerAdapter):
    def __init__(
        self, logger: logging.Logger, extra: Optional[MutableMapping[str, Any]] = None
    ) -> None:
        # Initialize LoggerAdapter with logger and extra context.
        super().__init__(logger, extra or {})

    def _resolve_caller(self) -> CallerInfo:
        # Start from the current frame and walk up, skipping logging internals and contextlib frames.
        frame: FrameType | None = sys._getframe(0)
        while frame is not None:
            filename = os.path.normpath(os.path.abspath(frame.f_code.co_filename))
            if not filename.startswith(_LOGGING_DIR) and filename != _CONTEXTLIB_FILE:
                return filename, frame.f_lineno, frame.f_code.co_name
            frame = frame.f_back
        return "<unknown>", 0, "<unknown>"

    def process(
        self, msg: str, kwargs: MutableMapping[str, Any]
    ) -> tuple[str, MutableMapping[str, Any]]:
        # Get current context and active span information.
        ctx = get_current_context()
        span_id, span_name = get_current_span()

        # Ensure an extra dictionary exists for structured payload data.
        extra = cast(MutableMapping[str, Any], kwargs.get("extra", {}))
        if "extra" not in kwargs:
            kwargs["extra"] = extra

        # Merge current context into the payload so nested logs inherit bound metadata.
        if "payload" not in extra:
            extra["payload"] = {}
        merged = {**ctx, **extra["payload"]}
        extra["payload"] = merged

        # Add span information when a span is active.
        if span_id:
            extra["span"] = {"span_id": span_id}
            if span_name:
                extra["span"]["name"] = span_name

        return msg, kwargs

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
    ) -> None:
        # Resolve caller info once unless already captured by the calling method.
        _caller = _caller or self._resolve_caller()

        # Build the payload with event type, stable event_id, and structured data.
        payload: LogPayload = {"event_type": event_type.value}
        if event_id is not None:
            payload["event_id"] = event_id
        if data is not None:
            payload.update(data)
        payload.update(kwargs)

        # Delegate to the standard logger with caller override information.
        extra = {"payload": payload, "_caller_override": _caller}
        self.log(level, msg, extra=extra, exc_info=exc_info, stacklevel=1)

    @contextmanager
    def context(self, **kv: Any) -> Generator[None, None, None]:
        # Set context with new metadata and store the reset token.
        token = set_context(**kv)
        try:
            yield
        finally:
            reset_context(token)

    # Span-naming conventions. `name` has no enforced grammar — anything is a valid span —
    # but two dotted prefixes carry meaning to the rest of this package and to
    # trace_formatter.py's compact renderer, so a vertical that wants the behavior below copies the
    # prefix, not just the idea:
    #   `db.<vertical>.<operation>` — one repository call, e.g. `db.reference_task.add`. Documented
    #   by example in project/infrastructure/persistence/reference_task_repository.py, which is the
    #   copyable original.
    #   `agent.tool.<tool_name>` — one tool call inside an agent loop, e.g.
    #   `agent.tool.search_docs`, opened by project/infrastructure/agents/tool_runner.py's
    #   `run_tool`. Two independent projects built on this template each wrapped their own tool
    #   calls by hand to see which tool ran and with what arguments, because the compact trace
    #   showed neither. The renderer shows this span's `input_params` inline: see
    #   trace_formatter.py's `_TOOL_SPAN_PREFIX` and the NOTE beside it.
    # Context manager for execution tracing with automatic timing, hierarchy, and request summary events.
    # Read one request with `make format-trace ARGS="<logfile> --trace <request-id>"`.
    # root: Start a new trace root, ignoring whatever span is ambient. See below.
    @contextmanager
    def span(
        self,
        name: str,
        *,
        input_params: Optional[dict[str, Any]] = None,
        level: Optional[int] = None,
        root: bool = False,
        **metadata: LogValue,
    ) -> Generator[SpanContext, None, None]:
        parent_span_id, _ = get_current_span()
        # Request tasks inherit the launcher's lifecycle span. An explicit root gives each request
        # INFO lifecycle events, a summary and its own counters across ASGI servers and tests.
        if root:
            parent_span_id = None
        is_root_span = parent_span_id is None
        span_level = (
            level if level is not None else (logging.INFO if is_root_span else logging.DEBUG)
        )
        # Avoid building a filtered DEBUG start/finish pair. Keep context, timing and errors because
        # a root's summary still counts visible spans, filtered spans and errors separately.
        emit_lifecycle = self.isEnabledFor(span_level)
        _caller = self._resolve_caller() if emit_lifecycle else None
        start_ns = time.perf_counter_ns()
        span_id = uuid.uuid4().hex
        ctx = SpanContext(span_id)

        stats_token = None
        if is_root_span:
            stats_token = init_span_stats()

        span_token, name_token = set_span(span_id, name)

        if emit_lifecycle:
            start_data: LogPayload = {**metadata}
            if input_params is not None:
                start_data["input_params"] = input_params

            self.log_event(
                EventType.FLOW_START,
                f"Span started: {name}",
                level=span_level,
                event_id="span.start",
                _caller=_caller,
                data=start_data,
                span_id=span_id,
                parent_span_id=parent_span_id,
                name=name,
            )

        error_id_token = None
        try:
            yield ctx
        except Exception as error:
            duration_ms = (time.perf_counter_ns() - start_ns) / 1e6
            error_id = uuid.uuid4().hex
            ctx.error_id = error_id
            error_id_token = set_current_error_id(error_id)
            rejection = is_client_rejection(error)
            # A rejection is handled traffic. Emit it at WARNING without raising the error count.
            if not rejection:
                increment_span_stat("error_count")
            if not is_root_span:
                # span.error is written whatever the span's own level, so a failed
                # child is always in the tree and always counted.
                increment_span_stat("child_span_count")

            already_logged = getattr(error, "_logged_with_traceback", False)
            # Resolve the same external caller lazily when start and finish events were filtered.
            if _caller is None:
                _caller = self._resolve_caller()

            # Share rejection classification with the HTTP handler so a routine 4xx stays at WARNING
            # and carries a safe field description instead of a traceback.
            event_level = logging.WARNING if rejection else logging.ERROR
            write_traceback = not rejection and not already_logged
            self.log_event(
                EventType.ISSUE_WARNING if rejection else EventType.ISSUE_ERROR,
                f"Span {'rejected' if rejection else 'failed'}: {name} ({type(error).__name__})",
                level=event_level,
                exc_info=write_traceback,
                event_id="span.error",
                _caller=_caller,
                data=metadata,
                span_id=span_id,
                parent_span_id=parent_span_id,
                error_id=error_id,
                duration_ms=round(duration_ms, 3),
                name=name,
                # A rejection's text is the client's to read, not the log's — ADR-013.
                error_message=describe_rejection(error) if rejection else str(error),
                exception_type=type(error).__name__,
                # WARNING also carries interruptions. The explicit marker keeps their trace symbols distinct.
                client_rejection=rejection,
            )

            if write_traceback:
                setattr(error, "_logged_with_traceback", True)

            if is_root_span:
                self._emit_request_summary(
                    name=name,
                    root_span_id=span_id,
                    duration_ms=duration_ms,
                    outcome=RequestOutcome.SERVER_ERROR,
                    # What the owner recorded before re-raising — the HTTP middleware does.
                    status_code=_extract_status_code(ctx.output),
                    _caller=_caller,
                )
            raise
        except BaseException as interruption:
            # Interruptions bypass Exception and need their own end event and root summary.
            duration_ms = (time.perf_counter_ns() - start_ns) / 1e6
            # Preserve the interruption when logging fails during shutdown. A newer signal still escapes.
            with contextlib.suppress(Exception):
                self._emit_interrupted_span(
                    name=name,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    metadata=metadata,
                    duration_ms=duration_ms,
                    is_root_span=is_root_span,
                    interruption=interruption,
                    _caller=_caller,
                )
            # Bare `raise`, the same object. asyncio recognises its cancellation
            # by identity, and tests/application/test_logging_api.py pins that it comes out.
            raise
        else:
            duration_ms = (time.perf_counter_ns() - start_ns) / 1e6
            if emit_lifecycle:
                finish_data: LogPayload = {**metadata}
                if ctx.output:
                    finish_data["output"] = ctx.output
                self.log_event(
                    EventType.FLOW_END,
                    f"Span finished: {name} ({round(duration_ms, 1)}ms)",
                    level=span_level,
                    event_id="span.finish",
                    _caller=_caller,
                    data=finish_data,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    duration_ms=round(duration_ms, 3),
                    name=name,
                )
                if not is_root_span:
                    increment_span_stat("child_span_count")
            elif not is_root_span:
                # Count a filtered span only at completion. An error or cancellation still writes an event.
                increment_span_stat("filtered_child_span_count")

            if is_root_span:
                status_code = _extract_status_code(ctx.output)
                self._emit_request_summary(
                    name=name,
                    root_span_id=span_id,
                    duration_ms=duration_ms,
                    outcome=classify_request_outcome(status_code),
                    status_code=status_code,
                    _caller=_caller,
                )
        finally:
            if error_id_token is not None:
                reset_current_error_id(error_id_token)
            if stats_token is not None:
                reset_span_stats(stats_token)
            reset_span(span_token, name_token)

    # End interrupted spans with an error event and, for a root, its request summary.
    def _emit_interrupted_span(
        self,
        *,
        name: str,
        span_id: str,
        parent_span_id: Optional[str],
        metadata: dict[str, Any],
        duration_ms: float,
        is_root_span: bool,
        interruption: BaseException,
        _caller: Optional[CallerInfo],
    ) -> None:
        if _caller is None:
            _caller = self._resolve_caller()
        # Keep interrupted spans in the tree at WARNING. The traceback adds no useful failure site.
        self.log_event(
            EventType.ISSUE_WARNING,
            f"Span interrupted: {name} ({type(interruption).__name__})",
            level=logging.WARNING,
            event_id="span.error",
            _caller=_caller,
            data=metadata,
            span_id=span_id,
            parent_span_id=parent_span_id,
            duration_ms=round(duration_ms, 3),
            name=name,
            error_message=str(interruption),
            exception_type=type(interruption).__name__,
        )
        if not is_root_span:
            # Written at WARNING whatever the span's own level, so an interrupted
            # child is in the tree and belongs in the count like a failed one.
            increment_span_stat("child_span_count")
        if is_root_span:
            self._emit_request_summary(
                name=name,
                root_span_id=span_id,
                duration_ms=duration_ms,
                outcome=RequestOutcome.CANCELLED,
                status_code=None,
                _caller=_caller,
            )

    # outcome: SERVER_ERROR raises the event to ERROR level.
    # status_code: Omitted for non-HTTP spans.
    def _emit_request_summary(
        self,
        *,
        name: str,
        root_span_id: str,
        duration_ms: float,
        outcome: RequestOutcome,
        status_code: Optional[int],
        _caller: Optional[CallerInfo] = None,
    ) -> None:
        # Collect span stats and build summary payload.
        stats = get_span_stats() or {}
        child_span_count = stats.get("child_span_count", 0)
        # Count spans the reader can find separately from spans that were filtered.
        filtered_child_span_count = stats.get("filtered_child_span_count", 0)
        error_count = stats.get("error_count", 0)

        summary_parts = [f"spans={child_span_count}"]
        if filtered_child_span_count:
            summary_parts.append(f"filtered={filtered_child_span_count}")
        if error_count:
            summary_parts.append(f"errors={error_count}")

        llm_calls = stats.get("llm_calls", 0)
        if llm_calls:
            summary_parts.append(f"llm_calls={llm_calls}")

        status_word = _OUTCOME_WORDS[outcome]
        status_suffix = f" [{status_code}]" if status_code is not None else ""
        msg = (
            f"Request {status_word}: {name}{status_suffix} in {round(duration_ms, 1)}ms "
            f"({', '.join(summary_parts)})"
        )

        payload: LogPayload = {
            # A log contains request and lifecycle summaries. Name the span in machine-readable data.
            "span_name": name,
            "root_span_id": root_span_id,
            "duration_ms": round(duration_ms, 3),
            "outcome": outcome.value,
            "child_span_count": child_span_count,
            "error_count": error_count,
        }
        if filtered_child_span_count:
            payload["filtered_child_span_count"] = filtered_child_span_count
        if status_code is not None:
            payload["status_code"] = status_code
        if llm_calls:
            payload["llm_calls"] = llm_calls
            payload["total_input_tokens"] = stats.get("total_input_tokens", 0)
            payload["total_output_tokens"] = stats.get("total_output_tokens", 0)

        # Only a server-side outcome deserves ERROR; 4xx is routine traffic and would
        # drown the reader in noise at anything above INFO. An interrupted request sits between
        # the two: not the application's fault, but a request that never answered.
        level = logging.INFO
        if outcome is RequestOutcome.SERVER_ERROR:
            level = logging.ERROR
        elif outcome is RequestOutcome.CANCELLED:
            level = logging.WARNING

        self.log_event(
            EventType.REQUEST_SUMMARY,
            msg,
            level=level,
            event_id="request.summary",
            _caller=_caller,
            data=payload,
        )


@functools.lru_cache(maxsize=128)
def get_logger(name: str) -> SemanticLogger:
    # Get the standard Python logger and wrap it in SemanticLogger.
    logger = logging.getLogger(name)
    return SemanticLogger(logger)
