# FILE: project/infrastructure/api/middleware.py
# SUMMARY: FastAPI middleware for AI-optimized semantic logging of HTTP requests.

import re
import uuid
from pathlib import Path
from typing import Dict

from fastapi import Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from project.common.sampling import should_sample_health_check
from project.core.logging import (
    get_logger,
    reset_trace_id,
    set_trace_id,
    summarize_mapping,
)

# ATTRIBUTE: logger (SemanticLogger)
# SUMMARY: Semantic logger used for HTTP request tracing in middleware.
logger = get_logger(__name__)

# ATTRIBUTE: _SENSITIVE_PARAM_PATTERN (re.Pattern)
# SUMMARY: Compiled regex pattern matching sensitive query parameter names.
# NOTE: This is defence in depth, not the defence. Query parameters reach the log through
# summarize_mapping, which emits key names and a count and drops every value regardless of the
# name — so no query-string value is logged whether this pattern matches it or not. Do not read
# the name list as the boundary of what is protected: adding `ssn` or `card` here would change
# nothing, and removing the whole pattern would change nothing either. It stays because the
# sanitised mapping is the shape a future direct logger would reach for first.
_SENSITIVE_PARAM_PATTERN = re.compile(r"(key|token|secret|password|auth|credential)", re.IGNORECASE)

# ATTRIBUTE: _REQUEST_ID_PATTERN (re.Pattern)
# SUMMARY: Acceptable inbound request-id format (UUIDv4 or 16-64 hex/dash chars).
# Defends against header injection: anything else is rejected and we mint a fresh UUID.
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{16,64}$")

# ATTRIBUTE: _TRACEPARENT_PATTERN (re.Pattern)
# SUMMARY: W3C traceparent header — `version-traceid-spanid-flags`. Extracts the trace_id segment.
# Per spec: 2 hex chars (version) - 32 hex chars (trace_id) - 16 hex chars (span_id) - 2 hex chars (flags).
_TRACEPARENT_PATTERN = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")


# FUNCTION: _extract_or_generate_request_id
# SUMMARY: Resolve a request id from inbound trace headers, falling back to a fresh UUID.
# INPUT: request (Request): The incoming HTTP request whose headers are inspected.
# OUTPUT: (str): A validated request id suitable for logging and trace correlation.
def _extract_or_generate_request_id(request: Request) -> str:
    # **LOGIC_STEP**: Prefer an explicit X-Request-ID when present and well-formed.
    explicit = request.headers.get("x-request-id")
    if explicit and _REQUEST_ID_PATTERN.match(explicit):
        return explicit

    # **LOGIC_STEP**: Fall back to W3C traceparent — extract the 32-hex trace_id segment.
    traceparent = request.headers.get("traceparent")
    if traceparent:
        match = _TRACEPARENT_PATTERN.match(traceparent)
        if match:
            return match.group(1)

    # **LOGIC_STEP**: No usable inbound id — generate a fresh UUID.
    return str(uuid.uuid4())


# FUNCTION: _sanitize_params
# SUMMARY: Mask values of sensitive query parameters to prevent credential leakage in logs.
# OUTPUT: (Dict[str, str]): Sanitized dictionary with sensitive values replaced by '***'.
def _sanitize_params(params: Dict[str, str]) -> Dict[str, str]:
    # **LOGIC_STEP**: Iterate params and mask values whose keys match sensitive patterns.
    return {k: "***" if _SENSITIVE_PARAM_PATTERN.search(k) else v for k, v in params.items()}


