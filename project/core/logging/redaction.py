# FILE: project/core/logging/redaction.py
# SUMMARY: Safe logging summaries for user-provided text payloads and request-like structures.

import re
from typing import Any, Mapping


def summarize_text(value: str) -> dict[str, Any]:
    # Retain only non-sensitive structural metadata about the text.
    return {
        "length": len(value),
        "is_empty": len(value) == 0,
        "line_count": value.count("\n") + 1,
        "has_leading_whitespace": bool(value[:1].isspace()),
        "has_trailing_whitespace": bool(value[-1:].isspace()),
    }


def summarize_mapping(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    # Preserve absence while avoiding logging raw values.
    if value is None:
        return None

    keys = sorted(str(key) for key in value.keys())
    return {
        "key_count": len(keys),
        "keys": keys,
    }


def summarize_payload(value: Any) -> dict[str, Any] | None:
    # Preserve explicit absence while avoiding raw value logging.
    if value is None:
        return None

    # Reuse specialized helpers for common sensitive payload shapes.
    if isinstance(value, str):
        return {"value_type": "text", **summarize_text(value)}

    if isinstance(value, Mapping):
        summary = summarize_mapping(value)
        if summary is None:
            return None
        return {"value_type": "mapping", **summary}

    if isinstance(value, (list, tuple, set)):
        item_types = sorted({type(item).__name__ for item in value})
        return {
            "value_type": type(value).__name__,
            "item_count": len(value),
            "item_types": item_types,
        }

    # Fall back to type-only summaries for scalars and arbitrary objects.
    return {"value_type": type(value).__name__}


_TRACEBACK_REDACT_PATTERNS = [
    # The optional `+driver` segment is not decoration. SQLAlchemy's async URL carries the
    # driver name between the scheme and the separator — this project's own DSN shape — and the
    # earlier pattern required the scheme to touch `://`, so the one connection string most likely
    # to appear in a traceback here was the one form that slipped through untouched.
    re.compile(
        r"(postgresql|postgres|mysql|mariadb|mssql|mongodb|redis|amqp)(\+[a-z0-9_]+)?://[^\s'\"]+",
        re.IGNORECASE,
    ),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(
        r"(api[_-]?key|token|secret|password|authorization)\s*[=:]\s*['\"]?[^\s'\"]+",
        re.IGNORECASE,
    ),
    # Bare credentials carry no `key=` prefix, so the assignment pattern above
    # never sees them. A JWT or a provider-prefixed token pasted into a log line, an exception
    # message, or a request body is the common shape, and it was passing through untouched.
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sk_(live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]
_REDACTED = "***REDACTED***"


def redact_secrets(text: str) -> str:
    """Scrub known secret patterns from arbitrary log-bound text."""
    result = text
    for pattern in _TRACEBACK_REDACT_PATTERNS:
        result = pattern.sub(_REDACTED, result)
    return result


def redact_traceback(tb_text: str) -> str:
    """Scrub known secret patterns from traceback text."""
    return redact_secrets(tb_text)


def summarize_chat_messages(messages: list[Any]) -> dict[str, Any]:
    # Extract roles and lengths from compatible message objects or mappings.
    roles: list[str] = []
    lengths: list[int] = []
    for message in messages:
        if isinstance(message, Mapping):
            role = message.get("role")
            content = message.get("content", "")
        else:
            role = getattr(message, "role", None)
            content = getattr(message, "content", "")

        roles.append(str(role) if role is not None else "unknown")
        lengths.append(len(content) if isinstance(content, str) else 0)

    return {
        "message_count": len(messages),
        "roles": roles,
        "content_lengths": lengths,
        "total_content_length": sum(lengths),
    }
