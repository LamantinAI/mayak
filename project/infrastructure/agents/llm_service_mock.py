# FILE: project/infrastructure/agents/llm_service_mock.py
# SUMMARY: Deterministic mock-response mixin for LLMService used in local development and tests.

import time
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from project.core.config import Settings
from project.core.logging import SemanticLogger
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

    # ATTRIBUTE: _mock_tool_args (dict[str, dict[str, Any]])
    # SUMMARY: Optional per-tool argument override, keyed by tool name. See the NOTE on
    # `_build_mock_response` for why this exists and, just as importantly, what it deliberately
    # does not attempt.
    _mock_tool_args: dict[str, dict[str, Any]]

    # FUNCTION: _next_uncalled_tool_name
    # SUMMARY: Return the first bound tool, in binding order, that has not yet produced a
    # ToolMessage anywhere in this conversation.
    def _next_uncalled_tool_name(self, messages: list[BaseMessage]) -> str | None: ...

    # FUNCTION: _extract_last_human_message
    # SUMMARY: Return the most recent human-authored message content.
    # OUTPUT: (str): Latest human message content or an empty string.
    def _extract_last_human_message(self, messages: list[BaseMessage]) -> str: ...

    # FUNCTION: _collect_tool_messages
    # SUMMARY: Return every ToolMessage anywhere in the conversation, in order.
    def _collect_tool_messages(self, messages: list[BaseMessage]) -> list[ToolMessage]: ...


# CLASS: project.infrastructure.agents.llm_service_mock.LLMServiceMockMixin
# SUMMARY: Mixin implementing deterministic mock-mode tool selection and response synthesis.
class LLMServiceMockMixin:
    # ATTRIBUTE: _mock_tool_args (dict[str, dict[str, Any]])
    # SUMMARY: Class-level default: no overrides, so `_build_mock_response` falls back to the
    # `{"query": <last human message>}` shape it always sent. A caller that needs a bound tool's
    # actual schema satisfied — Decimal, a nested object, an enum — sets this as an instance
    # attribute after `bind_tools(...)` (`bound._mock_tool_args = {"charge_customer": {...}}`).
    # No wiring change to LLMService.bind_tools was needed for this: it is a plain attribute, and
    # nothing here mutates the shared class-level dict, only reads it.
    _mock_tool_args: dict[str, dict[str, Any]] = {}

    # FUNCTION: _next_uncalled_tool_name
    # SUMMARY: Select the next bound tool, in binding order, that this conversation has not yet
    # called — the mechanism that turns one mock tool call into a cycle over every bound tool.
    # OUTPUT: (str | None): Next tool to call, or None once every bound tool has answered.
    # NOTE: Until 2026-09-08 mock mode always answered `_mock_tools[0]` and finalized the instant
    # any ToolMessage reached the tail of the conversation — so binding a second or third tool
    # changed nothing observable: the extra tools were dead weight. Two field builds hit this
    # independently and both wrote their own tool-selection layer on top of the mock to get a real
    # multi-tool loop running (see docs/adr/ADR-003-mock-first-llm-mode.md). Measured before this
    # fix, with three tools sharing a single compatible {"query": str} schema so an argument
    # mismatch could not also be the cause: round 1 called the first tool, round 2 finalized with
    # a text summary — the second and third tool were never reached even though two more rounds
    # were available. Scanning every ToolMessage in the conversation, not only the trailing block,
    # is deliberate: after round two the round-one ToolMessage is no longer trailing, so a
    # trailing-only scan would forget the first tool was already called and call it again.
    def _next_uncalled_tool_name(
        self: _LLMServiceMockContract,
        messages: list[BaseMessage],
    ) -> str | None:
        already_called = {
            message.name
            for message in self._collect_tool_messages(messages)
            if isinstance(message.name, str)
        }
        for tool in self._mock_tools:
            name = getattr(tool, "name", None)
            if isinstance(name, str) and name not in already_called:
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

    # FUNCTION: _collect_tool_messages
    # SUMMARY: Collect every tool message in the conversation, in the order they occurred.
    # NOTE: This used to be `_extract_trailing_tool_messages`, returning only the contiguous block
    # at the end of the list. That was correct for a single-tool loop, where the one and only
    # ToolMessage is always trailing when the mock is asked to finalize. A three-tool loop calls
    # this again after each round, and by round three the round-one and round-two ToolMessages are
    # separated from the tail by the AIMessages in between — a trailing-only scan would both
    # forget they were ever called (see `_next_uncalled_tool_name`) and drop them from the final
    # summary. Scanning the whole conversation costs nothing extra a mock response is not already
    # paying for and fixes both.
    def _collect_tool_messages(
        self: _LLMServiceMockContract, messages: list[BaseMessage]
    ) -> list[ToolMessage]:
        return [message for message in messages if isinstance(message, ToolMessage)]

    # FUNCTION: _build_mock_response
    # SUMMARY: Produce a deterministic assistant response without calling an external provider.
    # NOTE: What this mock guarantees and what it does not is the load-bearing fact for anyone
    # copying it into a vertical, and it lives here rather than only in the ADR because this is
    # the code a reader ends up in when the behavior surprises them. Guaranteed: with N tools
    # bound, calling N times in a row (each fed the previous round's ToolMessage) visits every
    # bound tool exactly once, in binding order, then finalizes — deterministic tool SELECTION.
    # Not guaranteed: valid ARGUMENTS for an arbitrary tool schema. The default
    # `{"query": <last human message>}` satisfies a tool whose schema happens to be `{query:
    # str}` and nothing else — a required Decimal, a nested object, or an enum field fails
    # `tool.ainvoke(...)` with the same pydantic ValidationError this file used to produce on the
    # very first call, args or no cycle. Generating a schema-valid instance for an arbitrary
    # pydantic model is a small library in its own right — guessing a Decimal that also satisfies
    # a domain invariant like "must be positive", or an enum member that means what the test
    # needs it to mean, is not something a generic mock can do without dragging real domain
    # knowledge into a file that has none. `_mock_tool_args` is the escape hatch instead: a test
    # writes the valid args down, same as `tests/support/scripted_llm.py` writes down a scripted
    # reply, because both are the caller declaring what only the caller can know.
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
            tool_lines = [
                f"{tool_message.name}: {tool_message.content}" for tool_message in tool_messages
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
