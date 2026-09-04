# FILE: project/domain/exceptions.py
# SUMMARY: Defines the hierarchy of custom exceptions for the entire application.


# CLASS: project.domain.exceptions.ProjectError
# EXTENDS: Exception
# SUMMARY: Base class for all custom exceptions in the application.
class ProjectError(Exception):
    pass


# CLASS: project.domain.exceptions.ValidationError
# EXTENDS: ProjectError
# SUMMARY: Raised when input data fails domain validation rules.
class ValidationError(ProjectError):
    pass


# CLASS: project.domain.exceptions.NotFoundError
# EXTENDS: ProjectError
# SUMMARY: Raised when a requested resource does not exist.
class NotFoundError(ProjectError):
    pass


# CLASS: project.domain.exceptions.ConflictError
# EXTENDS: ProjectError
# SUMMARY: Raised when an operation conflicts with the current state of a resource.
class ConflictError(ProjectError):
    pass


# CLASS: project.domain.exceptions.ExternalServiceError
# EXTENDS: ProjectError
# SUMMARY: Raised when an external service call fails.
class ExternalServiceError(ProjectError):
    pass


# CLASS: project.domain.exceptions.AuthenticationError
# EXTENDS: ProjectError
# SUMMARY: Raised when the CALLER's authentication fails or their credentials are invalid.
# NOTE: About whoever is calling this service, never about a credential this service presents to
# someone else — the handler answers it with 401, and error_utils treats its message as safe to
# return verbatim. For a provider that rejects OUR key, see UpstreamAuthenticationError below.
class AuthenticationError(ProjectError):
    pass


# CLASS: project.domain.exceptions.UpstreamAuthenticationError
# EXTENDS: ExternalServiceError
# SUMMARY: Raised when an upstream provider rejects the credentials this service presents to it.
# NOTE: A subclass of ExternalServiceError, so it answers 502 with the generic upstream message,
# and deliberately NOT of AuthenticationError: the caller's own credentials are fine, and telling
# them 401 would send them to re-authenticate against a problem they cannot fix. The distinct type
# is for us — a log line, a test, a vertical that wants to page someone about a dead key rather
# than watch a provider status page. Whose credentials failed is the only thing it adds.
class UpstreamAuthenticationError(ExternalServiceError):
    pass
