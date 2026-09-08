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

# ATTRIBUTE: SAFE_EXTERNAL_SERVICE_MESSAGE (str)
# SUMMARY: Stable client-facing message for upstream dependency failures.
SAFE_EXTERNAL_SERVICE_MESSAGE = "An upstream service is temporarily unavailable"

# ATTRIBUTE: SAFE_PROJECT_ERROR_MESSAGE (str)
# SUMMARY: Stable client-facing message for generic project-level failures.
SAFE_PROJECT_ERROR_MESSAGE = "The request could not be completed"

# ATTRIBUTE: SAFE_INTERNAL_ERROR_MESSAGE (str)
# SUMMARY: Stable client-facing message for unexpected internal failures.
SAFE_INTERNAL_ERROR_MESSAGE = "An internal server error occurred"


# FUNCTION: is_client_safe_project_error
# SUMMARY: Report whether a ProjectError subclass is allowed to expose its exact message to clients.
# OUTPUT: (bool): True when the message can be returned verbatim to clients.
def is_client_safe_project_error(error: ProjectError) -> bool:
    # **LOGIC_STEP**: Allow only domain-safe validation and state errors to expose their message.
    return isinstance(
        error,
        (ValidationError, NotFoundError, ConflictError, AuthenticationError),
    )


# FUNCTION: get_client_safe_message
# SUMMARY: Return a stable client-facing message without exposing unsafe internal exception details.
# OUTPUT: (str): Safe message suitable for HTTP and SSE responses.
def get_client_safe_message(error: Exception) -> str:
    # **LOGIC_STEP**: Preserve exact messages only for explicitly safe ProjectError subclasses.
    if isinstance(error, ProjectError) and is_client_safe_project_error(error):
        return str(error)

    # **LOGIC_STEP**: Use a stable upstream-failure message for dependency errors.
    if isinstance(error, ExternalServiceError):
        return SAFE_EXTERNAL_SERVICE_MESSAGE

    # **LOGIC_STEP**: Use a generic project-level message for all other ProjectError subclasses.
    if isinstance(error, ProjectError):
        return SAFE_PROJECT_ERROR_MESSAGE

    # **LOGIC_STEP**: Fall back to the generic internal error message for unexpected exceptions.
    return SAFE_INTERNAL_ERROR_MESSAGE


# FUNCTION: summarize_exception_for_logging
# SUMMARY: Build a sanitized structured summary for exception logging without exposing raw messages.
def summarize_exception_for_logging(error: Exception) -> dict[str, Any]:
    # **LOGIC_STEP**: Capture a structural summary of the original exception message without logging raw text.
    error_message = str(error)
    message_summary = summarize_text(error_message) if error_message else None

    # **LOGIC_STEP**: Return stable metadata that helps debugging while keeping payloads redacted.
    return {
        "exception_type": type(error).__name__,
        "exception_module": type(error).__module__,
        "client_message": get_client_safe_message(error),
        "detail_safe_for_client": (
            isinstance(error, ProjectError) and is_client_safe_project_error(error)
        ),
        "message_summary": message_summary,
    }
