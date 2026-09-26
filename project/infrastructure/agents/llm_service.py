# FILE: project/infrastructure/agents/llm_service.py
# SUMMARY: Public LLM service facade composing live-provider, mock-mode, and readiness subsystems.

import copy
from typing import Any, Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.tools import InjectedToolArg
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel

from project.core.config import get_settings
from project.core.logging import get_logger
from project.infrastructure.agents.llm_service_live import LLMServiceLiveMixin
from project.infrastructure.agents.llm_service_mock import LLMServiceMockMixin
from project.infrastructure.agents.llm_service_readiness import (
    LLMServiceReadinessMixin,
)


class SupportsAsyncInvoke(Protocol):
    async def ainvoke(self, input: list[BaseMessage]) -> BaseMessage: ...


# langchain leaves out of a tool's exported schema a field whose validation alias differs from
# its name while it has no plain alias — exactly what CoreModel's generator gives every field. A tool
# whose arguments inherited CoreModel therefore reached the provider as `"properties": {}`. Mock
# mode never reads that schema, which is why this runs at binding, in every mode: the first unit
# test to bind the tool fails, not the first live request. Found by the bench2 measurement
# (2026-09-24). The exported schema itself is checked, not the alias attributes: `Field(alias=...)`,
# `AliasChoices` and `AliasPath` all survive (measured, langchain-core 1.5.3). An injected argument
# is left out on purpose, alias or not, so it is skipped. A model class passed as the tool itself is
# not checked: it is exported under its aliases and parsed back through them, which works.
def _refuse_aliased_arguments(tool: Any) -> None:
    if isinstance(tool, dict):
        raise TypeError("Raw dict tools need a Pydantic argument schema")
    schema = getattr(tool, "args_schema", None)
    if isinstance(schema, dict):
        # run_tool (tool_runner.py) only validates Pydantic schemas; a dict one would
        # reach the tool body unchecked.
        raise TypeError(f"Tool {getattr(tool, 'name', '?')!r} needs a Pydantic argument schema")
    if isinstance(tool, type) or not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        return
    exported = convert_to_openai_tool(tool)["function"]["parameters"].get("properties", {})
    lost = sorted(
        name
        for name, field in schema.model_fields.items()
        if name not in exported
        and field.validation_alias is not None
        and not any(
            isinstance(marker, InjectedToolArg)
            or (isinstance(marker, type) and issubclass(marker, InjectedToolArg))
            for marker in field.metadata
        )
    )
    if lost:
        raise TypeError(
            f"Tool {getattr(tool, 'name', schema.__name__)!r} cannot be bound: its argument fields "
            f"{', '.join(lost)} are missing from the schema the provider receives, because langchain "
            "drops a field whose only alias is a validation alias — the model would never see them. "
            "Declare tool arguments on ToolArgs (project/application/core_model.py), not on CoreModel."
        )


class LLMService(
    LLMServiceReadinessMixin,
    LLMServiceMockMixin,
    LLMServiceLiveMixin,
):
    def __init__(self) -> None:
        # Initialize settings, logger, and mutable provider state.
        self._settings = get_settings()
        self._logger = get_logger(__name__)
        self._llm: BaseChatModel | None = None
        self._bound_llm: SupportsAsyncInvoke | None = None
        self._mock_tools: list[Any] = []

        # Initialize the default LLM or deterministic mock placeholders.
        self._initialize_llm()

    def is_mock_mode(self) -> bool:
        return self._settings.agent.llm_mode == "mock"

    async def call(self, messages: list[BaseMessage]) -> BaseMessage:
        # Use deterministic responses in mock mode and live provider calls otherwise.
        if self.is_mock_mode():
            return self._build_mock_response(messages)
        return await self._call_llm_with_retry(messages)

    def get_llm(self) -> BaseChatModel | None:
        return self._llm

    # Keep it — the receiver, not the caller's variable, is bound.
    # Mutating `self._mock_tools` and returning `self` would corrupt shared state:
    # CompositionRoot builds exactly one LLMService and hands the same object to every vertical,
    # so a second agentic vertical binding its own tools would silently replace the first one's —
    # last caller wins, no error, wrong tools on the next request. The copy is shallow on
    # purpose: `_llm`, `_settings` and `_logger` are shared, so nothing re-opens a provider
    # client, while `_mock_tools` and `_bound_llm` are per-binding. langchain's own `bind_tools`
    # already returns a new runnable rather than mutating.
    def bind_tools(self, tools: list[Any]) -> "LLMService":
        for tool in tools:
            _refuse_aliased_arguments(tool)
        bound = copy.copy(self)
        bound._mock_tools = list(tools)
        if self._llm is not None:
            bound._bound_llm = self._llm.bind_tools(tools)
        self._logger.log_system_event(
            event_name="tools_bound_to_llm",
            category="configuration",
            new_value={
                "tool_count": len(tools),
                "mode": self._settings.agent.llm_mode,
            },
        )
        return bound