# CLASS: project.infrastructure.api.middleware.AILoggingMiddleware
# SUMMARY: Middleware that intercepts HTTP requests and provides AI-optimized semantic logging with request tracing.
# EXTENDS: BaseHTTPMiddleware
class AILoggingMiddleware(BaseHTTPMiddleware):
    # FUNCTION: __init__
    # SUMMARY: Initialize the AI logging middleware.
    def __init__(self, app: ASGIApp) -> None:
        # **LOGIC_STEP**: Initialize the base middleware with the ASGI app.
        super().__init__(app)

    # FUNCTION: dispatch
    # SUMMARY: Process each HTTP request with comprehensive logging and error handling.
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # **LOGIC_STEP**: Skip logging for sampled-out health checks to reduce log noise.
        if request.url.path.rstrip("/") == "/health" and not should_sample_health_check():
            return await call_next(request)

        # **LOGIC_STEP**: Resolve request ID from inbound trace headers (X-Request-ID,
        # then W3C traceparent), with a fresh UUID fallback. Lets multi-service traces
        # stay correlated when an upstream caller already assigned an id.
        request_id = _extract_or_generate_request_id(request)

        # **LOGIC_STEP**: Set trace_id for all logs within this request scope.
        trace_token = set_trace_id(request_id)

        try:
            # **LOGIC_STEP**: Store request_id in request.state for exception handlers.
            request.state.request_id = request_id

            # **LOGIC_STEP**: Stash the trace_id too. An unhandled exception unwinds past this
            # middleware and reaches the app's exception handler only after the `finally` below
            # has already reset the ContextVar, so the handler's critical record was written
            # without a trace_id — and the trace reader drops records that have none. The handler
            # restores it from here for the duration of its log call.
            request.state.trace_id = request_id

            # **LOGIC_STEP**: Extract request information for logging.
            method = request.method
            path = request.url.path
            query_params = dict(request.query_params)

            # **LOGIC_STEP**: Extract optional business-context headers for log enrichment.
            session_id = request.headers.get("x-session-id")
            user_id = request.headers.get("x-user-id")

            # **LOGIC_STEP**: Set business-context BEFORE the span so it propagates
            # to span.finish and request.summary events (which fire on span exit).
            _pre_span_ctx: dict = {
                "request_id": request_id,
                "method": method,
                "path": path,
            }
            if session_id:
                _pre_span_ctx["session_id"] = session_id
            if user_id:
                _pre_span_ctx["user_id"] = user_id

            with logger.context(**_pre_span_ctx):
                # **LOGIC_STEP**: `root=True` is what makes a request observable at all. Without it
                # this span inherited `application_lifecycle` as its parent — the launcher holds
                # that span open around the whole of `uvicorn.run`, and asyncio copies the context
                # into each request task — so `is_root_span` was false, and with it went the
                # request.summary event, the INFO log level, and per-request counters. Measured on a
                # live container before the fix: seven requests, zero summaries, and a successful
                # request adding literally zero lines to the log.
                with logger.span(
                    "http_request",
                    root=True,
                    input_params={
                        "method": method,
                        "path": path,
                        "query_params_summary": summarize_mapping(_sanitize_params(query_params)),
                        "client_ip": request.client.host if request.client else None,
                        "user_agent": request.headers.get("user-agent"),
                    },
                    request_id=request_id,
                ) as span_ctx:
                    # **LOGIC_STEP**: Add span_id to context for all nested logs.
                    with logger.context(span_id=span_ctx.span_id):
                        # **LOGIC_STEP**: Process the request through the application.
                        response = await call_next(request)

                        # **LOGIC_STEP**: Determine response type and extract appropriate metadata.
                        response_type = "standard"
                        response_size: int | str | None = None
                        content_type = response.headers.get("content-type", "unknown")

                        if isinstance(response, StreamingResponse):
                            response_type = "streaming"
                            response_size = "streaming"
                        elif isinstance(response, FileResponse):
                            response_type = "file"
                            try:
                                response_size = (
                                    Path(response.path).stat().st_size if response.path else None
                                )
                            except Exception:
                                response_size = None
                        elif hasattr(response, "body"):
                            try:
                                response_size = len(response.body) if response.body else None
                            except Exception:
                                response_size = None

                        # **LOGIC_STEP**: Attach response metadata to span output for automatic logging on span finish.
                        span_ctx.output = {
                            "status_code": response.status_code,
                            "response_type": response_type,
                            "response_size": response_size,
                            "content_type": content_type,
                        }

                        # **LOGIC_STEP**: Add request ID to response headers for client tracing.
                        response.headers["X-Request-ID"] = request_id

            # **LOGIC_STEP**: Span is now closed — span.finish and, because the span above declares
            # itself a trace root, request.summary too. Both of those were silently absent for
            # every real request until that declaration was added.
            # NOTE: This used to rewrite the whole full-trace file here, on every request: read all
            # of it, rebuild the tree for every trace it held, write it all back — synchronously,
            # from an async handler, with no lock. Lines appended by concurrent requests between the
            # read and the write were silently destroyed, and the cost grew with the file. The
            # summary is regenerated once at shutdown (project/launcher/main.py) and on demand with
            # `make format-trace`, which is every bit as useful and cannot lose a line.

            return response
        finally:
            # **LOGIC_STEP**: Reset trace_id to previous state.
            reset_trace_id(trace_token)
