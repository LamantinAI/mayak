# FILE: tests/application/test_scripted_llm.py
# SUMMARY: The scripted model double itself, and the one thing it exists for — proving a vertical
# rejects an answer that is well-formed and wrong.
#
# The shipped mock service is enough to run an agent loop without a key, and not enough to test
# one: it derives its reply from the conversation, so every answer it can give is a well-behaved
# one. The failures that reach production are the other kind. This file pins the double's own
# behaviour and then shows the shape a vertical copies.

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from project.domain.exceptions import ExternalServiceError
from project.infrastructure.agents.prompt_llm_adapter import PromptLLMAdapter
from tests.support.scripted_llm import (
    ScriptedLLMExhausted,
    ScriptedLLMService,
    ScriptedToolCall,
)

# ATTRIBUTE: _ALLOWED_PRIORITIES (frozenset[str])
# SUMMARY: The closed set a made-up vertical's verdicts are checked against.
_ALLOWED_PRIORITIES = frozenset({"low", "normal", "high"})


# FUNCTION: _parse_verdict
# SUMMARY: Stand in for a vertical's own parser of a model answer, in miniature.
# INPUT: raw (str): Whatever text the model produced.
# OUTPUT: (str): The priority the model chose, once it is known to be one.
# RAISES: ExternalServiceError: For every shape of answer that is not a known priority.
# NOTE: Written here, in the test, because the kernel ships no vertical that reads a model's
# answer — the point is the shape, and a vertical copies it into its own adapter. Two details are
# load-bearing and both come from a field build: `isinstance` runs BEFORE the membership test,
# because `["high"] in <frozenset>` raises TypeError rather than answering False, and every
# rejection is one domain error, so the endpoint above it answers 502 instead of 500.
def _parse_verdict(raw: str) -> str:
    try:
        answer = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ExternalServiceError(f"model answer was not JSON: {error}") from error
    if not isinstance(answer, dict):
        raise ExternalServiceError("model answer was JSON but not an object")
    priority = answer.get("priority")
    if not isinstance(priority, str) or priority not in _ALLOWED_PRIORITIES:
        raise ExternalServiceError(f"model proposed an unusable priority: {priority!r}")
    return priority


# CLASS: tests.application.test_scripted_llm.TestTheDouble
# SUMMARY: What the double promises: replies in order, a loud end, tool calls, a record of asks.
class TestTheDouble:
    # FUNCTION: test_replies_arrive_in_the_order_they_were_written
    # SUMMARY: Verify each call consumes the next scripted reply rather than repeating one.
    @pytest.mark.unit
    async def test_replies_arrive_in_the_order_they_were_written(self) -> None:
        model = ScriptedLLMService(["first", "second"])

        first = await model.call([HumanMessage(content="hi")])
        second = await model.call([HumanMessage(content="hi again")])

        assert (first.content, second.content) == ("first", "second")
        assert model.call_count == 2

    # FUNCTION: test_a_call_past_the_end_of_the_script_is_an_error
    # SUMMARY: Verify an unscripted extra call fails the test instead of getting a default answer.
    @pytest.mark.unit
    async def test_a_call_past_the_end_of_the_script_is_an_error(self) -> None:
        model = ScriptedLLMService(["only one"])
        await model.call([HumanMessage(content="hi")])

        # **LOGIC_STEP**: This is what makes the double usable for a turn budget: a loop that
        # calls the model once more than its script allows says so, loudly, at the extra call.
        with pytest.raises(ScriptedLLMExhausted, match="call #2"):
            await model.call([HumanMessage(content="again")])

    # FUNCTION: test_a_scripted_tool_call_arrives_as_a_tool_call
    # SUMMARY: Verify a scripted tool call reaches the caller as a message carrying tool_calls.
    @pytest.mark.unit
    async def test_a_scripted_tool_call_arrives_as_a_tool_call(self) -> None:
        model = ScriptedLLMService(
            [ScriptedToolCall("search_articles", {"query": "printer"}), '{"priority": "high"}']
        )

        first = await model.call([HumanMessage(content="printer jammed")])
        second = await model.call([HumanMessage(content="printer jammed")])

        assert isinstance(first, AIMessage)
        assert first.tool_calls[0]["name"] == "search_articles"
        assert first.tool_calls[0]["args"] == {"query": "printer"}
        assert first.tool_calls[0]["id"] == "scripted-tool-1"
        assert second.content == '{"priority": "high"}'

    # FUNCTION: test_binding_tools_changes_nothing_about_the_script
    # SUMMARY: Verify `model.bind_tools(...).call(...)` runs and still answers from the script.
    @pytest.mark.unit
    async def test_binding_tools_changes_nothing_about_the_script(self) -> None:
        model = ScriptedLLMService(["answer"])

        reply = await model.bind_tools([object()]).call([HumanMessage(content="hi")])

        assert reply.content == "answer"

    # FUNCTION: test_the_conversation_each_call_saw_is_kept
    # SUMMARY: Verify a test can read back what the code under test actually sent.
    @pytest.mark.unit
    async def test_the_conversation_each_call_saw_is_kept(self) -> None:
        model = ScriptedLLMService(["a", "b"])

        await model.call([HumanMessage(content="one")])
        await model.call([HumanMessage(content="one"), HumanMessage(content="two")])

        assert [len(sent) for sent in model.received] == [1, 2]
        assert model.received[1][1].content == "two"


