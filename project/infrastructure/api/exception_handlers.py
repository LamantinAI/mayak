# FILE: project/infrastructure/api/exception_handlers.py
# SUMMARY: FastAPI exception handlers for translating domain exceptions to HTTP responses with semantic logging.

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from project.core.error_utils import (
    SAFE_INTERNAL_ERROR_MESSAGE,
    get_client_safe_message,
    summarize_exception_for_logging,
)
from project.core.logging import get_logger
from project.core.logging.context import get_trace_id, reset_trace_id, set_trace_id
from project.core.logging.redaction import summarize_text
from project.domain.exceptions import (
    is_client_rejection,
    ProjectError,
    ValidationError,
    NotFoundError,
    ConflictError,
    ExternalServiceError,
    AuthenticationError,
)

# ATTRIBUTE: logger (SemanticLogger)
# SUMMARY: Semantic logger used for API exception diagnostics.
logger = get_logger(__name__)


# FUNCTION: _safe_validation_input
# SUMMARY: Convert validation input to a low-risk log representation without storing full payloads.
# INPUT: value (object): Raw validation input from Pydantic error details.
def _safe_validation_input(value: object) -> str | None:
    # **LOGIC_STEP**: Represent only type and approximate size to avoid leaking user-provided data.
    if value is None:
        return None
    if isinstance(value, (str, bytes, list, tuple, dict, set)):
        return f"{type(value).__name__}(len={len(value)})"
    return type(value).__name__


# FUNCTION: _project_error_status
# SUMMARY: Map a domain error onto the status it answers with.
# INPUT: exc (ProjectError): The raised domain error.
# OUTPUT: (int): HTTP status code; 400 for a ProjectError subclass with no mapping of its own.
# NOTE: Lifted out of the handler so the log line above can be chosen by the same answer the
# response carries — a level decided separately from a status is a level that drifts from it.
def _project_error_status(exc: ProjectError) -> int:
    if isinstance(exc, ValidationError):
        return int(status.HTTP_422_UNPROCESSABLE_CONTENT)
    if isinstance(exc, NotFoundError):
        return int(status.HTTP_404_NOT_FOUND)
    if isinstance(exc, ConflictError):
        return int(status.HTTP_409_CONFLICT)
    if isinstance(exc, ExternalServiceError):
        return int(status.HTTP_502_BAD_GATEWAY)
    if isinstance(exc, AuthenticationError):
        return int(status.HTTP_401_UNAUTHORIZED)
    return int(status.HTTP_400_BAD_REQUEST)


