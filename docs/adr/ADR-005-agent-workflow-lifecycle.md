# ADR-005: Agent Workflow Lifecycle

## Status

Accepted

## Decision

Every agent session follows one canonical order of operations rather than improvising it: read the
skill for the task, edit, validate narrow-then-wide, and recover through the doctor when a gate
fails — `make doctor` for the blocking layer, `doctor_ai_context.py --rule <rule_id>` for one rule's
playbook.

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
- `scripts/doctor_ai_context.py` — recovery diagnostics, and the playbook for any `rule_id`.

## Consequences

- Agents follow a predictable, repeatable order across sessions.
- Recovery from a broken state is documented rather than improvised.
- The procedure has one home. Changing it means editing `docs/agent_rules.md`, and no second copy can
  quietly disagree.

## 2026-09: the query CLI and its maps removed

The lifecycle used to start from `scripts/query_ai_context.py` (`bootstrap`, `workset`,
`before-edit`, `symbol`, `failure`) over three generated maps and a hand-kept file policy. bench2
measured it: across twelve stages one arm called the CLI 6 times and the other 0, and the arm that
never called it scored higher on the navigation questions; the maps added 303–370 generated lines to
late commits, and agents spent turns repairing the map's own heuristics. What does reach an agent is
a skill read for the task and the text of a failing gate, so those carry the procedure now, and the
wiring checks the maps used to make live in `scripts/validate_endpoint_wiring.py`. Reproduce the
call counts by grepping the bench2 transcripts for `query_ai_context.py` invocations that are not
pytest runs. Whether navigation suffers without the CLI is bench3's question, asked before it runs.
