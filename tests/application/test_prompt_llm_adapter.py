# FILE: tests/application/test_prompt_llm_adapter.py
# SUMMARY: Tests that PromptLLMAdapter really satisfies the domain's LLMPort contract.
#
# project/domain/ports.py named LLMService as the adapter for LLMPort, but their signatures never
# matched: the port speaks str, the service speaks langchain BaseMessage. Only a fake in a test
# implemented the port. These tests hold the real adapter to the contract the domain documents.

from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from project.domain.ports import LLMPort
from project.infrastructure.agents.prompt_llm_adapter import PromptLLMAdapter


# FUNCTION: _service_returning
# SUMMARY: Build an LLMService double whose call() returns the given message.
def _service_returning(message: AIMessage) -> Any:
    service = AsyncMock()
    service.call = AsyncMock(return_value=message)
    return service


# FUNCTION: test_adapter_satisfies_the_domain_port
# SUMMARY: The structural check the domain relies on — without it the Protocol is decoration.
@pytest.mark.unit
def test_adapter_satisfies_the_domain_port() -> None:
    adapter: LLMPort = PromptLLMAdapter(_service_returning(AIMessage(content="ok")))

    assert isinstance(adapter, PromptLLMAdapter)


# FUNCTION: test_prompt_is_wrapped_and_reply_is_returned_as_text
# SUMMARY: A plain string goes in as one human message and the reply comes back as a plain string.
@pytest.mark.unit
async def test_prompt_is_wrapped_and_reply_is_returned_as_text() -> None:
    service = _service_returning(AIMessage(content="four"))
    adapter = PromptLLMAdapter(service)

    result = await adapter.call("two plus two")

    assert result == "four"
    sent = service.call.await_args.args[0]
    assert len(sent) == 1
    assert isinstance(sent[0], HumanMessage)
    assert sent[0].content == "two plus two"


# FUNCTION: test_multimodal_reply_is_flattened_to_text
# SUMMARY: A list-shaped reply must not be stringified into a Python repr for the domain.
@pytest.mark.unit
async def test_multimodal_reply_is_flattened_to_text() -> None:
    reply = AIMessage(content=[{"type": "text", "text": "two"}, {"type": "text", "text": " plus"}])
    adapter = PromptLLMAdapter(_service_returning(reply))

    result = await adapter.call("how much")

    assert result == "two plus"


# CLASS: tests.application.test_prompt_llm_adapter.TestSystemInstructionTravelsInItsOwnRole
# SUMMARY: Verify a system instruction reaches the model as a SystemMessage, not as user text.
# NOTE: The adapter sent one HumanMessage and nothing else. The full-trace extractor splits its
# `system_prompt` / `user_message` fields by `isinstance(m, SystemMessage)`, so every vertical built
# on this adapter reported an empty system_prompt and filed the entire instruction under user text —
# measured against the mock service, where a prompt beginning "SYSTEM: ..." came back wholly as
# user_message while the same call made directly on LLMService with a SystemMessage split correctly.
class TestSystemInstructionTravelsInItsOwnRole:
    # FUNCTION: test_a_system_instruction_becomes_a_system_message
    # SUMMARY: Verify the two roles arrive as two messages, in order.
    @pytest.mark.unit
    async def test_a_system_instruction_becomes_a_system_message(self) -> None:
        service = _service_returning(AIMessage(content="ok"))
        adapter = PromptLLMAdapter(service)

        await adapter.call("What is 2+2?", system="You are terse.")

        sent = service.call.await_args.args[0]
        assert [type(message).__name__ for message in sent] == ["SystemMessage", "HumanMessage"]
        assert sent[0].content == "You are terse."
        assert sent[1].content == "What is 2+2?"

    # FUNCTION: test_omitting_the_instruction_sends_one_message_as_before
    # SUMMARY: Verify the parameter is additive — a caller that passes only a prompt is unchanged.
    @pytest.mark.unit
    async def test_omitting_the_instruction_sends_one_message_as_before(self) -> None:
        service = _service_returning(AIMessage(content="ok"))
        adapter = PromptLLMAdapter(service)

        await adapter.call("What is 2+2?")

        sent = service.call.await_args.args[0]
        assert [type(message).__name__ for message in sent] == ["HumanMessage"]

    # FUNCTION: test_the_full_trace_fields_split_by_role
    # SUMMARY: Verify the payload the trace records really separates instruction from question.
    @pytest.mark.unit
    def test_the_full_trace_fields_split_by_role(self) -> None:
        # **LOGIC_STEP**: Against the real extractor, not a restatement of it. This is the field
        # that was empty for every vertical, and the reason the port grew a parameter at all.
        from langchain_core.messages import SystemMessage

        from project.infrastructure.agents.llm_service_live import _build_full_trace_extras

        extras = _build_full_trace_extras(
            True,
            [SystemMessage(content="You are terse."), HumanMessage(content="What is 2+2?")],
            "4",
        )

        assert extras["system_prompt"] == "You are terse."
        assert extras["user_message"] == "What is 2+2?"
