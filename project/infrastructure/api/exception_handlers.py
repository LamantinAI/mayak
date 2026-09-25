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
from project.core.pydantic_errors import value_free_message
from project.domain.exceptions import (
    is_client_rejection,
    ProjectError,
    ValidationError,
    NotFoundError,
    ConflictError,
    ExternalServiceError,
    AuthenticationError,
)

logger = get_logger(__name__)


# value: Raw validation input from Pydantic error details.
def _safe_validation_input(value: object) -> str | None:
    # Represent only type and approximate size to avoid leaking user-provided data.
    if value is None:
        return None
    if isinstance(value, (str, bytes, list, tuple, dict, set)):
        return f"{type(value).__name__}(len={len(value)})"
    return type(value).__name__


# Lifted out of the handler so the log line above can be chosen by the same answer the
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


class ExceptionHandlerManager:
    def __init__(self, app: FastAPI):
        # Store FastAPI app instance.
        self.app = app
        # Register all exception handlers.
        self._register_handlers()

    def _register_handlers(self) -> None:
        # Register ProjectError handler.
        self.app.add_exception_handler(ProjectError, self._handle_project_error)

        # Register HTTPException handler.
        self.app.add_exception_handler(HTTPException, self._handle_http_exception)

        # Register Starlette HTTPException handler.
        self.app.add_exception_handler(
            StarletteHTTPException, self._handle_starlette_http_exception
        )

        # Register RequestValidationError handler.
        self.app.add_exception_handler(RequestValidationError, self._handle_validation_error)

        # Register generic Exception handler.
        self.app.add_exception_handler(Exception, self._handle_generic_exception)

    async def _handle_project_error(self, request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, ProjectError):
            return await self._handle_generic_exception(request, exc)
        return await self._render_project_error(request, exc)

    async def _render_project_error(self, request: Request, exc: ProjectError) -> JSONResponse:
        # A 404 or a 422 is this application working — it read a request it could
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

        error: dict[str, object] = {
            "type": "ProjectError",
            "message": get_client_safe_message(exc),
            "status_code": status_code,
        }
        # A rule that names its field answers in the shape _render_validation_error gives a
        # request the framework refused, so the client learns which field either way.
        if isinstance(exc, ValidationError) and exc.field is not None:
            error["details"] = [
                {"field": exc.field, "message": error["message"], "type": "value_error"}
            ]
        return JSONResponse(status_code=status_code, content={"error": error})

    async def _handle_http_exception(self, request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, HTTPException):
            return await self._handle_generic_exception(request, exc)
        return await self._render_http_exception(request, exc)

    async def _render_http_exception(self, request: Request, exc: HTTPException) -> JSONResponse:
        # Log the HTTP exception with context.
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

        # Return structured error response.
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

    async def _handle_starlette_http_exception(
        self, request: Request, exc: Exception
    ) -> JSONResponse:
        if not isinstance(exc, StarletteHTTPException):
            return await self._handle_generic_exception(request, exc)
        return await self._render_starlette_http_exception(request, exc)

    async def _render_starlette_http_exception(
        self, request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # Log the Starlette HTTP exception with context.
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

        # Return structured error response.
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

    async def _handle_validation_error(self, request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, RequestValidationError):
            return await self._handle_generic_exception(request, exc)
        return await self._render_validation_error(request, exc)

    async def _render_validation_error(
        self, request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # The response gets pydantic's message whole — it is about what this caller sent, and the
        # caller already has it. The log gets it only when the message names no value
        # (project/core/pydantic_errors.py): a validator raising ValueError(f"... {value}") put
        # the value into the record through this message — ADR-013.
        response_errors = []
        log_errors = []
        for error in exc.errors():
            field = ".".join(str(loc) for loc in error["loc"])
            response_errors.append({"field": field, "message": error["msg"], "type": error["type"]})
            log_errors.append(
                {
                    "field": field,
                    "message": value_free_message(error),
                    "type": error["type"],
                    "input_summary": _safe_validation_input(error.get("input")),
                }
            )

        # Log which fields failed and why, and NOT what the caller sent:
        # `_safe_validation_input` reduces every value to its type and length.
        # A 422 is the request being wrong, never the service.
        logger.log_client_error(
            error_type="validation_error",
            message=f"Request validation failed with {len(log_errors)} errors",
            exc_info=False,
            validation_errors=log_errors,
            path=request.url.path,
            method=request.method,
            request_id=getattr(request.state, "request_id", None),
        )

        # Return structured validation error response.
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

    async def _handle_generic_exception(self, request: Request, exc: Exception) -> JSONResponse:
        # The logging middleware already unwound and reset the trace ContextVar
        # by the time an unhandled exception reaches this handler, so this record — the only one
        # naming the real cause — needs its trace_id restored here or every trace-reading tool
        # drops it. Restore it from request.state for the duration of the call.
        stashed_trace_id = getattr(request.state, "trace_id", None)
        trace_token = (
            set_trace_id(stashed_trace_id)
            if isinstance(stashed_trace_id, str) and not get_trace_id()
            else None
        )
        try:
            # Log the unexpected exception as critical with full traceback.
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

        # Return generic error response (don't expose internal details).
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


def setup_exception_handlers(app: FastAPI) -> None:
    # Create exception handler manager and register handlers.
    ExceptionHandlerManager(app)