# CLASS: project.infrastructure.api.exception_handlers.ExceptionHandlerManager
# SUMMARY: Manager class for registering exception handlers with FastAPI application.
class ExceptionHandlerManager:
    # FUNCTION: __init__
    # SUMMARY: Initialize the exception handler manager.
    def __init__(self, app: FastAPI):
        # **LOGIC_STEP**: Store FastAPI app instance.
        self.app = app
        # **LOGIC_STEP**: Register all exception handlers.
        self._register_handlers()

    # FUNCTION: _register_handlers
    # SUMMARY: Register all exception handlers with the FastAPI application.
    def _register_handlers(self) -> None:
        # **LOGIC_STEP**: Register ProjectError handler.
        self.app.add_exception_handler(ProjectError, self._handle_project_error)

        # **LOGIC_STEP**: Register HTTPException handler.
        self.app.add_exception_handler(HTTPException, self._handle_http_exception)

        # **LOGIC_STEP**: Register Starlette HTTPException handler.
        self.app.add_exception_handler(
            StarletteHTTPException, self._handle_starlette_http_exception
        )

        # **LOGIC_STEP**: Register RequestValidationError handler.
        self.app.add_exception_handler(RequestValidationError, self._handle_validation_error)

        # **LOGIC_STEP**: Register generic Exception handler.
        self.app.add_exception_handler(Exception, self._handle_generic_exception)

    # FUNCTION: _handle_project_error
    # SUMMARY: Handle ProjectError exceptions and translate to HTTP responses.
    # INPUT: exc (Exception): The exception instance provided by Starlette handler contract.
    async def _handle_project_error(self, request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, ProjectError):
            return await self._handle_generic_exception(request, exc)
        return await self._render_project_error(request, exc)

    # FUNCTION: _render_project_error
    # SUMMARY: Render ProjectError exceptions with domain-specific HTTP mapping.
    async def _render_project_error(self, request: Request, exc: ProjectError) -> JSONResponse:
        # **LOGIC_STEP**: A 404 or a 422 is this application working — it read a request it could
        # not serve and said so — and recording it at ERROR made a healthy service read as a
        # failing one, in the log and in `make format-trace`'s error count. The level is asked of
        # `is_client_rejection`, which is also what `logger.span()` asks a moment earlier about the
        # very same exception. Deriving it here from `status_code >= 500` instead gave the same
        # answers, but as a second copy of the rule: the two could drift, and a nested span and its
        # handler disagreeing about whether anything went wrong is the exact confusion this pair of
        # call sites exists to avoid.
        status_code = _project_error_status(exc)
        record = logger.log_client_error if is_client_rejection(exc) else logger.log_error
        record(
            error_type="project_error",
            message="Project error occurred",
            exception=exc,
            exc_info=False,
            path=request.url.path,
            method=request.method,
            request_id=getattr(request.state, "request_id", None),
            status_code=status_code,
            error_summary=summarize_exception_for_logging(exc),
        )

        # **LOGIC_STEP**: Return structured error response.
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "type": "ProjectError",
                    "message": get_client_safe_message(exc),
                    "status_code": status_code,
                }
            },
        )

    # FUNCTION: _handle_http_exception
    # SUMMARY: Handle FastAPI HTTPException instances.
    # INPUT: exc (Exception): The exception instance provided by Starlette handler contract.
    async def _handle_http_exception(self, request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, HTTPException):
            return await self._handle_generic_exception(request, exc)
        return await self._render_http_exception(request, exc)

    # FUNCTION: _render_http_exception
    # SUMMARY: Render FastAPI HTTPException instances.
    async def _render_http_exception(self, request: Request, exc: HTTPException) -> JSONResponse:
        # **LOGIC_STEP**: Log the HTTP exception with context.
        detail_text = str(exc.detail) if exc.detail is not None else ""
        record = logger.log_error if exc.status_code >= 500 else logger.log_client_error
        record(
            error_type="http_exception",
            message="HTTP exception encountered",
            exception=exc,
            exc_info=False,
            status_code=exc.status_code,
            detail_summary=summarize_text(detail_text) if detail_text else None,
            path=request.url.path,
            method=request.method,
            request_id=getattr(request.state, "request_id", None),
        )

        # **LOGIC_STEP**: Return structured error response.
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "type": "HTTPException",
                    "message": exc.detail,
                    "status_code": exc.status_code,
                }
            },
        )

    # FUNCTION: _handle_starlette_http_exception
    # SUMMARY: Handle Starlette HTTPException instances.
    # INPUT: exc (Exception): The exception instance provided by Starlette handler contract.
    async def _handle_starlette_http_exception(
        self, request: Request, exc: Exception
    ) -> JSONResponse:
        if not isinstance(exc, StarletteHTTPException):
            return await self._handle_generic_exception(request, exc)
        return await self._render_starlette_http_exception(request, exc)

    # FUNCTION: _render_starlette_http_exception
    # SUMMARY: Render Starlette HTTPException instances.
    async def _render_starlette_http_exception(
        self, request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # **LOGIC_STEP**: Log the Starlette HTTP exception with context.
        detail_text = str(exc.detail) if exc.detail is not None else ""
        record = logger.log_error if exc.status_code >= 500 else logger.log_client_error
        record(
            error_type="starlette_http_exception",
            message="Starlette HTTP exception encountered",
            exception=exc,
            exc_info=False,
            status_code=exc.status_code,
            detail_summary=summarize_text(detail_text) if detail_text else None,
            path=request.url.path,
            method=request.method,
            request_id=getattr(request.state, "request_id", None),
        )

        # **LOGIC_STEP**: Return structured error response.
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "type": "HTTPException",
                    "message": exc.detail,
                    "status_code": exc.status_code,
                }
            },
        )

    # FUNCTION: _handle_validation_error
    # SUMMARY: Handle RequestValidationError exceptions from Pydantic validation.
    # INPUT: exc (Exception): The exception instance provided by Starlette handler contract.
    async def _handle_validation_error(self, request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, RequestValidationError):
            return await self._handle_generic_exception(request, exc)
        return await self._render_validation_error(request, exc)

    # FUNCTION: _render_validation_error
    # SUMMARY: Render RequestValidationError exceptions without leaking user input.
    async def _render_validation_error(
        self, request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # **LOGIC_STEP**: Extract validation error details with input for logs.
        log_errors = []
        for error in exc.errors():
            log_errors.append(
                {
                    "field": ".".join(str(loc) for loc in error["loc"]),
                    "message": error["msg"],
                    "type": error["type"],
                    "input_summary": _safe_validation_input(error.get("input")),
                }
            )

        # **LOGIC_STEP**: Log which fields failed and why, and NOT what the caller sent:
        # `_safe_validation_input` reduces every value to its type and length. The comment here
        # said "including user input" until 2026-08-14, which described the opposite of what
        # the code does and read as permission to add the payload back.
        # **LOGIC_STEP**: A 422 is the request being wrong, never the service.
        logger.log_client_error(
            error_type="validation_error",
            message=f"Request validation failed with {len(log_errors)} errors",
            exc_info=False,
            validation_errors=log_errors,
            path=request.url.path,
            method=request.method,
            request_id=getattr(request.state, "request_id", None),
        )

        # **LOGIC_STEP**: Build response errors without user input to prevent data leakage.
        response_errors = [
            {
                "field": e["field"],
                "message": e["message"],
                "type": e["type"],
            }
            for e in log_errors
        ]

        # **LOGIC_STEP**: Return structured validation error response.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "type": "ValidationError",
                    "message": "Request validation failed",
                    "status_code": status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "details": response_errors,
                }
            },
        )

    # FUNCTION: _handle_generic_exception
    # SUMMARY: Handle unexpected generic exceptions as critical server errors.
    # OUTPUT: (JSONResponse): HTTP 500 response with generic error message.
    async def _handle_generic_exception(self, request: Request, exc: Exception) -> JSONResponse:
        # **LOGIC_STEP**: The logging middleware already unwound and reset the trace ContextVar
        # by the time an unhandled exception reaches this handler, so this record — the only one
        # naming the real cause — used to be written without a trace_id and was then dropped by
        # every trace-reading tool. Restore it from request.state for the duration of the call.
        stashed_trace_id = getattr(request.state, "trace_id", None)
        trace_token = (
            set_trace_id(stashed_trace_id)
            if isinstance(stashed_trace_id, str) and not get_trace_id()
            else None
        )
        try:
            # **LOGIC_STEP**: Log the unexpected exception as critical with full traceback.
            logger.log_critical(
                failure_type="unexpected_server_error",
                message="Unexpected server error occurred",
                impact_scope="current_request",
                exception=exc,
                exc_info=True,
                path=request.url.path,
                method=request.method,
                request_id=getattr(request.state, "request_id", None),
            )
        finally:
            if trace_token is not None:
                reset_trace_id(trace_token)

        # **LOGIC_STEP**: Return generic error response (don't expose internal details).
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "type": "InternalServerError",
                    "message": SAFE_INTERNAL_ERROR_MESSAGE,
                    "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                }
            },
        )


# FUNCTION: setup_exception_handlers
# SUMMARY: Setup function to register all exception handlers with FastAPI application.
def setup_exception_handlers(app: FastAPI) -> None:
    # **LOGIC_STEP**: Create exception handler manager and register handlers.
    ExceptionHandlerManager(app)
