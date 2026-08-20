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
# SUMMARY: Raised when authentication fails or credentials are invalid.
class AuthenticationError(ProjectError):
    pass
