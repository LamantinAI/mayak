# FILE: tests/application/test_logging_redaction.py
# SUMMARY: Unit tests for safe logging summary helpers.

import pytest

from project.core.logging.redaction import (
    redact_traceback,
    summarize_chat_messages,
    summarize_mapping,
    summarize_payload,
    summarize_text,
)


# CLASS: tests.application.test_logging_redaction.TestLoggingRedaction
# SUMMARY: Verify logging helpers keep structure while dropping raw payload values.
class TestLoggingRedaction:
    # FUNCTION: test_summarize_text_uses_structure_only
    # SUMMARY: Verify text summaries retain only structural metadata without raw content.
    @pytest.mark.unit
    def test_summarize_text_uses_structure_only(self) -> None:
        value = "  hello\nworld  "

        summary = summarize_text(value)

        assert summary["length"] == len(value)
        assert summary["line_count"] == 2
        assert summary["has_leading_whitespace"] is True
        assert summary["has_trailing_whitespace"] is True
        assert "hello" not in str(summary)

    # FUNCTION: test_summarize_mapping_hides_values
    # SUMMARY: Verify mapping summaries retain only keys and counts.
    @pytest.mark.unit
    def test_summarize_mapping_hides_values(self) -> None:
        summary = summarize_mapping({"token": "secret", "region": "eu"})

        assert summary == {"key_count": 2, "keys": ["region", "token"]}

    # FUNCTION: test_summarize_chat_messages_hides_content
    # SUMMARY: Verify chat message summaries omit raw message text.
    @pytest.mark.unit
    def test_summarize_chat_messages_hides_content(self) -> None:
        summary = summarize_chat_messages(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "world!"},
            ]
        )

        assert summary["message_count"] == 2
        assert summary["roles"] == ["user", "assistant"]
        assert summary["content_lengths"] == [5, 6]
        assert "hello" not in str(summary)
        assert "world!" not in str(summary)

    # FUNCTION: test_summarize_payload_hides_nested_tool_values
    # SUMMARY: Verify payload summaries expose only structure for tool-style inputs.
    @pytest.mark.unit
    def test_summarize_payload_hides_nested_tool_values(self) -> None:
        summary = summarize_payload({"query": "show me users", "limit": 10})

        assert summary == {
            "value_type": "mapping",
            "key_count": 2,
            "keys": ["limit", "query"],
        }


class TestRedactTraceback:
    @pytest.mark.unit
    def test_scrubs_connection_string(self) -> None:
        tb = 'File "db.py", line 5\n  url = "postgresql://user:s3cret@db.host:5432/mydb"\n'  # allow-secret: fixture for the scrubber
        result = redact_traceback(tb)
        assert "s3cret" not in result
        assert "***REDACTED***" in result

    @pytest.mark.unit
    def test_scrubs_bearer_token(self) -> None:
        tb = 'headers = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.test"}\n'
        result = redact_traceback(tb)
        assert "eyJhbGciOi" not in result

    @pytest.mark.unit
    def test_scrubs_openai_api_key(self) -> None:
        tb = '  api_key = "sk-1234567890abcdefghijklmnop"\n'  # allow-secret: fixture for the scrubber
        result = redact_traceback(tb)
        assert "sk-1234567890" not in result

    @pytest.mark.unit
    def test_scrubs_key_value_assignment(self) -> None:
        tb = '  password = "hunter2"\n'
        result = redact_traceback(tb)
        assert "hunter2" not in result

    @pytest.mark.unit
    def test_preserves_normal_traceback(self) -> None:
        tb = 'File "app.py", line 10, in process\n    return data["key"]\nKeyError: "key"\n'
        result = redact_traceback(tb)
        assert result == tb
