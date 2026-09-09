# ADR-005: Agent Workflow Lifecycle

## Status

Accepted

## Decision

Every agent session follows one canonical order of operations rather than improvising it: cold start
from the query CLI, plan with the cheapest query that answers the question, check file policy before
editing anything outside the `safe` zone, edit, validate narrow-then-wide, and recover through
`make doctor` when a gate fails.

The steps themselves are written once, in `docs/agent_rules.md` and the `AGENTS.md` it generates,
and the commands come from the `Makefile`. This ADR deliberately does not restate either.

## Rationale

Mayak is worked on entirely by LLM agents. Without a documented order, agents skip
pre-edit checks, forget to regenerate artifacts, or run broad scans where a focused query would do.
A canonical lifecycle removes that variance.

Restating the lifecycle here is what this ADR used to do, and it is exactly how the rule broke: this
file spelled out all six phases with their commands, the operational contract spelled out the same steps,
and nothing compared them. When the canonical final command became `make quality-gates`,
`AGENTS.md` and `docs/agent_rules.md` were updated together and this document was
not — so an agent that read the ADR ran the wrong command and never learned it was wrong. An ADR
records *why a decision was made*; the procedure belongs where the agent already looks.

## Operationalization

- `AGENTS.md` — Task process and Working notes: the steps, in order.
- `make help` — the commands, rendered from the Makefile's own annotations.
- `docs/agent_rules.md` — Working Notes, which generate `AGENTS.md`.
- `scripts/query_ai_context.py` — every query used in the lifecycle, registered in
  `ai_context/constants.py:QUERY_SUPPORTED_COMMANDS`.
- `scripts/doctor_ai_context.py` — recovery diagnostics.

## Consequences

- Agents follow a predictable, repeatable order across sessions.
- Recovery from a broken state is documented rather than improvised.
- The procedure has one home. Changing it means editing `docs/agent_rules.md`, and no second copy can
  quietly disagree.
