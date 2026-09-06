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

# ATTRIBUTE: _LOGGING_DIR (str)
# SUMMARY: Normalized absolute path to the logging package directory, used by _resolve_caller to skip internal frames.
_LOGGING_DIR = os.path.normpath(os.path.dirname(os.path.abspath(__file__)))

# ATTRIBUTE: _CONTEXTLIB_FILE (str)
# SUMMARY: Normalized absolute path to stdlib contextlib.py, skipped by _resolve_caller since @contextmanager adds an extra frame.
_CONTEXTLIB_FILE = os.path.normpath(os.path.abspath(contextlib.__file__))


# FUNCTION: _extract_status_code
# SUMMARY: Read an HTTP status code out of a span's output payload, or None when the span carried none.
def _extract_status_code(output: LogPayload) -> Optional[int]:
    raw = output.get("status_code")
    return raw if isinstance(raw, int) else None


# FUNCTION: classify_request_outcome
# SUMMARY: Map an HTTP status code to the outcome reported by request.summary.
# NOTE: This exists because a boolean `success` lied. Starlette turns every handled exception into a
# Response inside ExceptionMiddleware, which sits *below* the logging middleware, so the span never
# sees the exception and reported success for 404, 409, 422 and — the case that mattered — 502 from a
# failing upstream. Only unhandled exceptions reached the except branch. Classifying by status code
# instead of by "did an exception escape" is what makes `server_error` mean what a reader assumes.
# Spans with no status code (startup, background work) stay OK: absence of an HTTP result is not a
# failure, and an outcome of SERVER_ERROR there would be the same lie in the other direction.
def classify_request_outcome(status_code: Optional[int]) -> RequestOutcome:
    # **LOGIC_STEP**: Treat a missing status as a non-HTTP span, which never fails on its own.
    if status_code is None or status_code < 400:
        return RequestOutcome.OK
    if status_code < 500:
        return RequestOutcome.CLIENT_ERROR
    return RequestOutcome.SERVER_ERROR


# ATTRIBUTE: _OUTCOME_WORDS (dict[RequestOutcome, str])
# SUMMARY: Human-readable verb per outcome for the request.summary message line.
_OUTCOME_WORDS: dict[RequestOutcome, str] = {
    RequestOutcome.OK: "completed",
    RequestOutcome.CLIENT_ERROR: "rejected",
    RequestOutcome.SERVER_ERROR: "failed",
    RequestOutcome.CANCELLED: "cancelled",
}


