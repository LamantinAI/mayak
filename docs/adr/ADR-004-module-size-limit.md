# ADR-004: Production Module Size Limit

## Status

Accepted

## Decision

Production Python modules under `project/**` stay capped by `scripts/validate_module_sizes.py` to preserve navigability and edit safety.

## Rationale

Mayak is designed for LLM-first maintenance. Oversized modules degrade search, review quality, and localized edits, even when the code is technically correct.

## Operationalization

- `scripts/validate_module_sizes.py`
- `AGENTS.md`

## Consequences

- Refactors happen earlier instead of after hotspots become unmanageable.
- Generated or hand-written features stay within a context window that is practical for agents.
