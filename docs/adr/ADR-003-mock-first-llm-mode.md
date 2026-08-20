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

## Consequences

- Fast local feedback loops.
- Live-provider validation remains an explicit opt-in step.
- Functional tests can focus on integration paths instead of provider variance.
- An agentic vertical can prove its tool loop in CI, deterministically.
