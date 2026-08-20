# FILE: tests/application/test_exceptions.py
# SUMMARY: Unit tests for the domain exception hierarchy.

import pytest

from project.domain.exceptions import (
    ProjectError,
    ValidationError,
    NotFoundError,
    ConflictError,
    ExternalServiceError,
    AuthenticationError,
)


# CLASS: tests.application.test_exceptions.TestExceptionHierarchy
# SUMMARY: Test suite for domain exception hierarchy and behavior.
class TestExceptionHierarchy:
    # FUNCTION: test_all_exceptions_inherit_from_project_error
    # SUMMARY: Verify all custom exceptions inherit from ProjectError.
    @pytest.mark.unit
    def test_all_exceptions_inherit_from_project_error(self) -> None:
        exceptions = [
            ValidationError,
            NotFoundError,
            ConflictError,
            ExternalServiceError,
            AuthenticationError,
        ]
        for exc_class in exceptions:
            assert issubclass(exc_class, ProjectError), (
                f"{exc_class.__name__} must inherit from ProjectError"
            )

    # FUNCTION: test_project_error_inherits_from_exception
    # SUMMARY: Verify ProjectError inherits from base Exception.
    @pytest.mark.unit
    def test_project_error_inherits_from_exception(self) -> None:
        assert issubclass(ProjectError, Exception)

    # FUNCTION: test_catch_all_via_project_error
    # SUMMARY: Verify all custom exceptions are caught by except ProjectError.
    @pytest.mark.unit
    def test_catch_all_via_project_error(self) -> None:
        exceptions = [
            ValidationError("bad input"),
            NotFoundError("not found"),
            ConflictError("conflict"),
            ExternalServiceError("external fail"),
            AuthenticationError("auth fail"),
        ]
        for exc in exceptions:
            try:
                raise exc
            except ProjectError as caught:
                assert caught is exc
            except Exception:
                pytest.fail(f"{type(exc).__name__} was not caught by except ProjectError")

    # FUNCTION: test_exception_message
    # SUMMARY: Verify exception message is preserved.
    @pytest.mark.unit
    def test_exception_message(self) -> None:
        msg = "Something went wrong"
        exc = ProjectError(msg)
        assert str(exc) == msg

    # FUNCTION: test_each_type_has_correct_message
    # SUMMARY: Verify each exception type preserves its message correctly.
    @pytest.mark.unit
    def test_each_type_has_correct_message(self) -> None:
        cases = {
            ValidationError: "Invalid data",
            NotFoundError: "Resource not found",
            ConflictError: "Already exists",
            ExternalServiceError: "Service unavailable",
            AuthenticationError: "Unauthorized",
        }
        for exc_class, message in cases.items():
            exc = exc_class(message)
            assert str(exc) == message

    # FUNCTION: test_exceptions_are_distinct_types
    # SUMMARY: Verify specific exception types are not caught by sibling handlers.
    @pytest.mark.unit
    def test_exceptions_are_distinct_types(self) -> None:
        with pytest.raises(NotFoundError):
            raise NotFoundError("not found")

        # NotFoundError should NOT be caught by ValidationError handler
        try:
            raise NotFoundError("not found")
        except ValidationError:
            pytest.fail("NotFoundError should not be caught by ValidationError handler")
        except NotFoundError:
            pass
