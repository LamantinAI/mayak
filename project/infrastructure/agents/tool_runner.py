# FILE: project/infrastructure/agents/tool_runner.py
# SUMMARY: Runs one tool call of an agent loop inside an `agent.tool.<name>` span: which tool ran, with what, and how it ended.
# The kernel runs no agent loop — a vertical does — and the one it writes calls tools by hand. Two
# projects built on this template each wrapped those calls themselves to see them in the trace;
# this is that wrapper, once. The compact renderer shows an `agent.tool.` span's arguments inline
# (project/core/logging/trace_formatter.py), so what goes into them is decided here.

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import ValidationError

from project.core.logging import get_logger

logger = get_logger(__name__)


# Arguments a model produced that do not fit the tool's schema. Raised in place of pydantic's
# ValidationError, whose message quotes every input value — the user's text, written into an
# ERROR record with its traceback. This names each field and what was wrong with it: what a loop
# hands back to the model so it can try again.
class ToolArgumentsError(ValueError):
    pass


# An argument's type and size, never its value. Tool arguments are what a user typed or a model
# derived from it — a name, a message, a pasted page — and this span is written at INFO, into the
# log production keeps, not only the opt-in full-trace file.
def _described(value: Any) -> str:
    if isinstance(value, str):
        return f"str, {len(value)} chars"
    if isinstance(value, Mapping):
        return f"object, {len(value)} keys"
    if isinstance(value, (list, tuple, set)):
        return f"list, {len(value)} items"
    return type(value).__name__


# Call `tool` with `arguments` as a model asked for them, and return what it returned.
# shown: argument names safe to record verbatim — an id, an enum, a count. Every other argument is
#   recorded as its type and size.
# Raises ToolArgumentsError when the arguments do not fit the tool's schema, and whatever else the
#   tool raises as it is, after the span has recorded it.
async def run_tool(
    tool: BaseTool, arguments: Mapping[str, Any], *, shown: Collection[str] = ()
) -> Any:
    recorded = {
        name: value
        if name in shown and isinstance(value, (str, int, float, bool))
        else _described(value)
        for name, value in arguments.items()
    }
    with logger.span(f"agent.tool.{tool.name}", input_params=recorded, level=logging.INFO) as span:
        try:
            result = await tool.ainvoke(dict(arguments))
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(map(str, item['loc'])) or 'arguments'}: {item['msg']}"
                for item in error.errors(include_input=False, include_url=False)
            )
            raise ToolArgumentsError(f"{tool.name} — {problems}") from None
        span.output["result_chars"] = len(str(result))
        return result
