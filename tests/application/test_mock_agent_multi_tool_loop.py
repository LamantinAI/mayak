# FILE: tests/application/test_mock_agent_multi_tool_loop.py
# SUMMARY: The sample ADR-003 promises and the kernel did not ship: a real agent loop over three
# bound tools with structured arguments (Decimal, a nested object, an enum), driven end to end by
# LLMService in mock mode, with no key and no network. Copy this file's shape into a vertical.
#
# Before 2026-09-08 this test could not be written against the shipped mock at all. Two field
# builds hit the same wall independently and both wrote their own tool-selection layer on top of
# `LLMService` to get here — see the NOTE on `_next_uncalled_tool_name` in
# project/infrastructure/agents/llm_service_mock.py for what was actually broken and how it was
# measured, and docs/adr/ADR-003-mock-first-llm-mode.md for what mock mode now guarantees.

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from project.infrastructure.agents.llm_service import LLMService
from tests.conftest import _FixtureSettings as FixtureSettings


# CLASS: tests.application.test_mock_agent_multi_tool_loop.NotificationChannel
# SUMMARY: The enum leg of the structured-argument requirement.
class NotificationChannel(str, Enum):
    EMAIL = "email"
    SMS = "sms"


# CLASS: tests.application.test_mock_agent_multi_tool_loop.Money
# SUMMARY: The nested-object-with-a-Decimal-field leg: a tool argument that is itself a model,
# holding the Decimal a `{"query": str}` shape can never satisfy.
class Money(BaseModel):
    amount: Decimal
    currency: str


# CLASS: tests.application.test_mock_agent_multi_tool_loop.LookupArgs
# SUMMARY: Schema for the first tool in the loop: plain scalar arguments.
class LookupArgs(BaseModel):
    customer_id: str


# CLASS: tests.application.test_mock_agent_multi_tool_loop.PriceOrderArgs
# SUMMARY: Schema for the second tool: a nested object carrying a Decimal.
class PriceOrderArgs(BaseModel):
    customer_id: str
    total: Money


# CLASS: tests.application.test_mock_agent_multi_tool_loop.SendConfirmationArgs
# SUMMARY: Schema for the third tool: an enum-constrained field.
class SendConfirmationArgs(BaseModel):
    customer_id: str
    channel: NotificationChannel


# FUNCTION: _lookup_customer / _price_order / _send_confirmation
# SUMMARY: Trivial bodies. What this test exercises is argument validation and loop control, not
# business logic — a real vertical's tool bodies belong in its own infrastructure layer.
def _lookup_customer(customer_id: str) -> str:
    return f"customer {customer_id}: active"


def _price_order(customer_id: str, total: Money) -> str:
    return f"order for {customer_id} priced at {total.amount} {total.currency}"


def _send_confirmation(customer_id: str, channel: NotificationChannel) -> str:
    return f"confirmation sent to {customer_id} via {channel.value}"


# ATTRIBUTE: _TOOLS (list[StructuredTool])
# SUMMARY: Three tools with three different schemas, bound in this order — the order the mock is
# expected to visit them in, since selection is positional (see ADR-003).
_TOOLS = [
    StructuredTool.from_function(
        func=_lookup_customer,
        name="lookup_customer",
        description="Look up a customer by id.",
        args_schema=LookupArgs,
    ),
    StructuredTool.from_function(
        func=_price_order,
        name="price_order",
        description="Price an order for a customer.",
        args_schema=PriceOrderArgs,
    ),
    StructuredTool.from_function(
        func=_send_confirmation,
        name="send_confirmation",
        description="Send an order confirmation to a customer.",
        args_schema=SendConfirmationArgs,
    ),
]

# ATTRIBUTE: _VALID_ARGS_BY_TOOL (dict[str, dict[str, Any]])
# SUMMARY: The one thing the mock cannot invent on its own — see the NOTE on `_build_mock_response`
# in llm_service_mock.py. Written down here, the same way a vertical would write it down for its
# own tools, and handed to the bound service through `_mock_tool_args`.
_VALID_ARGS_BY_TOOL: dict[str, dict[str, Any]] = {
    "lookup_customer": {"customer_id": "cust-42"},
    "price_order": {"customer_id": "cust-42", "total": {"amount": "129.90", "currency": "USD"}},
    "send_confirmation": {"customer_id": "cust-42", "channel": "email"},
}


# FUNCTION: _run_agent_loop
# SUMMARY: The loop shape a vertical's own agent service runs: call the model, execute whatever
# tool it asked for against the REAL tool (so args validate against its REAL schema), feed the
# result back, repeat until the model answers with no further tool call.
# INPUT: service (LLMService): Already bound to `_TOOLS`, in mock mode.
# OUTPUT: (tuple[list[str], str]): Tool names called, in order, and the final text answer.
# RAISES: AssertionError: If the loop does not terminate within one round per bound tool plus one —
# a runaway loop should fail the test loudly, not hang it or loop silently past the budget.
async def _run_agent_loop(service: LLMService, prompt: str) -> tuple[list[str], str]:
    messages: list[BaseMessage] = [HumanMessage(content=prompt)]
    tools_by_name = {tool.name: tool for tool in _TOOLS}
    called_in_order: list[str] = []

    for _round in range(len(_TOOLS) + 1):
        response = await service.call(messages)
        messages.append(response)
        if not isinstance(response, AIMessage) or not response.tool_calls:
            assert isinstance(response.content, str)
            return called_in_order, response.content

        call = response.tool_calls[0]
        called_in_order.append(call["name"])
        # **LOGIC_STEP**: `ainvoke` runs the tool's own pydantic validation on `call["args"]` —
        # this is what proves the args are schema-valid, not merely well-typed Python.
        result = await tools_by_name[call["name"]].ainvoke(call["args"])
        messages.append(
            ToolMessage(content=str(result), name=call["name"], tool_call_id=call["id"])
        )

    raise AssertionError(f"loop did not finalize within {len(_TOOLS) + 1} rounds")


