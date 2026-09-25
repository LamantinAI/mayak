# FILE: tests/application/test_tool_argument_schema.py
# SUMMARY: Verify a language-model tool's parameters reach the provider's schema under their own names.

from __future__ import annotations

from typing import Annotated
from unittest.mock import patch

import pytest
from langchain_core.tools import InjectedToolArg, StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, Field

from project.application.core_model import CoreModel
from project.infrastructure.agents.llm_service import LLMService
from tests.conftest import _FixtureSettings as FixtureSettings


# Tool arguments declared the way a vertical declares its DTOs — on CoreModel.
class _SessionsOnDtoBase(CoreModel):
    hall_id: int


# The tool body; only its argument schema matters here.
def _find_sessions(hall_id: int, coach_id: str | None = None) -> str:
    """Find the training sessions booked in a hall."""
    return f"hall={hall_id} coach={coach_id}"


def _create_service(test_settings: FixtureSettings) -> LLMService:
    with patch(
        "project.infrastructure.agents.llm_service.get_settings",
        return_value=test_settings,
    ):
        return LLMService()


# Verify binding refuses a tool whose argument fields would vanish from the provider's schema.
# langchain leaves every field that has a validation alias out of the schema it sends to the
# provider, and CoreModel gives every field one (camelCase). A tool whose arguments inherit
# CoreModel therefore exported `"properties": {}` — measured with langchain-core 1.5.3 — and a live
# model calls it with no arguments at all. Mock mode never reads that schema, so nothing failed
# until a real provider was switched on. Found by the bench2 measurement (2026-09-24): hall_id,
# coach_id and squad_id disappeared from a scheduling agent's tools this way.
@pytest.mark.unit
def test_a_tool_whose_arguments_carry_aliases_is_refused_at_binding(
    test_settings: FixtureSettings,
) -> None:
    tool = StructuredTool.from_function(
        _find_sessions, args_schema=_SessionsOnDtoBase, name="find_sessions"
    )
    service = _create_service(test_settings)

    with pytest.raises(TypeError, match=r"find_sessions.*hall_id"):
        service.bind_tools([tool])


@pytest.mark.unit
def test_tool_args_export_every_field_under_its_own_name(
    test_settings: FixtureSettings,
) -> None:
    from project.application.core_model import ToolArgs

    class _SessionsOnToolArgs(ToolArgs):
        hall_id: int
        coach_id: str | None = None

    tool = StructuredTool.from_function(
        _find_sessions, args_schema=_SessionsOnToolArgs, name="find_sessions"
    )

    exported = convert_to_openai_tool(tool)["function"]["parameters"]
    assert set(exported["properties"]) == {"hall_id", "coach_id"}
    assert exported["required"] == ["hall_id"]
    _create_service(test_settings).bind_tools([tool])


# Verify the refusal is about fields the provider loses, not about aliases as such.
# The independent review of this change caught two false refusals in earlier versions: a
# plain `Field(alias=...)`, which langchain exports under the field's own name, and an injected
# argument carrying a validation alias, which is left out of the schema on purpose.
@pytest.mark.unit
def test_aliases_that_lose_nothing_are_bound(test_settings: FixtureSettings) -> None:
    # An explicitly aliased field the schema keeps, and an injected one it omits by design.
    class _SessionsWithHarmlessAliases(BaseModel):
        hall_id: int = Field(alias="hallId")
        caller: Annotated[str, InjectedToolArg, Field(validation_alias="callerId")] = ""

    tool = StructuredTool.from_function(
        _find_sessions, args_schema=_SessionsWithHarmlessAliases, name="find_sessions"
    )

    assert set(convert_to_openai_tool(tool)["function"]["parameters"]["properties"]) == {"hall_id"}
    _create_service(test_settings).bind_tools([tool])
