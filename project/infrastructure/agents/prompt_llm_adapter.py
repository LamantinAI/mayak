# FILE: project/infrastructure/agents/prompt_llm_adapter.py
# SUMMARY: Adapter implementing the domain's LLMPort on top of the langchain-flavoured LLMService.

from __future__ import annotations

from typing import Optional, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage


# FUNCTION: _as_text
# SUMMARY: Flatten a langchain message body into plain text the domain can hold.
# NOTE: `content` is typed as str | list[str | dict] because multimodal replies arrive as a list of
# parts. Joining the text parts keeps the port's `-> str` promise honest instead of stringifying a
# Python list into the caller's face.
def _as_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append(part["text"])
    return "".join(parts)


# CLASS: project.infrastructure.agents.prompt_llm_adapter.SupportsMessageCall
# SUMMARY: The one method this adapter needs from a model: a message list in, a message out.
# NOTE: A Protocol rather than the concrete LLMService, so a test can hand this adapter a scripted
# double — see tests/support/scripted_llm.py — and prove how a vertical handles a reply that is
# plausible and wrong. Typed against the class, the only model a test could supply was the shipped
# mock, which computes its answer from the conversation and so cannot produce a wrong one.
# LLMService satisfies this structurally; nothing about it changes.
class SupportsMessageCall(Protocol):
    # FUNCTION: call
    # SUMMARY: Send the conversation and return the model's reply.
    async def call(self, messages: list[BaseMessage]) -> BaseMessage: ...


# CLASS: project.infrastructure.agents.prompt_llm_adapter.PromptLLMAdapter
# SUMMARY: Concrete LLMPort implementation: one text prompt in, the model's text out.
# NOTE: This exists because project/domain/ports.py named LLMService as its adapter while the two
# signatures did not match — the port takes and returns str, LLMService takes and returns
# BaseMessage. Nothing in the repository implemented the port, so the dependency-inversion boundary
# the domain documented was never actually crossed by production code, only by a fake in a test.
class PromptLLMAdapter:
    # FUNCTION: __init__
    # SUMMARY: Wrap an already-configured LLMService instance.
    def __init__(self, llm_service: SupportsMessageCall) -> None:
        self._llm_service = llm_service

    # FUNCTION: call
    # SUMMARY: Send a text prompt, optionally with a system instruction, and return the reply.
    # INPUT: system (Optional[str]): Instruction sent in the system role. Omitted when None.
    async def call(self, prompt: str, *, system: Optional[str] = None) -> str:
        # **LOGIC_STEP**: A SystemMessage when there is one, not a prefix glued onto the prompt.
        # The adapter used to send a lone HumanMessage, and the full-trace extractor splits its
        # fields by `isinstance(m, SystemMessage)` — so `system_prompt` was empty for every
        # vertical built on this adapter and the whole instruction was recorded as `user_message`.
        # Measured against the mock service: text beginning "SYSTEM: ..." landed in user_message
        # in full, while the same call made directly on LLMService with a SystemMessage split
        # correctly. The instrumentation was right; the port was the blocker.
        messages: list[BaseMessage] = []
        if system is not None:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=prompt))
        response = await self._llm_service.call(messages)
        return _as_text(response)