# CLASS: tests.application.test_mock_agent_multi_tool_loop.TestThreeToolAgentLoopOnMock
# SUMMARY: The sample itself.
class TestThreeToolAgentLoopOnMock:
    # FUNCTION: test_the_loop_visits_every_tool_once_with_valid_structured_args_then_answers
    # SUMMARY: Drive the full loop and check every part of the promise: selection, argument
    # validity across all three structured shapes, and termination with a real final answer.
    @pytest.mark.unit
    async def test_the_loop_visits_every_tool_once_with_valid_structured_args_then_answers(
        self,
    ) -> None:
        with patch(
            "project.infrastructure.agents.llm_service.get_settings",
            return_value=FixtureSettings(),
        ):
            service = LLMService()
        bound = service.bind_tools(_TOOLS)
        bound._mock_tool_args = _VALID_ARGS_BY_TOOL

        called_in_order, final_answer = await _run_agent_loop(
            bound, "place and confirm an order for cust-42"
        )

        # **LOGIC_STEP**: Selection is positional and complete — every bound tool exactly once,
        # in binding order. This is the defect that made a 3-tool loop unreachable on the shipped
        # mock: before the fix this list stopped at one entry no matter how many tools were bound.
        assert called_in_order == ["lookup_customer", "price_order", "send_confirmation"]
        # **LOGIC_STEP**: The final call carried no tool call and produced the summary branch —
        # the loop actually ended instead of hitting the round budget in `_run_agent_loop`.
        assert "Tool-assisted summary" in final_answer
        for tool_name in called_in_order:
            assert tool_name in final_answer

    # FUNCTION: test_without_the_override_the_default_args_still_only_fit_a_query_shaped_tool
    # SUMMARY: Pin the boundary from the other side: no `_mock_tool_args` override means the old
    # `{"query": ...}` default, which is exactly why the override exists.
    @pytest.mark.unit
    async def test_without_the_override_the_default_args_still_only_fit_a_query_shaped_tool(
        self,
    ) -> None:
        with patch(
            "project.infrastructure.agents.llm_service.get_settings",
            return_value=FixtureSettings(),
        ):
            service = LLMService()
        bound = service.bind_tools(_TOOLS)

        response = await bound.call([HumanMessage(content="place an order")])

        assert isinstance(response, AIMessage)
        assert response.tool_calls[0]["name"] == "lookup_customer"
        # **LOGIC_STEP**: `{"query": ...}` is not `LookupArgs`'s shape (`customer_id`), so the
        # default args fail this tool's real schema — the exact failure the override in the test
        # above exists to avoid. Asserted here as the documented boundary, not just described.
        with pytest.raises(Exception, match="customer_id"):
            await _TOOLS[0].ainvoke(response.tool_calls[0]["args"])

    # FUNCTION: test_the_loop_advances_when_the_tool_result_carries_no_name
    # SUMMARY: Drive the same loop with `ToolMessage(content=..., tool_call_id=...)` — no `name` —
    # and check the cycle still visits every tool once.
    # NOTE: `ToolMessage.name` is optional, and the shortest hand-written loop omits it. Selection
    # read only that field until 2026-09-08, so an unnamed result taught the mock nothing: it
    # answered with the first bound tool again, and again, until the caller's own round budget
    # stopped it. Found by an independent review of this branch, not by the loop above, because
    # the loop above happens to set the name.
    @pytest.mark.unit
    async def test_the_loop_advances_when_the_tool_result_carries_no_name(self) -> None:
        with patch(
            "project.infrastructure.agents.llm_service.get_settings",
            return_value=FixtureSettings(),
        ):
            service = LLMService()
        bound = service.bind_tools(_TOOLS)
        bound._mock_tool_args = _VALID_ARGS_BY_TOOL

        messages: list[BaseMessage] = [HumanMessage(content="confirm the order for cust-42")]
        tools_by_name = {tool.name: tool for tool in _TOOLS}
        called_in_order: list[str] = []

        for _round in range(len(_TOOLS) + 1):
            response = await bound.call(messages)
            messages.append(response)
            if not isinstance(response, AIMessage) or not response.tool_calls:
                break
            call = response.tool_calls[0]
            called_in_order.append(call["name"])
            result = await tools_by_name[call["name"]].ainvoke(call["args"])
            # **LOGIC_STEP**: The whole point — no `name=`, only the id of the call it answers.
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

        assert called_in_order == ["lookup_customer", "price_order", "send_confirmation"]
