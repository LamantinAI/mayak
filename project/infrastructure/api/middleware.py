# FILE: project/infrastructure/api/middleware.py
# SUMMARY: FastAPI middleware for AI-optimized semantic logging of HTTP requests.

import re
import uuid
from typing import Any, Dict

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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

# ATTRIBUTE: _UNKNOWN_CONTENT_TYPE (str)
# SUMMARY: Recorded when a response never carried a readable content-type header.
_UNKNOWN_CONTENT_TYPE = "unknown"


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


# FUNCTION: _parse_content_length
# SUMMARY: Parse a Content-Length header value, tolerating absence or garbage.
# OUTPUT: (int | None): The parsed length, or None when absent or not an integer.
def _parse_content_length(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# FUNCTION: _with_header_replaced
# SUMMARY: Return a raw ASGI header list with one name set to one value, dropping any duplicate.
# INPUT: headers (list[tuple[bytes, bytes]]): Raw headers from an `http.response.start` message.
# INPUT: name (bytes): Lower-case header name to set.
# INPUT: value (bytes): Value to set it to.
# OUTPUT: (list[tuple[bytes, bytes]]): Headers with exactly one entry for `name`.
# NOTE: Mirrors starlette.datastructures.MutableHeaders.__setitem__ (drop existing, append once)
# without constructing a MutableHeaders/Response just to change one header on a message we are
# forwarding, not building.
def _with_header_replaced(
    headers: list[tuple[bytes, bytes]], name: bytes, value: bytes
) -> list[tuple[bytes, bytes]]:
    kept = [(key, val) for key, val in headers if key.lower() != name]
    kept.append((name, value))
    return kept


# CLASS: project.infrastructure.api.middleware._ResponseObserver
# SUMMARY: Accumulates response shape from the raw ASGI messages a downstream app sends; there is
# no Response object to inspect instead — BaseHTTPMiddleware never hands this class a real one.
# NOTE: starlette.middleware.base.BaseHTTPMiddleware.call_next does not return the endpoint's own
# Response — it consumes the downstream ASGI messages and hands back its own private
# `_StreamingResponse` (starlette/middleware/base.py). That type does not subclass
# fastapi.responses.StreamingResponse and never gets a `.body` attribute, so on starlette==1.4.1 an
# `isinstance(response, StreamingResponse)` / `isinstance(response, FileResponse)` /
# `hasattr(response, "body")` chain is unreachable: confirmed by running an actual
# StreamingResponse and an actual JSONResponse through the real dispatch path and printing what
# `call_next` returned (`type(response).__name__` was `_StreamingResponse` and
# `hasattr(response, "body")` was False in both cases). response_type would read "standard" and
# response_size would read None for every request, streaming or not. Reading the ASGI messages
# directly, as below, is the only version of these two fields that observes what it claims to.
class _ResponseObserver:
    # FUNCTION: __init__
    # SUMMARY: Start with the defaults each field falls back to when nothing overrides them.
    def __init__(self) -> None:
        self.status_code: int | None = None
        self.content_type: str = _UNKNOWN_CONTENT_TYPE
        self.response_type: str = "standard"
        self.response_size: int | str | None = None
        # ATTRIBUTE: _content_length (int | None)
        # SUMMARY: Content-Length from `http.response.start`, used only if this turns out to be
        # a pathsend (file) response — kept private because "standard" responses size themselves
        # from bytes actually seen, not from a header a handler could get wrong.
        self._content_length: int | None = None
        self._bytes_seen: int = 0

    # FUNCTION: record_start
    # SUMMARY: Capture status and content-type from `http.response.start`.
    # INPUT: status (int | None): The response's HTTP status, as sent by the downstream app.
    # INPUT: headers (list[tuple[bytes, bytes]]): Raw headers from the same message.
    def record_start(self, status: int | None, headers: list[tuple[bytes, bytes]]) -> None:
        self.status_code = status
        header_view = Headers(raw=headers)
        self.content_type = header_view.get("content-type", _UNKNOWN_CONTENT_TYPE)
        self._content_length = _parse_content_length(header_view.get("content-length"))

    # FUNCTION: record_pathsend
    # SUMMARY: Record a zero-copy file response — the `http.response.pathsend` extension message.
    # NOTE: FileResponse only sends this when the ASGI server advertises the extension, and only
    # after its own os.stat() call already set Content-Length (starlette.responses.FileResponse).
    # Reading that header back here is what lets this file skip a Path().stat() call — see the
    # module NOTE below the class for what that means for pyproject.toml's ASYNC240 exclusion.
    def record_pathsend(self) -> None:
        self.response_type = "file"
        self.response_size = self._content_length

    # FUNCTION: record_body
    # SUMMARY: Fold one `http.response.body` message into the running response shape.
    # INPUT: body (bytes): This message's chunk.
    # INPUT: more_body (bool): Whether another body message will follow.
    # NOTE: A response sent as a single message with more_body False or absent (plain
    # Response.__call__ — starlette.responses) stays "standard" and is sized by the bytes actually
    # seen. Any message with more_body True flips it to "streaming", which is the only signal pure
    # ASGI has for "more than one chunk" — a real StreamingResponse and a FileResponse falling back
    # to chunked reads because the server has no pathsend support look identical from here, and
    # both get called "streaming". That collapse is a real loss of information, accepted because
    # there is no ASGI-level signal to tell the two apart. "streaming" means "size not counted",
    # not a byte total.
    def record_body(self, body: bytes, more_body: bool) -> None:
        if more_body and self.response_type == "standard":
            self.response_type = "streaming"
        if self.response_type == "standard":
            self._bytes_seen += len(body)
        if not more_body:
            if self.response_type == "streaming":
                self.response_size = "streaming"
            elif self.response_type == "standard":
                # **LOGIC_STEP**: An empty body reports None, not 0 — the same convention other
                # span-output fields already use for "nothing here" rather than a raw zero.
                self.response_size = self._bytes_seen if self._bytes_seen else None

    # FUNCTION: as_span_output
    # SUMMARY: Render the accumulated shape as the span.output payload.
    # OUTPUT: (dict[str, Any]): status_code, response_type, response_size, content_type.
    def as_span_output(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "response_type": self.response_type,
            "response_size": self.response_size,
            "content_type": self.content_type,
        }


# FUNCTION: _observing_send
# SUMMARY: Wrap a `send` callable to feed `observer` from the ASGI messages passing through it and
# stamp X-Request-ID onto the response, without buffering a single byte of the body.
# INPUT: send (Send): The downstream `send` this middleware was given.
# INPUT: request_id (str): Resolved request id to publish on the response.
# INPUT: observer (_ResponseObserver): Sink for the response shape, read after the app returns.
# OUTPUT: (Send): A `send` with the same signature, safe to hand to the wrapped app.
def _observing_send(send: Send, request_id: str, observer: _ResponseObserver) -> Send:
    request_id_header = request_id.encode("latin-1")

    async def _send(message: Message) -> None:
        message_type = message.get("type")
        if message_type == "http.response.start":
            raw_headers = list(message.get("headers") or [])
            observer.record_start(message.get("status"), raw_headers)
            # **LOGIC_STEP**: Headers can only be changed on this one message — by the time a body
            # message arrives the client has already seen them.
            raw_headers = _with_header_replaced(raw_headers, b"x-request-id", request_id_header)
            message = {**message, "headers": raw_headers}
        elif message_type == "http.response.pathsend":
            observer.record_pathsend()
        elif message_type == "http.response.body":
            observer.record_body(message.get("body") or b"", bool(message.get("more_body", False)))
        await send(message)

    return _send


# CLASS: project.infrastructure.api.middleware.AILoggingMiddleware
# SUMMARY: Pure-ASGI middleware providing AI-optimized semantic logging with request tracing.
# NOTE: Pure ASGI, not starlette.middleware.base.BaseHTTPMiddleware. Starlette's own docs (and
# https://github.com/encode/starlette/discussions/1737) recommend pure ASGI for hot-path
# middleware for exactly the reason found above: BaseHTTPMiddleware runs the downstream app in a
# task group with a memory-object-stream per request to rebuild a Response from raw ASGI
# messages, which is where the real response type got lost in the first place. This class is on
# every request of every service built from this template, so that per-request task group and
# stream would be pure cost, and are the reason response_type/response_size never work through
# BaseHTTPMiddleware. Pure ASGI removes both problems at once: no task group, and the messages
# this middleware wants to inspect all along instead of a rebuilt stand-in for them.
class AILoggingMiddleware:
    # FUNCTION: __init__
    # SUMMARY: Store the wrapped ASGI application.
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    # FUNCTION: __call__
    # SUMMARY: Process each HTTP request with comprehensive logging and error handling.
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # **LOGIC_STEP**: Lifespan and websocket scopes carry none of the request shape below;
        # forward untouched, exactly as the BaseHTTPMiddleware version did for non-http scopes.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # **LOGIC_STEP**: Skip logging for sampled-out health checks to reduce log noise. Read
        # straight off the scope rather than building a Request: a skipped health ping should cost
        # a dict lookup and a random draw, nothing more.
        if scope["path"].rstrip("/") == "/health" and not should_sample_health_check():
            await self.app(scope, receive, send)
            return

        # **LOGIC_STEP**: A Request built from `scope` (and `receive`, unused otherwise) is a thin,
        # lazy view — no task group, no stream, no I/O — so header/query/client access below reuses
        # the same tested datastructures the BaseHTTPMiddleware version did.
        request = Request(scope, receive)

        # **LOGIC_STEP**: Resolve request ID from inbound trace headers (X-Request-ID,
        # then W3C traceparent), with a fresh UUID fallback. Lets multi-service traces
        # stay correlated when an upstream caller already assigned an id.
        request_id = _extract_or_generate_request_id(request)

        # **LOGIC_STEP**: Set trace_id for all logs within this request scope.
        trace_token = set_trace_id(request_id)

        try:
            # **LOGIC_STEP**: Store request_id in request.state for exception handlers.
            # Request.state reads and writes `scope["state"]` directly (starlette.requests.State),
            # so this is visible to every later Request built from the same `scope` — including the
            # one the exception handler below builds — with no ASGI message involved.
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
            # NOTE: x-session-id and x-user-id are correlation only. Either header is entirely
            # client-controlled and unauthenticated — this middleware performs no check on them
            # beyond "present or absent" — so neither one may ever feed an authorization decision
            # or be treated as a caller's identity. A project that adds authentication derives the
            # real principal from its own auth layer and logs that under its own field, separately
            # from these.

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
                        # **LOGIC_STEP**: Wrap `send` rather than the response: the wrapped app
                        # writes straight through to the real ASGI `send`, one message at a time, so
                        # nothing here buffers a streamed body or blocks a background task the way
                        # BaseHTTPMiddleware's memory stream did.
                        observer = _ResponseObserver()
                        await self.app(scope, receive, _observing_send(send, request_id, observer))

                        # **LOGIC_STEP**: Attach response metadata to span output for automatic
                        # logging on span finish. See _ResponseObserver for what response_type and
                        # response_size mean now and why.
                        span_ctx.output = observer.as_span_output()

            # **LOGIC_STEP**: Span is now closed — span.finish and, because the span above declares
            # itself a trace root, request.summary too.
            # NOTE: Rewriting the whole full-trace file here, on every request — reading all of
            # it, rebuilding the tree for every trace it holds, writing it all back — would be
            # synchronous work inside an async handler, with no lock: lines appended by concurrent
            # requests between the read and the write would be silently destroyed, and the cost
            # would grow with the file. The summary is regenerated once at shutdown
            # (project/launcher/main.py) and on demand with `make format-trace`, which is every
            # bit as useful and cannot lose a line.
        finally:
            # **LOGIC_STEP**: Reset trace_id to previous state, on every path including a raised
            # exception — this `finally` is not inside the `with logger.span(...)` block above, so
            # it also runs after that span has already logged span.error and re-raised.
            reset_trace_id(trace_token)


# NOTE: Removing the class above's Path(response.path).stat() call (there was no faithful pure-ASGI
# equivalent — file size now comes from the Content-Length the downstream FileResponse already
# computed, see _ResponseObserver.record_pathsend) removes the reason pyproject.toml's
# `[tool.ruff.lint]` comment gives for excluding ASYNC240 from the ASYNC rule family: that comment
# names this file as the sole reason. Not changed here — pyproject.toml is out of scope for this
# change — flagged for whoever reconciles it.
