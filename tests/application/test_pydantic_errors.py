# FILE: tests/application/test_pydantic_errors.py
# SUMMARY: Every error type the log may quote word for word has a message built only from the schema.

import re
from typing import get_args

import pytest
from pydantic_core.core_schema import ErrorType

from project.core.pydantic_errors import VALUE_FREE_ERROR_TYPES

# pydantic_core's catalogue of its errors with their message templates. Not public API — reached
# for here, in a test, because it is the only place the templates are written down. If a bump
# removes it, this import fails loudly rather than the list going unchecked.
from pydantic_core._pydantic_core import list_all_errors

# Placeholders filled from the schema — a bound, a length limit, the expected choices, a class
# name, a count — never from what the caller sent.
_SCHEMA_PLACEHOLDERS = frozenset(
    {
        "expected",
        "expected_plural",
        "expected_schemes",
        "expected_version",
        "min_length",
        "max_length",
        "actual_length",
        "field_type",
        "gt",
        "ge",
        "lt",
        "le",
        "multiple_of",
        "max_digits",
        "decimal_places",
        "whole_digits",
        "class_name",
        "class",
        "pattern",
    }
)

_TEMPLATES: dict[str, str] = {
    str(error["type"]): error["message_template_python"] for error in list_all_errors()
}


class TestTheValueFreeList:
    @pytest.mark.unit
    def test_every_entry_is_an_error_pydantic_has(self) -> None:
        assert VALUE_FREE_ERROR_TYPES <= set(get_args(ErrorType))

    @pytest.mark.unit
    @pytest.mark.parametrize("error_type", sorted(VALUE_FREE_ERROR_TYPES))
    def test_the_message_is_built_from_the_schema_alone(self, error_type: str) -> None:
        placeholders = set(re.findall(r"\{(\w+)\}", _TEMPLATES[error_type]))

        assert placeholders <= _SCHEMA_PLACEHOLDERS, _TEMPLATES[error_type]

    # The two a check of the project's own raises, and the one the independent check caught.
    @pytest.mark.unit
    @pytest.mark.parametrize("error_type", ["value_error", "assertion_error", "union_tag_invalid"])
    def test_a_message_that_can_quote_the_input_is_not_listed(self, error_type: str) -> None:
        assert error_type not in VALUE_FREE_ERROR_TYPES
