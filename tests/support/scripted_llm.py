# FILE: tests/support/scripted_llm.py
# SUMMARY: A language model that answers from a script, so a vertical's own handling of a bad
# answer can be tested without a provider.
#
# The shipped mock service answers deterministically, but it computes its answer from the shape of
# the conversation: text when nothing is bound, a tool call when a tool is, a summary once tool
# results arrive. That is what makes an agent loop runnable in CI without a key, and it is exactly
# what cannot express the failure that matters — a model that answers with something plausible and
# wrong. Valid JSON with a value outside the enum, a missing field, a number where a string
# belonged, a second tool call when the loop expected an answer: none of those are reachable by
# describing the conversation, because they are properties of the reply, not of the request.
#
# So a test that wants them writes them down. Each entry of the script is one reply, returned in
# order; the double never parses what it returns, which is the point — the vertical's own parser
# is the thing under test.

from __future__ import annotations

from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage


# CLASS: tests.support.scripted_llm.ScriptedToolCall
# SUMMARY: A scripted reply that is a tool call rather than text.
# NOTE: A dedicated type rather than a dict, because a dict reply is ambiguous: it reads equally as
# "the model asked for a tool" and "the model answered with the text of a dict". The double would
# have to guess, and a test double that guesses is a test that lies.
class ScriptedToolCall:
    # FUNCTION: __init__
    # SUMMARY: Record the tool the model asks for, and the arguments it passes.
    # INPUT: call_id (str | None): Tool-call id; a positional default is generated when None.
    def __init__(self, name: str, args: dict[str, Any], call_id: str | None = None) -> None:
        self.name = name
        self.args = args
        self.call_id = call_id


# CLASS: tests.support.scripted_llm.ScriptedLLMExhausted
# EXTENDS: RuntimeError
# SUMMARY: Raised when the model is called more times than the script has replies.
# NOTE: Loud on purpose. A loop with an off-by-one in its turn budget calls the model once more
# than the test expected, and the useful outcome there is a failed test naming the extra call —
# not a default answer that lets the loop finish and the assertion pass.
class ScriptedLLMExhausted(RuntimeError):
    pass


# FUNCTION: _as_message
# SUMMARY: Turn one scripted reply into the message shape a caller reads back.
# INPUT: position (int): 1-based call number, used only for a default tool-call id.
def _as_message(reply: str | ScriptedToolCall, position: int) -> BaseMessage:
    if isinstance(reply, ScriptedToolCall):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": reply.name,
                    "args": reply.args,
                    "id": reply.call_id or f"scripted-tool-{position}",
                    "type": "tool_call",
                }
            ],
        )
    return AIMessage(content=reply)


# CLASS: tests.support.scripted_llm.ScriptedLLMService
# SUMMARY: Answers `call` from a fixed list of replies and records what it was asked.
# NOTE: Deliberately not a subclass of LLMService. Inheriting would drag in the provider client,
# the readiness probe and the settings the double exists to do without; matching the two methods a
# caller uses is enough for anything that takes its model as a Protocol — which is what
# `PromptLLMAdapter` and any vertical adapter should ask for.
class ScriptedLLMService:
    # FUNCTION: __init__
    # SUMMARY: Load the script. Nothing is read from it until the first call.
    # INPUT: script (Sequence[str | ScriptedToolCall]): Replies, one per call, in order.
    def __init__(self, script: Sequence[str | ScriptedToolCall]) -> None:
        self._script: list[str | ScriptedToolCall] = list(script)
        self._position = 0

        # ATTRIBUTE: received (list[list[BaseMessage]])
        # SUMMARY: Every message list this was called with, in order — so a test can assert what
        # the loop actually sent (that a tool result reached the next turn, say) without wrapping
        # a second spy around the double.
        self.received: list[list[BaseMessage]] = []

    # FUNCTION: call_count
    # SUMMARY: How many replies have been used.
    @property
    def call_count(self) -> int:
        return self._position

    # FUNCTION: call
    # SUMMARY: Return the next scripted reply and record the conversation it answered.
    # RAISES: ScriptedLLMExhausted: When the script has no reply left for this call.
    async def call(self, messages: list[BaseMessage]) -> BaseMessage:
        self.received.append(list(messages))
        if self._position >= len(self._script):
            raise ScriptedLLMExhausted(
                f"call #{self._position + 1} arrived, but only {len(self._script)} "
                "replies were scripted"
            )
        reply = self._script[self._position]
        self._position += 1
        return _as_message(reply, self._position)

    # FUNCTION: bind_tools
    # SUMMARY: Return this same double, so `model.bind_tools(...).call(...)` runs unchanged.
    # NOTE: The script already fixes every reply, so there is no per-binding state to copy the way
    # the real service copies its tool list. What the model was offered is not what decides the
    # answer here — the test is.
    def bind_tools(self, tools: list[Any]) -> "ScriptedLLMService":
        del tools
        return self
