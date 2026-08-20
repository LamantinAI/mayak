# ADR-001: Pragmatic CBM Strictness

## Status

Accepted

## Decision

Mayak keeps strict CBM for file headers, classes, public functions, and `__init__`, while treating `# ATTRIBUTE:` blocks and private helpers as optional detail. `# **LOGIC_STEP**:` annotations are reserved for branching logic, side effects, security nuances, lifecycle nuances, wiring nuances, and non-obvious invariants.

`# INPUT:` and `# OUTPUT:` are written only when the line states something the signature cannot: a condition, a default, a side effect, or a relationship to another symbol. A gloss that restates the parameter name and its annotation is removed, not kept "for completeness".

## Rationale

The template is optimized for LLM editing. Strict-core CBM preserves discoverability and reviewability where it matters most, but forcing metadata onto every trivial attribute and helper inflates edit cost and creates avoidable drift.

## Operationalization

- `scripts/validate_cbm.py`
- `docs/agent_rules.md`

## Consequences

- Public API surfaces stay easy to scan.
- Small refactors generate less annotation churn.
- Optional-detail CBM is still valid and should stay accurate when present.
- `# **LOGIC_STEP**:` stops being ceremonial narration for obvious code and stays focused on semantic decision points.
- Parameter documentation stops competing with the type annotation. Measured on 2026-08-04 across `project/`, `scripts/`, `ai_context/`, and `ai_query/`: those four directories held 889 `# INPUT:` / `# OUTPUT:` lines, and 307 remain. Everything removed said only what the signature already said; everything kept carries a condition, a default, a side effect, a unit, or the shape of a returned structure (`keyed by`, `sorted by`, `1-based`). `tests/` holds a further 73 lines that were never reviewed — the pass did not cover it. No validator enforces these two tags in either direction, so the rule lives here, not in a gate.
