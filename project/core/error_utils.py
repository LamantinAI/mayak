# FILE: project/core/error_utils.py
# SUMMARY: Safe exception helpers for client-facing messages and sanitized error logging.

from typing import Any

from project.core.logging.redaction import summarize_text
from project.domain.exceptions import (
    AuthenticationError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    ProjectError,
    ValidationError,
)

SAFE_EXTERNAL_SERVICE_MESSAGE = "An upstream service is temporarily unavailable"

SAFE_PROJECT_ERROR_MESSAGE = "The request could not be completed"

SAFE_INTERNAL_ERROR_MESSAGE = "An internal server error occurred"


def is_client_safe_project_error(error: ProjectError) -> bool:
    # Allow only domain-safe validation and state errors to expose their message.
    return isinstance(
        error,
        (ValidationError, NotFoundError, ConflictError, AuthenticationError),
    )


# Safe message suitable for HTTP and SSE responses.
def get_client_safe_message(error: Exception) -> str:
    # Preserve exact messages only for explicitly safe ProjectError subclasses.
    if isinstance(error, ProjectError) and is_client_safe_project_error(error):
        return str(error)

    # Use a stable upstream-failure message for dependency errors.
    if isinstance(error, ExternalServiceError):
        return SAFE_EXTERNAL_SERVICE_MESSAGE

    # Use a generic project-level message for all other ProjectError subclasses.
    if isinstance(error, ProjectError):
        return SAFE_PROJECT_ERROR_MESSAGE

    # Fall back to the generic internal error message for unexpected exceptions.
    return SAFE_INTERNAL_ERROR_MESSAGE


def summarize_exception_for_logging(error: Exception) -> dict[str, Any]:
    # Capture a structural summary of the original exception message without logging raw text.
    error_message = str(error)
    message_summary = summarize_text(error_message) if error_message else None

    # Return stable metadata that helps debugging while keeping payloads redacted.
    detail_safe_for_client = isinstance(error, ProjectError) and is_client_safe_project_error(error)
    return {
        "exception_type": type(error).__name__,
        "exception_module": type(error).__module__,
        # Only the fixed text a client gets in place of the exception's own. When the client gets
        # the exception's own text, that is what it sent coming back to it — kept out (ADR-013).
        "client_message": None if detail_safe_for_client else get_client_safe_message(error),
        "detail_safe_for_client": detail_safe_for_client,
        "message_summary": message_summary,
    }