# CLASS: tests.application.test_scripted_llm.TestAWrongAnswerIsRejected
# SUMMARY: The reason the double exists: answers the mock service cannot produce.
# NOTE: Each case below is a real failure a language model produces and a deterministic mock
# cannot: the mock's reply is derived from the conversation, so it is always well-formed. A field
# build shipped a parser whose only test ran against that mock, which meant exactly one of these
# cases was covered — the one where the answer is not JSON at all.
class TestAWrongAnswerIsRejected:
    # FUNCTION: test_a_good_answer_is_accepted
    # SUMMARY: Verify the parser under test is not simply rejecting everything.
    @pytest.mark.unit
    async def test_a_good_answer_is_accepted(self) -> None:
        model = ScriptedLLMService(['{"priority": "high"}'])
        adapter = PromptLLMAdapter(model)

        assert _parse_verdict(await adapter.call("triage this")) == "high"

    # FUNCTION: test_text_that_is_not_json_is_rejected
    # SUMMARY: Verify prose where JSON was asked for becomes a domain error.
    @pytest.mark.unit
    async def test_text_that_is_not_json_is_rejected(self) -> None:
        model = ScriptedLLMService(["Sure! Here is the triage you asked for."])
        adapter = PromptLLMAdapter(model)

        with pytest.raises(ExternalServiceError, match="not JSON"):
            _parse_verdict(await adapter.call("triage this"))

    # FUNCTION: test_valid_json_with_a_value_outside_the_set_is_rejected
    # SUMMARY: Verify a well-formed answer with an invented value is still refused.
    @pytest.mark.unit
    async def test_valid_json_with_a_value_outside_the_set_is_rejected(self) -> None:
        model = ScriptedLLMService(['{"priority": "critical"}'])
        adapter = PromptLLMAdapter(model)

        with pytest.raises(ExternalServiceError, match="unusable priority"):
            _parse_verdict(await adapter.call("triage this"))

    # FUNCTION: test_a_list_where_a_string_was_expected_is_rejected
    # SUMMARY: Verify an unhashable value fails as a domain error, not as a TypeError.
    # NOTE: This is the case that separates `isinstance(value, str) and value in <frozenset>` from
    # the bare membership test. `["high"] in frozenset()` raises `TypeError: unhashable type`,
    # which is not a ProjectError, so it reaches the client as a 500 — from the one line written
    # to make sure it could not. Measured in a field build on 2026-09-03, where a triage endpoint
    # answered 500 for exactly this input. Assert the domain type, never bare `Exception`: an
    # assertion on `Exception` passes on both the fix and the defect.
    @pytest.mark.unit
    async def test_a_list_where_a_string_was_expected_is_rejected(self) -> None:
        model = ScriptedLLMService(['{"priority": ["high"]}'])
        adapter = PromptLLMAdapter(model)

        with pytest.raises(ExternalServiceError, match="unusable priority"):
            _parse_verdict(await adapter.call("triage this"))