# CLASS: project.core.logging.logger.SemanticLogger
# SUMMARY: Semantic logging adapter providing caller-aware structured events and execution spans.
# EXTENDS: logging.LoggerAdapter
class SemanticLogger(SemanticLoggerEventsMixin, logging.LoggerAdapter):
    # FUNCTION: __init__
    # SUMMARY: Initialize SemanticLogger with an underlying Python logger.
    # INPUT: extra (Optional[MutableMapping[str, Any]]): Default extra context.
    def __init__(
        self, logger: logging.Logger, extra: Optional[MutableMapping[str, Any]] = None
    ) -> None:
        # **LOGIC_STEP**: Initialize LoggerAdapter with logger and extra context.
        super().__init__(logger, extra or {})

    # FUNCTION: _resolve_caller
    # SUMMARY: Walk up the call stack to find the first frame outside the logging package.
    # OUTPUT: (_CallerInfo): Tuple of pathname, line number, and function name for the external caller.
    def _resolve_caller(self) -> CallerInfo:
        # **LOGIC_STEP**: Start from the current frame and walk up, skipping logging internals and contextlib frames.
        frame: FrameType | None = sys._getframe(0)
        while frame is not None:
            filename = os.path.normpath(os.path.abspath(frame.f_code.co_filename))
            if not filename.startswith(_LOGGING_DIR) and filename != _CONTEXTLIB_FILE:
                return filename, frame.f_lineno, frame.f_code.co_name
            frame = frame.f_back
        return "<unknown>", 0, "<unknown>"

    # FUNCTION: process
    # SUMMARY: Process log message and kwargs to inject context and structured payload data.
    def process(
        self, msg: str, kwargs: MutableMapping[str, Any]
    ) -> tuple[str, MutableMapping[str, Any]]:
        # **LOGIC_STEP**: Get current context and active span information.
        ctx = get_current_context()
        span_id, span_name = get_current_span()

        # **LOGIC_STEP**: Ensure an extra dictionary exists for structured payload data.
        extra = cast(MutableMapping[str, Any], kwargs.get("extra", {}))
        if "extra" not in kwargs:
            kwargs["extra"] = extra

        # **LOGIC_STEP**: Merge current context into the payload so nested logs inherit bound metadata.
        if "payload" not in extra:
            extra["payload"] = {}
        merged = {**ctx, **extra["payload"]}
        extra["payload"] = merged

        # **LOGIC_STEP**: Add span information when a span is active.
        if span_id:
            extra["span"] = {"span_id": span_id}
            if span_name:
                extra["span"]["name"] = span_name

        return msg, kwargs

    # FUNCTION: log_event
    # SUMMARY: Emit a structured semantic event with caller override metadata.
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
        # **LOGIC_STEP**: Resolve caller info once unless already captured by the calling method.
        _caller = _caller or self._resolve_caller()

        # **LOGIC_STEP**: Build the payload with event type, stable event_id, and structured data.
        payload: LogPayload = {"event_type": event_type.value}
        if event_id is not None:
            payload["event_id"] = event_id
        if data is not None:
            payload.update(data)
        payload.update(kwargs)

        # **LOGIC_STEP**: Delegate to the standard logger with caller override information.
        extra = {"payload": payload, "_caller_override": _caller}
        self.log(level, msg, extra=extra, exc_info=exc_info, stacklevel=1)

    # FUNCTION: context
    # SUMMARY: Context manager for binding metadata to all logs within scope.
    # INPUT: **kv (dict): Arbitrary keyword arguments to bind to log context.
    @contextmanager
    def context(self, **kv: Any) -> Generator[None, None, None]:
        # **LOGIC_STEP**: Set context with new metadata and store the reset token.
        token = set_context(**kv)
        try:
            yield
        finally:
            reset_context(token)

    # FUNCTION: span
    # SUMMARY: Context manager for execution tracing with automatic timing, hierarchy, and request summary events.
    # INPUT: root (bool): Start a new trace root, ignoring whatever span is ambient. See below.
    # OUTPUT: (ContextManager): Context manager yielding a SpanContext.
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
        # **LOGIC_STEP**: `root=True` says "this span begins a trace" rather than "this span happens
        # to have no parent". The distinction is load-bearing. The launcher opens
        # `application_lifecycle` around the whole of `uvicorn.run`, and asyncio copies that context
        # into every request task, so `http_request` inherited a parent and was never root — which
        # silently turned off three things at once: request.summary was not emitted for any request
        # (it fires for root spans only), the span dropped to DEBUG and vanished from a production
        # log at INFO, and every concurrent request mutated one process-wide counter dict instead of
        # its own. Measured on a live container: seven requests, zero summaries, and a final
        # `duration_ms` equal to process uptime. Declaring root-ness at the call site fixes all
        # three, and unlike uvicorn's `reset_contextvars` flag it also holds under ASGITransport,
        # under any other ASGI server, and in tests.
        if root:
            parent_span_id = None
        is_root_span = parent_span_id is None
        span_level = (
            level if level is not None else (logging.INFO if is_root_span else logging.DEBUG)
        )
        # **LOGIC_STEP**: Decide once whether the start/finish pair will survive the level filter,
        # and skip building it when it will not. A child span logs at DEBUG, so in production — where
        # the level sits at INFO — both events were assembled in full and then dropped by `logging`.
        # Measured on this machine: a child span cost 7918 ns, of which 3668 ns was the two discarded
        # `log_event` calls and 948 ns the stack walk feeding them. That is what made wrapping every
        # repository method in a span too expensive to prescribe. Stats, timing, context and the
        # error branch stay outside the guard: `error_count` and the ERROR-level `span.error` event
        # are read by the root span's summary, which production does emit.
        #
        # `child_span_count` used to sit out here too, and that made the summary say things the log
        # could not show: three filtered children produced `spans=3` over a tree with no children in
        # it, and a reader had no way to tell a suppressed span from a miscount. Counted below
        # instead, where each branch knows whether it wrote anything — and what the filter swallowed
        # is reported separately rather than folded in, because "nothing happened" and "something
        # happened at a level you are not reading" are different answers.
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
            increment_span_stat("error_count")
            if not is_root_span:
                # **LOGIC_STEP**: span.error is written whatever the span's own level, so a failed
                # child is always in the tree and always counted.
                increment_span_stat("child_span_count")

            already_logged = getattr(error, "_logged_with_traceback", False)
            # **LOGIC_STEP**: A span whose lifecycle events were filtered out never paid for the
            # stack walk, so resolve the caller here. `_resolve_caller` skips both the logging
            # package and contextlib, and @contextmanager re-enters this frame through
            # contextlib.__exit__ — so it lands on the same external frame it would have at entry.
            # tests/application/test_logging_api.py pins that.
            if _caller is None:
                _caller = self._resolve_caller()
            self.log_event(
                EventType.ISSUE_ERROR,
                f"Span failed: {name} ({type(error).__name__})",
                level=logging.ERROR,
                exc_info=not already_logged,
                event_id="span.error",
                _caller=_caller,
                data=metadata,
                span_id=span_id,
                parent_span_id=parent_span_id,
                error_id=error_id,
                duration_ms=round(duration_ms, 3),
                name=name,
                error_message=str(error),
                exception_type=type(error).__name__,
            )

            if not already_logged:
                setattr(error, "_logged_with_traceback", True)

            if is_root_span:
                self._emit_request_summary(
                    name=name,
                    root_span_id=span_id,
                    duration_ms=duration_ms,
                    outcome=RequestOutcome.SERVER_ERROR,
                    status_code=None,
                    _caller=_caller,
                )
            raise
        except BaseException as interruption:
            # **LOGIC_STEP**: asyncio.CancelledError, KeyboardInterrupt and SystemExit are not
            # Exceptions, so the branch above never sees them — and until 2026-09-02 neither did
            # the log. A request cancelled by uvicorn's graceful-shutdown timeout (30 s, set in
            # project/launcher/main.py) emitted span.start and nothing else: no span.error, no
            # span.finish, no request.summary. The trace tree is built from finish and error
            # events alone, so the request was not in it at all. Measured with a fake log_event:
            # ['span.start'] against ['span.start', 'span.error', 'request.summary'] for a
            # RuntimeError in the same harness.
            duration_ms = (time.perf_counter_ns() - start_ns) / 1e6
            # **LOGIC_STEP**: An Exception raised while logging must not replace the
            # interruption. A handler already closing at shutdown would otherwise turn a
            # CancelledError into its own error through __context__, and uvicorn would see a
            # failed task instead of a cancelled one. The log line is the one that gets dropped.
            # A second BaseException from inside the logger — a second Ctrl-C landing exactly
            # here — is not caught, and does replace the first; that one is the newer signal.
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
            # **LOGIC_STEP**: Bare `raise`, the same object. asyncio recognises its cancellation
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
                # **LOGIC_STEP**: Counted here rather than at open, so that each span is counted
                # exactly once and in one counter. Counting the filter at open was speculative: a
                # filtered span that then raised or was cancelled had its span.error written
                # anyway — those are emitted whatever the span's own level — and was counted a
                # second time in the error branch, so one span reported `spans=1, filtered=1` and
                # a reader saw two.
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

    # FUNCTION: _emit_interrupted_span
    # SUMMARY: Write the span.error and, for a root span, the request.summary that an interruption
    # would otherwise skip.
    # INPUT: interruption (BaseException): What cut the span short; named in the event, never traced.
    # INPUT: _caller (Optional[CallerInfo]): Resolved at span entry when lifecycle events were
    #        enabled, and here otherwise — the same lazy rule as the Exception branch.
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
        # **LOGIC_STEP**: The same event_id as a failure, so the trace tree — which reads
        # span.finish and span.error and nothing else — shows the span with its exception type.
        # At WARNING and without a traceback: a CancelledError's frames say nothing a reader can
        # act on, and the ERROR level is reserved for the application's own failures.
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
            # **LOGIC_STEP**: Written at WARNING whatever the span's own level, so an interrupted
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

    # FUNCTION: _emit_request_summary
    # SUMMARY: Emit an aggregated request summary event at the end of a root span.
    # INPUT: outcome (RequestOutcome): Result classification; SERVER_ERROR raises the event to ERROR level.
    # INPUT: status_code (Optional[int]): HTTP status of the response, omitted for non-HTTP spans.
    # INPUT: _caller (Optional[_CallerInfo]): Pre-resolved caller info from the parent span.
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
        # **LOGIC_STEP**: Collect span stats and build summary payload.
        stats = get_span_stats() or {}
        child_span_count = stats.get("child_span_count", 0)
        # **LOGIC_STEP**: What the level filter swallowed is reported next to what it kept, never
        # folded into it. `spans=` now counts what a reader can actually find in the tree, and
        # `filtered=` tells them the rest happened at a level they are not reading — which is a
        # different fact from "nothing else happened", and the one that used to be lost.
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
            # **LOGIC_STEP**: The span's name belongs in the payload, not only inside the prose of
            # `msg`. Until requests emitted their own summaries there was exactly one of these per
            # process and the question never came up; now a log holds one per request plus the
            # lifecycle one, and a reader filtering `event_id == "request.summary"` has to be able
            # to tell them apart without parsing a sentence.
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

        # **LOGIC_STEP**: Only a server-side outcome deserves ERROR; 4xx is routine traffic and would
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


# FUNCTION: get_logger
# SUMMARY: Get SemanticLogger instance by name using standard logging.
@functools.lru_cache(maxsize=128)
def get_logger(name: str) -> SemanticLogger:
    # **LOGIC_STEP**: Get the standard Python logger and wrap it in SemanticLogger.
    logger = logging.getLogger(name)
    return SemanticLogger(logger)
