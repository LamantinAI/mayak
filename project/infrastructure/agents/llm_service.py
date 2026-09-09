# FILE: project/infrastructure/agents/llm_service.py
# SUMMARY: Public LLM service facade composing live-provider, mock-mode, and readiness subsystems.

import copy
from typing import Any, Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage

from project.core.config import get_settings
from project.core.logging import get_logger
from project.infrastructure.agents.llm_service_live import LLMServiceLiveMixin
from project.infrastructure.agents.llm_service_mock import LLMServiceMockMixin
from project.infrastructure.agents.llm_service_readiness import (
    LLMServiceReadinessMixin,
)


# CLASS: project.infrastructure.agents.llm_service.SupportsAsyncInvoke
# SUMMARY: Structural protocol for the runnable object used to invoke the LLM.
class SupportsAsyncInvoke(Protocol):
    # FUNCTION: ainvoke
    # SUMMARY: Asynchronously invoke the underlying runnable with chat messages.
    async def ainvoke(self, input: list[BaseMessage]) -> BaseMessage: ...


# CLASS: project.infrastructure.agents.llm_service.LLMService
# SUMMARY: Service facade for deterministic mock mode, live provider calls, and readiness checks.
class LLMService(
    LLMServiceReadinessMixin,
    LLMServiceMockMixin,
    LLMServiceLiveMixin,
):
    # FUNCTION: __init__
    # SUMMARY: Initialize the LLM service from global settings.
    def __init__(self) -> None:
        # **LOGIC_STEP**: Initialize settings, logger, and mutable provider state.
        self._settings = get_settings()
        self._logger = get_logger(__name__)
        self._llm: BaseChatModel | None = None
        self._bound_llm: SupportsAsyncInvoke | None = None
        self._mock_tools: list[Any] = []

        # **LOGIC_STEP**: Initialize the default LLM or deterministic mock placeholders.
        self._initialize_llm()

    # FUNCTION: is_mock_mode
    # SUMMARY: Report whether the service is operating in deterministic mock mode.
    # OUTPUT: (bool): True when mock mode is enabled.
    def is_mock_mode(self) -> bool:
        return self._settings.agent.llm_mode == "mock"

    # FUNCTION: call
    # SUMMARY: Call the LLM with the specified messages.
    async def call(self, messages: list[BaseMessage]) -> BaseMessage:
        # **LOGIC_STEP**: Use deterministic responses in mock mode and live provider calls otherwise.
        if self.is_mock_mode():
            return self._build_mock_response(messages)
        return await self._call_llm_with_retry(messages)

    # FUNCTION: get_llm
    # SUMMARY: Get the current LLM instance.
    # OUTPUT: (BaseChatModel | None): Current live provider client, if initialized.
    def get_llm(self) -> BaseChatModel | None:
        return self._llm

    # FUNCTION: bind_tools
    # SUMMARY: Return a view of this service bound to the given tools, leaving the shared instance untouched.
    # OUTPUT: (LLMService): A copy carrying these tools. Keep it — the receiver, not the caller's variable, is bound.
    # NOTE: Mutating `self._mock_tools` and returning `self` would corrupt shared state:
    # CompositionRoot builds exactly one LLMService and hands the same object to every vertical,
    # so a second agentic vertical binding its own tools would silently replace the first one's —
    # last caller wins, no error, wrong tools on the next request. The copy is shallow on
    # purpose: `_llm`, `_settings` and `_logger` are shared, so nothing re-opens a provider
    # client, while `_mock_tools` and `_bound_llm` are per-binding. langchain's own `bind_tools`
    # already returns a new runnable rather than mutating.
    def bind_tools(self, tools: list[Any]) -> "LLMService":
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
