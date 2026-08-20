# FILE: tests/application/test_serialization.py
# SUMMARY: Unit tests for low-level structured serialization helpers used by semantic logging.

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import pytest

from project.core.serialization import (
    MAX_COLLECTION_SIZE,
    MAX_DEPTH,
    REDACT_EXEMPT_KEYS,
    REDACT_KEYS,
    safe_serialize,
)

# ATTRIBUTE: _LOGGING_PACKAGE (Path)
# SUMMARY: The package whose own literal payload keys must survive redaction untouched.
_LOGGING_PACKAGE = Path(__file__).resolve().parents[2] / "project" / "core" / "logging"


# ENUM: tests.application.test_serialization.ExampleEnum
# SUMMARY: Simple enum used to verify enum serialization behavior.
class ExampleEnum(Enum):
    VALUE = "value"


# DATACLASS: tests.application.test_serialization.ExamplePayload
# SUMMARY: Dataclass payload used to verify nested serialization behavior.
@dataclass
class ExamplePayload:
    token: str
    values: list[int]


# CLASS: tests.application.test_serialization.TestSerialization
# SUMMARY: Verify low-level serialization keeps structure while applying truncation, redaction, and safety guards.
class TestSerialization:
    # FUNCTION: test_safe_serialize_redacts_sensitive_keys
    # SUMMARY: Verify sensitive key names are redacted recursively.
    @pytest.mark.unit
    def test_safe_serialize_redacts_sensitive_keys(self) -> None:
        payload = {"token": "secret", "nested": {"api_key": "hidden", "region": "eu"}}

        serialized = safe_serialize(payload)

        assert serialized["token"] == "***REDACTED***"
        assert serialized["nested"]["api_key"] == "***REDACTED***"
        assert serialized["nested"]["region"] == "eu"

    # FUNCTION: test_safe_serialize_redacts_every_declared_key
    # SUMMARY: Verify each name in REDACT_KEYS is actually redacted, not just the two spot-checked ones.
    @pytest.mark.unit
    @pytest.mark.parametrize("key", sorted(REDACT_KEYS))
    def test_safe_serialize_redacts_every_declared_key(self, key: str) -> None:
        # **LOGIC_STEP**: Parametrised over the constant instead of naming two keys by hand.
        # Deleting "password" from REDACT_KEYS left the whole suite green — the spot checks used
        # "token" and "api_key", so the one key most likely to appear in a log line was the one
        # nothing covered. Driving the test from the set means a key can never be dropped quietly.
        serialized = safe_serialize({key: "leaked-value"})

        assert serialized[key] == "***REDACTED***"

    # FUNCTION: test_safe_serialize_exempts_llm_token_counters
    # SUMMARY: Verify LLM usage counters like input_tokens/output_tokens/total_tokens
    #          are preserved intact despite containing the `token` substring.
    @pytest.mark.unit
    def test_safe_serialize_exempts_llm_token_counters(self) -> None:
        payload = {
            "input_tokens": 1234,
            "output_tokens": 567,
            "total_tokens": 1801,
            "prompt_tokens": 900,
            "completion_tokens": 901,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
            "reasoning_tokens": 30,
            "tokens_per_second": 45.5,
            # Still-sensitive siblings must remain redacted.
            "access_token": "ABC-secret",
            "api_key": "key-123",
        }

        serialized = safe_serialize(payload)

        assert serialized["input_tokens"] == 1234
        assert serialized["output_tokens"] == 567
        assert serialized["total_tokens"] == 1801
        assert serialized["prompt_tokens"] == 900
        assert serialized["completion_tokens"] == 901
        assert serialized["cache_creation_input_tokens"] == 10
        assert serialized["cache_read_input_tokens"] == 20
        assert serialized["reasoning_tokens"] == 30
        assert serialized["tokens_per_second"] == 45.5
        assert serialized["access_token"] == "***REDACTED***"
        assert serialized["api_key"] == "***REDACTED***"

    # FUNCTION: test_safe_serialize_exempts_every_declared_exempt_key
    # SUMMARY: Verify each name in REDACT_EXEMPT_KEYS survives serialization with its value intact.
    @pytest.mark.unit
    @pytest.mark.parametrize("key", sorted(REDACT_EXEMPT_KEYS))
    def test_safe_serialize_exempts_every_declared_exempt_key(self, key: str) -> None:
        # **LOGIC_STEP**: Parametrised over the constant, the mirror of the REDACT_KEYS test above.
        # The hand-written case listed nine names and the set had nine entries, so the two looked
        # like each other; when two names were added, the test kept passing without covering them.
        serialized = safe_serialize({key: 1234})

        assert serialized[key] == 1234

    # FUNCTION: test_logging_package_writes_no_key_its_own_redactor_would_hide
    # SUMMARY: Verify every literal payload key the logging package writes survives redaction.
    @pytest.mark.unit
    def test_logging_package_writes_no_key_its_own_redactor_would_hide(self) -> None:
        # **LOGIC_STEP**: Redaction matches key names by substring, and the logging package writes
        # its own metadata under literal names — so a name it invents can collide with REDACT_KEYS
        # and be blanked on the way out. That is not hypothetical: _emit_request_summary wrote
        # total_input_tokens / total_output_tokens, REDACT_EXEMPT_KEYS listed only the per-call
        # names emitted by llm.call, and every request summary shipped "***REDACTED***" where a
        # number belonged. Values a caller passes in are out of scope here — this covers only the
        # keys the package itself hard-codes, which are metadata by construction.
        offenders: list[str] = []

        for source_path in sorted(_LOGGING_PACKAGE.rglob("*.py")):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                keys: list[str] = []
                if isinstance(node, ast.Dict):
                    keys = [
                        key.value
                        for key in node.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    ]
                elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                    if isinstance(node.slice.value, str):
                        keys = [node.slice.value]
                for key in keys:
                    normalized = key.lower().replace("-", "_")
                    if normalized in REDACT_EXEMPT_KEYS:
                        continue
                    if any(fragment in normalized for fragment in REDACT_KEYS):
                        offenders.append(f"{source_path.name}: {key}")

        assert not offenders, (
            "logging package writes keys its own redactor blanks; "
            "add them to REDACT_EXEMPT_KEYS or rename them: " + ", ".join(sorted(set(offenders)))
        )

    # FUNCTION: test_safe_serialize_supports_dataclass_enum_and_non_serializable_values
    # SUMMARY: Verify serializer handles dataclasses, enums, and non-serializable objects without raising.
    @pytest.mark.unit
    def test_safe_serialize_supports_dataclass_enum_and_non_serializable_values(
        self,
    ) -> None:
        payload = ExamplePayload(token="abc", values=[1, 2, 3])

        serialized = safe_serialize(
            {"payload": payload, "enum": ExampleEnum.VALUE, "object": object()}
        )

        assert serialized["payload"]["token"] == "***REDACTED***"
        assert serialized["payload"]["values"] == [1, 2, 3]
        assert serialized["enum"] == "value"
        assert isinstance(serialized["object"], str)

    # FUNCTION: test_safe_serialize_limits_depth_and_collection_size
    # SUMMARY: Verify serializer truncates recursion depth and large collections to bounded sizes.
    @pytest.mark.unit
    def test_safe_serialize_limits_depth_and_collection_size(self) -> None:
        nested: dict[str, Any] = {}
        current = nested
        for index in range(MAX_DEPTH + 2):
            child = {"level": index}
            current["child"] = child
            current = child

        serialized_nested = safe_serialize(nested)
        serialized_large_list = safe_serialize(list(range(MAX_COLLECTION_SIZE + 5)))

        assert isinstance(serialized_nested["child"]["child"]["child"], dict)
        assert len(serialized_large_list) == MAX_COLLECTION_SIZE
