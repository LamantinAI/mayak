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
    # Class-level default: no overrides, so `_build_mock_response` falls back to the
    # `{"query": <last human message>}` shape it always sent. A caller that needs a bound tool's
    # actual schema satisfied — Decimal, a nested object, an enum — sets this as an instance
    # attribute after `bind_tools(...)` (`bound._mock_tool_args = {"charge_customer": {...}}`).
    # No wiring change to LLMService.bind_tools was needed for this: it is a plain attribute, and
    # nothing here mutates the shared class-level dict, only reads it.
    _mock_tool_args: dict[str, dict[str, Any]] = {}

    # Cycling through bound tools in order matters: always answering `_mock_tools[0]` and
    # finalizing the instant any ToolMessage reaches the tail of the conversation would make
    # every extra bound tool dead weight — binding a second or third tool would change nothing
    # observable. Two field builds hit exactly that independently, each writing its own
    # tool-selection layer on top of the mock to get a real multi-tool loop running (see
    # docs/adr/ADR-003-mock-first-llm-mode.md). Measured with three tools sharing a single
    # compatible {"query": str} schema so an argument mismatch could not also be the cause:
    # without this cycling, round 1 calls the first tool and round 2 finalizes with a text
    # summary — the second and third tool are never reached even though two more rounds are
    # available. Scanning every ToolMessage in the conversation, not only the trailing block,
    # is deliberate: after round two the round-one ToolMessage is no longer trailing, so a
    # trailing-only scan would forget the first tool was already called and call it again.
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

    # Returns tool names, resolved by ToolMessage.name where it is set and by the
    # tool call the message answers where it is not.
    # `ToolMessage.name` is optional in langchain-core, and the hand-written agent loop this
    # template tells a vertical to write is exactly the caller most likely to omit it —
    # `ToolMessage(content=..., tool_call_id=...)` is the shortest thing that works. Reading only
    # `.name` then leaves `already_called` empty forever, so `_next_uncalled_tool_name` keeps
    # answering with the first bound tool and the vertical's loop spins until its own round cap
    # stops it. Falling back to the tool call the message answers costs one pass over the
    # AIMessages and removes a hang that would have looked like a bug in the vertical.
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

    # A trailing-only scan — only the contiguous block of ToolMessages at the end of the
    # list — is correct for a single-tool loop, where the one and only ToolMessage is always
    # trailing when the mock is asked to finalize. A three-tool loop calls this again after each
    # round, and by round three the round-one and round-two ToolMessages are separated from the
    # tail by the AIMessages in between — a trailing-only scan would both forget they were ever
    # called (see `_next_uncalled_tool_name`) and drop them from the final summary. Scanning the
    # whole conversation costs nothing extra a mock response is not already paying for and fixes
    # both.
    def _collect_tool_messages(
        self: _LLMServiceMockContract, messages: list[BaseMessage]
    ) -> list[ToolMessage]:
        return [
            message for message in self._current_turn(messages) if isinstance(message, ToolMessage)
        ]

    # Returns everything after the last HumanMessage, or all of it when the
    # conversation has no human turn at all.
    # The cycle over bound tools has to remember what it already called within one answer and
    # forget it at the next question. Scanning the whole conversation gets the first half right and
    # the second half badly wrong: measured, a second question in the same conversation got no tool
    # call at all, because every tool still counted as answered from the first one. A trailing-only
    # scan has the opposite failure — it forgets mid-answer and calls the first tool forever. The
    # turn is the unit that makes both correct.
    def _current_turn(
        self: _LLMServiceMockContract, messages: list[BaseMessage]
    ) -> list[BaseMessage]:
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], HumanMessage):
                return messages[index + 1 :]
        return list(messages)

    # What this mock guarantees and what it does not is the load-bearing fact for anyone
    # copying it into a vertical, and it lives here rather than only in the ADR because this is
    # the code a reader ends up in when the behavior surprises them. Guaranteed: with N tools
    # bound, calling N times in a row (each fed the previous round's ToolMessage) visits every
    # bound tool exactly once, in binding order, then finalizes — deterministic tool SELECTION.
    # Not guaranteed: valid ARGUMENTS for an arbitrary tool schema. The default
    # `{"query": <last human message>}` satisfies a tool whose schema happens to be `{query:
    # str}`, and any schema whose only REQUIRED field is a compatible `query` — optional fields
    # with defaults are filled in by the tool's own model — and nothing else: a required Decimal,
    # a nested object, or an enum field fails `tool.ainvoke(...)` with the same pydantic
    # ValidationError regardless of args or the cycle above. Generating a schema-valid instance
    # for an arbitrary
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
            # The summary names tools the same way the selection above does.
            # Reading `.name` directly printed `None: <result>` for the very ToolMessage shape the
            # selection had just learned to resolve — a summary that cannot say which tool produced
            # what is the thing a reader copies this mock to see.
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
