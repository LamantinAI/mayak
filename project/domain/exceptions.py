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
# SUMMARY: Raised when an upstream provider refuses the credentials this service presents — either
# because it does not accept them at all, or because they do not carry the access being asked for.
# NOTE: A subclass of ExternalServiceError, so it answers 502 with the generic upstream message,
# and deliberately NOT of AuthenticationError: the caller's own credentials are fine, and telling
# them 401 would send them to re-authenticate against a problem they cannot fix. The distinct type
# is for us — a log line, a test, a vertical that wants to page someone about a dead key rather
# than watch a provider status page. Whose credentials failed is the only thing it adds.
class UpstreamAuthenticationError(ExternalServiceError):
    pass


# FUNCTION: is_client_rejection
# SUMMARY: Report whether `error` is a domain exception this application answers with a 4xx, as
# opposed to one it answers with a 5xx or one outside the ProjectError hierarchy altogether.
# INPUT: error (Exception): Anything caught — the hierarchy check is the whole test.
# OUTPUT: (bool): True for every ProjectError subclass except ExternalServiceError — the one
# subclass exception_handlers.py's _project_error_status maps onto 502.
# NOTE: Consumed by project.core.logging.logger.span() and by
# project.infrastructure.api.exception_handlers._render_project_error. A nested span — a repository
# call translating a duplicate-name insert into ConflictError, an application-layer span enforcing
# a precondition — used to log whatever it caught exactly like a real failure: ERROR level, full
# traceback. The very same exception reaches the handler a moment later and is answered
# 409/404/422/401, routine and logged at WARNING with no traceback, so a duplicate name produced
# two records that disagreed about whether anything had gone wrong — and the ERROR one, with its
# stack trace, was the one that looked worth investigating. Two independent agents building real
# projects on this template lost a session stage each to that trace before the cause turned out to
# be a domain rejection rather than a crash. Both call sites now judge by this one function, and
# TestBothCallSitesJudgeAlike pins that it agrees with the status the handler answers.
# NOTE: It lives beside the hierarchy it reads, not in project/core/error_utils.py where
# it was first written, because that module imports project.core.logging.redaction — and importing
# any submodule of that package runs its __init__, which imports logger, which imported this. A
# fresh process that touched error_utils before the logging package died on a partially
# initialised module; the suite never saw it because conftest always imported logging first.
# Measured on 2026-09-08 with `python -c "import project.core.error_utils"`, which raised
# ImportError on its own. The domain layer imports nothing, so a predicate over these classes
# cannot start that cycle again — see tests/application/test_module_imports_standalone.py.
def is_client_rejection(error: Exception) -> bool:
    return isinstance(error, ProjectError) and not isinstance(error, ExternalServiceError)
