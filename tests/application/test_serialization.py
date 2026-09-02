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

# ATTRIBUTE: _REDACTED_KEY_NAMES (tuple[str, ...])
# SUMMARY: Every name REDACT_KEYS holds, written out by hand rather than read off the set.
# NOTE: The parametrised test below takes its cases from REDACT_KEYS itself, so deleting
# "password" from the set also deletes the case that would have noticed: the suite went from ten
# cases to nine, stayed green, and `safe_serialize({"password": "hunter2"})` returned the password
# in clear. Measured on 2026-09-02 — the comment on that test had claimed the opposite since the
# first commit. Only a copy that does not move with the set can see the set shrink; this is the
# same reason `test.sql_constant_round_trip` demands a clause pinned as text. Removing a key
# means editing this tuple in the same change, which is the point.
_REDACTED_KEY_NAMES = (
    "password",
    "token",
    "secret",
    "authorization",
    "api_key",
    "private_key",
    "credentials",
    "jwt",
    "bearer",
    "cookie",
)

# ATTRIBUTE: _EXEMPT_KEY_NAMES (tuple[str, ...])
# SUMMARY: Every name REDACT_EXEMPT_KEYS holds, for the same reason as _REDACTED_KEY_NAMES.
# NOTE: The mirror trap: drop "total_tokens" from the exempt set and the parametrised test drops
# the case with it, while every request summary starts reporting its token count as
# "***REDACTED***". The observability numbers this set exists to protect would vanish under a
# green suite.
_EXEMPT_KEY_NAMES = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "reasoning_tokens",
    "tokens_per_second",
    "total_input_tokens",
    "total_output_tokens",
)


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
        # **LOGIC_STEP**: Parametrised over the constant, so a key ADDED to the set is exercised
        # without anyone editing this file — that is the one direction a set-driven test covers.
        # It does not cover removal: a key deleted from REDACT_KEYS is a case deleted from this
        # test, and the suite stays green. The tuple-driven test below is what catches that.
        serialized = safe_serialize({key: "leaked-value"})

        assert serialized[key] == "***REDACTED***"

    # FUNCTION: test_every_key_named_by_hand_is_still_redacted
    # SUMMARY: Verify each name in the hand-written copy is redacted — the copy cannot shrink with the set.
    @pytest.mark.unit
    @pytest.mark.parametrize("key", _REDACTED_KEY_NAMES)
    def test_every_key_named_by_hand_is_still_redacted(self, key: str) -> None:
        # **LOGIC_STEP**: Runs the real function over a literal name, not a membership check on
        # the set: this fails both when the key leaves REDACT_KEYS and when the matcher itself
        # stops honouring it.
        serialized = safe_serialize({key: "leaked-value"})

        assert serialized[key] == "***REDACTED***"

    # FUNCTION: test_the_hand_written_copy_and_the_set_agree
    # SUMMARY: Verify the literal tuple and REDACT_KEYS name the same keys, so neither drifts.
    @pytest.mark.unit
    def test_the_hand_written_copy_and_the_set_agree(self) -> None:
        # **LOGIC_STEP**: Equality, both ways. A key added to the set without being added here
        # would be covered only by the set-driven test, which cannot see it removed later; a key
        # left here after leaving the set is a stale claim. Editing both in one change is the cost.
        assert set(_REDACTED_KEY_NAMES) == REDACT_KEYS

    # FUNCTION: test_redaction_ignores_case_and_hyphens
    # SUMMARY: Verify a key arriving as an HTTP header — capitalised, hyphenated — is still redacted.
    @pytest.mark.unit
    @pytest.mark.parametrize("key", ["Password", "AUTHORIZATION", "Api-Key", "Set-Cookie"])
    def test_redaction_ignores_case_and_hyphens(self, key: str) -> None:
        # **LOGIC_STEP**: Every other test in this class spells its keys in lower case, so the
        # `.lower().replace("-", "_")` in _redact_key had no test at all: removing it left 49
        # tests green while `Authorization` — the spelling every header arrives in — went
        # through in clear. Found by a reviewer's mutation on 2026-09-02.
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
        # That is the direction this test covers — an addition. A removal takes its case with it,
        # and the tuple-driven pair below is what sees that.
        serialized = safe_serialize({key: 1234})

        assert serialized[key] == 1234

    # FUNCTION: test_every_exempt_key_named_by_hand_still_survives
    # SUMMARY: Verify each name in the hand-written copy keeps its value — the copy cannot shrink with the set.
    @pytest.mark.unit
    @pytest.mark.parametrize("key", _EXEMPT_KEY_NAMES)
    def test_every_exempt_key_named_by_hand_still_survives(self, key: str) -> None:
        serialized = safe_serialize({key: 1234})

        assert serialized[key] == 1234

    # FUNCTION: test_the_hand_written_exempt_copy_and_the_set_agree
    # SUMMARY: Verify the literal tuple and REDACT_EXEMPT_KEYS name the same keys.
    @pytest.mark.unit
    def test_the_hand_written_exempt_copy_and_the_set_agree(self) -> None:
        assert set(_EXEMPT_KEY_NAMES) == REDACT_EXEMPT_KEYS

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
