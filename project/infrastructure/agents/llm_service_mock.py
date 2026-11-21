# FILE: project/infrastructure/agents/llm_service_mock.py
# SUMMARY: Deterministic mock-response mixin for LLMService used in local development and tests.

import time
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from project.core.config import Settings
from project.core.logging import SemanticLogger
from project.infrastructure.agents.llm_service_live import _build_full_trace_extras


class _LLMServiceMockContract(Protocol):
    # Validated application settings used to label deterministic mock responses.
    _settings: Settings

    _logger: SemanticLogger

    # Tools currently bound to the service for deterministic mock tool-call flows.
    _mock_tools: list[Any]

    # Optional per-tool argument override, keyed by tool name. See the NOTE on
    # `_build_mock_response` for why this exists and, just as importantly, what it deliberately
    # does not attempt.
    _mock_tool_args: dict[str, dict[str, Any]]

    # Return the first bound tool, in binding order, that has not yet produced a
    # ToolMessage anywhere in this conversation.
    def _next_uncalled_tool_name(self, messages: list[BaseMessage]) -> str | None: ...

    # Return the names of every tool the conversation already has a result for.
    def _answered_tool_names(self, messages: list[BaseMessage]) -> set[str]: ...

    # Latest human message content or an empty string.
    def _extract_last_human_message(self, messages: list[BaseMessage]) -> str: ...

    # Return every ToolMessage of the current turn, in order.
    def _collect_tool_messages(self, messages: list[BaseMessage]) -> list[ToolMessage]: ...

    # Return the messages that follow the most recent human question.
    def _current_turn(self, messages: list[BaseMessage]) -> list[BaseMessage]: ...

    # Return the tool name behind every tool-call id issued this turn.
    def _tool_calls_by_id(self, messages: list[BaseMessage]) -> dict[str, str]: ...

    # Return the tool a result answers, by its own field or by the call id.
    def _tool_name_of(
        self, tool_message: ToolMessage, by_call_id: dict[str, str]
    ) -> str | None: ...


class LLMServiceMockMixin:
    # Set per-tool arguments on the bound instance, leaving the shared default untouched.
    _mock_tool_args: dict[str, dict[str, Any]] = {}

    # Visit bound tools once per turn. A trailing-only scan would forget earlier tool results.
    def _next_uncalled_tool_name(
        self: _LLMServiceMockContract,
        messages: list[BaseMessage],
    ) -> str | None:
        already_called = self._answered_tool_names(messages)
        for tool in self._mock_tools:
            name = getattr(tool, "name", None)
            if isinstance(name, str) and name not in already_called:
                return name
        return None

    # ToolMessage.name is optional. Resolve the tool by its originating call ID too.
    def _answered_tool_names(
        self: _LLMServiceMockContract,
        messages: list[BaseMessage],
    ) -> set[str]:
        by_call_id = self._tool_calls_by_id(messages)

        answered: set[str] = set()
        for tool_message in self._collect_tool_messages(messages):
            resolved = self._tool_name_of(tool_message, by_call_id)
            if resolved is not None:
                answered.add(resolved)
        return answered

    # Returns tool name per call id.
    def _tool_calls_by_id(
        self: _LLMServiceMockContract,
        messages: list[BaseMessage],
    ) -> dict[str, str]:
        by_call_id: dict[str, str] = {}
        for message in self._current_turn(messages):
            for call in getattr(message, "tool_calls", None) or []:
                call_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
                call_name = (
                    call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
                )
                if isinstance(call_id, str) and isinstance(call_name, str):
                    by_call_id[call_id] = call_name
        return by_call_id

    def _tool_name_of(
        self: _LLMServiceMockContract,
        tool_message: ToolMessage,
        by_call_id: dict[str, str],
    ) -> str | None:
        if isinstance(tool_message.name, str) and tool_message.name:
            return tool_message.name
        return by_call_id.get(tool_message.tool_call_id)

    def _extract_last_human_message(
        self: _LLMServiceMockContract,
        messages: list[BaseMessage],
    ) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                content = message.content
                return content if isinstance(content, str) else str(content)
        return ""

    # Keep earlier tool results even when assistant messages separate them from the tail.
    def _collect_tool_messages(
        self: _LLMServiceMockContract, messages: list[BaseMessage]
    ) -> list[ToolMessage]:
        return [
            message for message in self._current_turn(messages) if isinstance(message, ToolMessage)
        ]

    # A new human turn starts a new tool cycle; earlier results do not count.
    def _current_turn(
        self: _LLMServiceMockContract, messages: list[BaseMessage]
    ) -> list[BaseMessage]:
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], HumanMessage):
                return messages[index + 1 :]
        return list(messages)

    # Mock calls each bound tool once in binding order, then summarizes. Its default query
    # fits only compatible schemas. Set _mock_tool_args on the bound instance for other tools
    # or script full replies in tests/support/scripted_llm.py. See ADR-003.
    def _build_mock_response(
        self: _LLMServiceMockContract,
        messages: list[BaseMessage],
    ) -> BaseMessage:
        start_ns = time.perf_counter_ns()
        model = self._settings.llm.model
        last_user_message = self._extract_last_human_message(messages)
        next_tool_name = self._next_uncalled_tool_name(messages)
        tool_messages = self._collect_tool_messages(messages)

        if next_tool_name is not None:
            args = self._mock_tool_args.get(next_tool_name, {"query": last_user_message})
            response: BaseMessage = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": next_tool_name,
                        "args": args,
                        "id": f"mock-tool-{len(messages)}-{len(last_user_message)}",
                        "type": "tool_call",
                    }
                ],
            )
        elif tool_messages:
            # Resolve names in the summary the same way as in tool selection.
            by_call_id = self._tool_calls_by_id(messages)
            tool_lines = [
                f"{self._tool_name_of(tool_message, by_call_id) or 'tool'}: {tool_message.content}"
                for tool_message in tool_messages
            ]
            response = AIMessage(
                content=(
                    f"Mock response from {model}.\nTool-assisted summary:\n" + "\n".join(tool_lines)
                )
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
