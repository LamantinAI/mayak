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

The mock exercises the plumbing. Use `tests/support/scripted_llm.py` to test whether the
adapter handles a text reply without tool calls and sends tool results on the next turn.
Also exercise invalid tool arguments:

```sh
uv run pytest -q tests/application/test_mock_agent_multi_tool_loop.py
```

Set the live turn budget and verify provider behavior separately for the product that owns it.
