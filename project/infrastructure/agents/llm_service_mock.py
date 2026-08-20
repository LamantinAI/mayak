# FILE: project/infrastructure/agents/llm_service_mock.py
# SUMMARY: Deterministic mock-response mixin for LLMService used in local development and tests.

import time
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from project.core.config import Settings
from project.core.logging import SemanticLogger
from project.domain.exceptions import ExternalServiceError
from project.infrastructure.agents.llm_service_live import _build_full_trace_extras


# CLASS: project.infrastructure.agents.llm_service_mock._LLMServiceMockContract
# SUMMARY: Structural contract describing the shared LLMService state used by the mock-response mixin.
class _LLMServiceMockContract(Protocol):
    # ATTRIBUTE: _settings (Settings)
    # SUMMARY: Validated application settings used to label deterministic mock responses.
    _settings: Settings

    # ATTRIBUTE: _logger (SemanticLogger)
    # SUMMARY: Semantic logger used for mock LLM call telemetry.
    _logger: SemanticLogger

    # ATTRIBUTE: _mock_tools (list[Any])
    # SUMMARY: Tools currently bound to the service for deterministic mock tool-call flows.
    _mock_tools: list[Any]

    # FUNCTION: _get_mock_tool_name
    # SUMMARY: Return the selected deterministic tool name when tools are available.
    def _get_mock_tool_name(self) -> str | None: ...

    # FUNCTION: _extract_last_human_message
    # SUMMARY: Return the most recent human-authored message content.
    # OUTPUT: (str): Latest human message content or an empty string.
    def _extract_last_human_message(self, messages: list[BaseMessage]) -> str: ...

    # FUNCTION: _extract_trailing_tool_messages
    # SUMMARY: Return the trailing contiguous block of tool messages.
    def _extract_trailing_tool_messages(
        self,
        messages: list[BaseMessage],
    ) -> list[ToolMessage]: ...

    # FUNCTION: _should_use_mock_tool
    # SUMMARY: Decide whether deterministic mock mode should emit a tool call.
    # OUTPUT: (bool): True when mock mode should call a tool.
    def _should_use_mock_tool(self) -> bool: ...


# CLASS: project.infrastructure.agents.llm_service_mock.LLMServiceMockMixin
# SUMMARY: Mixin implementing deterministic mock-mode tool selection and response synthesis.
class LLMServiceMockMixin:
    # FUNCTION: _get_mock_tool_name
    # SUMMARY: Select the preferred tool name for deterministic mock tool-calling flows.
    # OUTPUT: (str | None): Selected tool name or None when no tools are bound.
    # NOTE: This used to prefer a tool named "template_reference_query" before falling back to the
    # first bound tool. No tool of that name exists here, so the preference never fired and the
    # fallback did all the work — while the name sent every reader hunting for a definition that
    # was not there. It is gone: mock mode calls whatever the caller bound first.
    def _get_mock_tool_name(self: _LLMServiceMockContract) -> str | None:
        if self._mock_tools:
            name = getattr(self._mock_tools[0], "name", None)
            if isinstance(name, str):
                return name

        return None

    # FUNCTION: _extract_last_human_message
    # SUMMARY: Retrieve the latest human-authored message content from a prompt stack.
    # OUTPUT: (str): Latest human message content or an empty string.
    def _extract_last_human_message(
        self: _LLMServiceMockContract,
        messages: list[BaseMessage],
    ) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                content = message.content
                return content if isinstance(content, str) else str(content)
        return ""

    # FUNCTION: _extract_trailing_tool_messages
    # SUMMARY: Collect the most recent contiguous block of tool messages.
    def _extract_trailing_tool_messages(
        self: _LLMServiceMockContract, messages: list[BaseMessage]
    ) -> list[ToolMessage]:
        trailing: list[ToolMessage] = []
        for message in reversed(messages):
            if isinstance(message, ToolMessage):
                trailing.append(message)
                continue
            break
        return list(reversed(trailing))

    # FUNCTION: _should_use_mock_tool
    # SUMMARY: Decide whether deterministic mock mode should produce a tool call.
    # OUTPUT: (bool): True when the mock backend should request a tool.
    def _should_use_mock_tool(self: _LLMServiceMockContract) -> bool:
        # **LOGIC_STEP**: Bound tools mean the caller is running an agent loop, and in mock mode the
        # loop has to run for the test to be worth anything. This used to require one of five
        # English words — "template", "capabilities", "workflow", "plan", "reference" — in the
        # prompt, so a domain prompt in any other vocabulary got a plain text answer and the tool
        # branch never executed, which left a tool-calling vertical exercisable only against a
        # live provider — and CI has no key for one.
        #
        # The trade this accepts: the mock never declines to call a tool. A live model decides per
        # prompt; the mock decides once, at bind time. That makes "the model answered without
        # reaching for a tool" untestable here — assert that path against a fake LLMPort in the
        # vertical's own test, not against this mock.
        return bool(self._get_mock_tool_name() and self._mock_tools)

    # FUNCTION: _build_mock_response
    # SUMMARY: Produce a deterministic assistant response without calling an external provider.
    def _build_mock_response(
        self: _LLMServiceMockContract,
        messages: list[BaseMessage],
    ) -> BaseMessage:
        start_ns = time.perf_counter_ns()
        model = self._settings.llm.model
        last_user_message = self._extract_last_human_message(messages)
        trailing_tool_messages = self._extract_trailing_tool_messages(messages)

        if trailing_tool_messages:
            tool_lines = [
                f"{tool_message.name}: {tool_message.content}"
                for tool_message in trailing_tool_messages
            ]
            response: BaseMessage = AIMessage(
                content=(
                    f"Mock response from {model}.\nTool-assisted summary:\n" + "\n".join(tool_lines)
                )
            )
        elif self._should_use_mock_tool():
            tool_name = self._get_mock_tool_name()
            if tool_name is None:
                raise ExternalServiceError(
                    "Mock tool name resolved to None after _should_use_mock_tool check"
                )
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": tool_name,
                        "args": {"query": last_user_message},
                        "id": f"mock-tool-{len(messages)}-{len(last_user_message)}",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            response = AIMessage(
                content=(
                    f"Mock response from {model}. "
                    f"Processed {len(messages)} message(s). "
                    f"Last user message length={len(last_user_message)}."
                )
            )

        duration_ms = (time.perf_counter_ns() - start_ns) / 1e6
        completion_text = response.content if isinstance(response.content, str) else None
        full_trace = self._settings.observability.full_trace_enabled
        extras = _build_full_trace_extras(full_trace, messages, completion_text)
        self._logger.log_llm_call(
            model,
            duration_ms=duration_ms,
            input_tokens=sum(
                len(message.content) if isinstance(message.content, str) else 0
                for message in messages
            ),
            output_tokens=(len(response.content) if isinstance(response.content, str) else None),
            success=True,
            **extras,
        )
        return response
