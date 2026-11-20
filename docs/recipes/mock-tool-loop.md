# Tool calls with a mock model

Run the executable example without a provider key:

```sh
uv run pytest -q tests/application/test_mock_agent_multi_tool_loop.py
```

Use that example for the conversation mechanics. Keep the bound copy returned by
`bind_tools`, append the model reply, run each requested tool with `run_tool` and append
one `ToolMessage` per call using its original ID. Put a bound on the number of turns.
`ToolArgs` supplies the Pydantic schema for the arguments. Keep the chosen tool commands
and provider message types inside the adapter.

The smoke mock walks bound tools one at a time. For a real batch in one assistant reply,
pass an `AIMessage` to `ScriptedLLMService` with multiple calls and explicit IDs:

```python
from langchain_core.messages import AIMessage

from tests.support.scripted_llm import ScriptedLLMService

model = ScriptedLLMService([
    AIMessage(
        content="",
        tool_calls=[
            {"name": "lookup_customer", "args": {"customer_id": "cust-42"}, "id": "lookup"},
            {"name": "price_order", "args": {"customer_id": "cust-42", "total": {"amount": "129.90", "currency": "USD"}}, "id": "price"},
        ],
    ),
    "order checked",
])
```

Inspect `model.received[1]` for one result per ID. The executable example also tests the turn
budget with a script that keeps requesting tools.

Define how the application combines tool results before trusting a batch. For example, only
merge a team and a playbook when both refer to the same ticket or returned record. Test a
mismatched result, invalid arguments and a model response with no calls separately. Return an
explicit timeout, partial result or error when the tool budget is exhausted, and record the
outcome in the trace. Select the behavior for the application's contract. Verify the live
provider separately before relying on it.
