# ADR-003: Mock-First LLM Mode

## Status

Accepted

## Decision

`AGENT_LLM_MODE=mock` is the default for local development, CI, and deterministic template verification.

## Rationale

Most template work is about wiring, orchestration, and contracts, not live-provider behavior. Mock mode keeps tests reproducible, cheap, and fast, which is especially important for agent-driven edit loops.

## Operationalization

- `project/core/config_settings_agent.py`
- `README.md`
- `AGENTS.md`

## What mock mode does with tools (2026-08-13, revised 2026-09-08)

- Tools bound, and this conversation has a bound tool that has not yet produced a `ToolMessage` →
  the mock emits a tool call for the first such tool, in binding order.
- Every bound tool has already produced a `ToolMessage` → the mock emits a final answer
  summarizing all of them, in the order they occurred.
- Nothing bound → plain text, as before.

That is a real loop over N tools, not one tool call: bind three tools, call the service, feed each
tool call's result back as a `ToolMessage`, call again — the mock visits all three, once each, in
binding order, then finalizes. `tests/application/test_mock_agent_multi_tool_loop.py` is that loop
run to completion, with structured arguments, no key, no network; copy its shape into a vertical.

A prompt-keyword gate on this branch previously excluded any vertical whose prompts did not use one
of a fixed set of English words, leaving its agent loop untested outside a live provider (CI has no
key for one). Binding a tool is the signal, not the prompt's wording.

Before this revision the mock could only ever call the *first* bound tool, once: any `ToolMessage`
at the tail of the conversation finalized the answer immediately, so a second or third bound tool
was dead weight — binding three tools and running the loop still visited exactly one. Two field builds
that each built a real agent on three or four tools hit this independently and both wrote their own
tool-selection layer on top of `LLMService` to route around it, which is exactly the layer this ADR
had claimed a vertical would not need. Measured before the fix, with three tools sharing one
compatible `{query: str}` schema so an argument mismatch could not also be the cause: round 1 called
the first tool, round 2 finalized with a text summary — the second and third tool were never
reached even though two more rounds were available on the loop budget. See the NOTE on
`_next_uncalled_tool_name` in `project/infrastructure/agents/llm_service_mock.py` for the fix and
the same measurement re-run after it.

The trade: the mock never declines to call a tool, and once it starts a cycle it will not stop
early or skip one — a live model decides per prompt which tool, if any, to reach for; the mock
decides the full sequence at bind time, positionally. "The model answered without reaching for a
tool", and "the model called only two of the three tools it was offered", both stay untestable
here — assert either against a fake port in the vertical's own test.

## What mock mode does not do with tool arguments (2026-09-08)

The default arguments sent with every tool call are still `{"query": <last human message>}`,
whichever tool is being called. That satisfies any schema whose only required field is a compatible
`query` — a `limit: int = 50` beside it is filled in by the tool's own model — and nothing
else: a required `Decimal`, a nested object, or an enum field fails the tool's own pydantic
validation the same way it always did, cycle or no cycle — this is not a defect the loop fix above
closes, because the mock has no way to know what value would be *valid*, only what value would be
*present*. Manufacturing a schema-valid instance for an arbitrary pydantic model is a small library
in its own right, and guessing a `Decimal` that also satisfies a domain invariant ("must be
positive") or an enum member that means what the test needs it to mean would mean dragging real
domain knowledge into a file that has none, for a guess still not guaranteed right — the kind of
complexity `scripts/validate_module_sizes.py`'s per-function budget exists to keep out of one place.

`_mock_tool_args: dict[str, dict[str, Any]]` is the escape hatch instead: an instance attribute,
empty by default, set after `bind_tools(...)` (`bound._mock_tool_args = {"charge_customer": {...},
...}`) to supply real, valid arguments per tool name. No change to `LLMService.bind_tools` was
needed for this — it is a plain attribute a test assigns, read only, never mutated, by the mixin.
This is the same shape of answer as `tests/support/scripted_llm.py`: the mock cannot know what only
the caller knows, so the caller writes it down.

## What mock mode is not for (2026-09-04, still true with `_mock_tool_args` set)

Every reply the mock gives is derived from the conversation: text when nothing is bound, a tool call
when a tool is, a summary once tool results arrive. That is what makes a loop runnable with no key,
and it is also the limit — a derived answer is a well-formed answer, and the failures that reach
production are the other kind. Valid JSON naming a value outside the enum, a missing field, a number
where a string belonged, one more tool call than the budget allows: none of those can be asked of a
model that answers by describing the request. `_mock_tool_args` only changes which valid arguments
go with a tool call the conversation state already decided would happen — it is a fixed lookup
table the caller writes ahead of time, not a second source of behavior, so it does not reopen the
"the model answered with something plausible and wrong" case above. That case is still
`tests/support/scripted_llm.py`'s alone.

So a test that needs one writes it down. `tests/support/scripted_llm.py` ships a `ScriptedLLMService`
that replays a fixed list of replies in order and raises when a call arrives past the end of the
script, which is how a turn budget is tested at all. It is a test double, not a second mode: nothing
in `project/` knows about it, and mock mode is unchanged. A vertical hands it to its own adapter the
way `PromptLLMAdapter` takes one — through a Protocol naming the one method, never the concrete
`LLMService` — and `tests/application/test_scripted_llm.py` shows the four answers worth pinning.

## Consequences

- Fast local feedback loops.
- Live-provider validation remains an explicit opt-in step.
- Functional tests can focus on integration paths instead of provider variance.
- An agentic vertical can prove its tool loop in CI, deterministically.
