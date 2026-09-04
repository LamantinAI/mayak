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
- `CLAUDE.md`

## What mock mode does with tools (2026-08-13)

- Tools bound, no tool results yet → the mock emits one tool call for the first bound tool.
- Tool results present at the end of the message list → the mock emits a final answer summarizing
  them. That is the whole loop: request, tool call, result, answer, and it runs with no key and no
  network.
- Nothing bound → plain text, as before.

Until 2026-08-13 the first branch also required one of five English words in the prompt
(`template`, `capabilities`, `workflow`, `plan`, `reference`). A vertical whose prompts use any
other vocabulary therefore never exercised its own agent loop outside a live provider, which CI has
no key for. The keywords are gone; binding a tool is the signal.

The trade: the mock never declines to call a tool. A live model decides per prompt, the mock decides
at bind time, so "the model answered without reaching for a tool" cannot be tested here. Assert that
path against a fake port in the vertical's own test.

## What mock mode is not for (2026-09-04)

Every reply the mock gives is derived from the conversation: text when nothing is bound, a tool call
when a tool is, a summary once tool results arrive. That is what makes a loop runnable with no key,
and it is also the limit — a derived answer is a well-formed answer, and the failures that reach
production are the other kind. Valid JSON naming a value outside the enum, a missing field, a number
where a string belonged, one more tool call than the budget allows: none of those can be asked of a
model that answers by describing the request.

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
