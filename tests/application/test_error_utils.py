# FILE: tests/application/test_error_utils.py
# SUMMARY: Unit tests for safe client-facing exception messages and sanitized exception summaries.

import pytest

from project.core.error_utils import (
    SAFE_EXTERNAL_SERVICE_MESSAGE,
    SAFE_INTERNAL_ERROR_MESSAGE,
    SAFE_PROJECT_ERROR_MESSAGE,
    get_client_safe_message,
    summarize_exception_for_logging,
)
from project.domain.exceptions import ConflictError, ExternalServiceError, ProjectError


# CLASS: tests.application.test_error_utils.TestErrorUtils
# SUMMARY: Verify exception helpers keep client messages stable while redacting raw details.
class TestErrorUtils:
    # FUNCTION: test_get_client_safe_message_preserves_safe_domain_errors
    # SUMMARY: Verify safe domain-state errors keep their explicit message.
    @pytest.mark.unit
    def test_get_client_safe_message_preserves_safe_domain_errors(self) -> None:
        assert (
            get_client_safe_message(ConflictError("Task already exists")) == "Task already exists"
        )

    # FUNCTION: test_get_client_safe_message_redacts_external_service_details
    # SUMMARY: Verify upstream failures return a stable safe message.
    @pytest.mark.unit
    def test_get_client_safe_message_redacts_external_service_details(self) -> None:
        assert (
            get_client_safe_message(ExternalServiceError("Provider timeout token=secret"))
            == SAFE_EXTERNAL_SERVICE_MESSAGE
        )

    # FUNCTION: test_get_client_safe_message_redacts_generic_project_error_details
    # SUMMARY: Verify generic project errors do not expose their original message.
    @pytest.mark.unit
    def test_get_client_safe_message_redacts_generic_project_error_details(
        self,
    ) -> None:
        assert (
            get_client_safe_message(ProjectError("Internal state leak: foo=bar"))
            == SAFE_PROJECT_ERROR_MESSAGE
        )

    # FUNCTION: test_get_client_safe_message_redacts_unexpected_exceptions
    # SUMMARY: Verify unexpected exceptions return the stable internal error message.
    @pytest.mark.unit
    def test_get_client_safe_message_redacts_unexpected_exceptions(self) -> None:
        assert get_client_safe_message(RuntimeError("boom secret")) == SAFE_INTERNAL_ERROR_MESSAGE

    # FUNCTION: test_summarize_exception_for_logging_uses_structural_message_summary
    # SUMMARY: Verify exception summaries keep type information but not raw message text.
    @pytest.mark.unit
    def test_summarize_exception_for_logging_uses_structural_message_summary(
        self,
    ) -> None:
        summary = summarize_exception_for_logging(
            ExternalServiceError("Provider timeout token=secret")
        )

        assert summary["exception_type"] == "ExternalServiceError"
        assert summary["client_message"] == SAFE_EXTERNAL_SERVICE_MESSAGE
        assert summary["detail_safe_for_client"] is False
        assert summary["message_summary"]["length"] > 0
        assert "token=secret" not in str(summary)
