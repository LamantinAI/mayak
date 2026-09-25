# ADR-001: Code-Base Markup — only the file header is left

## Status

Accepted. Amended 2026-09-24: the tags on classes, functions and attributes were removed, and the
validator now checks only the file header. The strict-core rules of 2026-08-04 are superseded.

## Decision

Every Python file under `project/` opens with its header:

```python
# FILE: project/domain/reference_task.py
# SUMMARY: What this file is for — one sentence, continued over more lines if it needs them.
```

Below the header there is no markup. A class, function or attribute carries no tag. A comment says
what the code cannot: why it is written this way, the case it guards, the measurement or incident
behind it, the ADR it follows. A comment that restates a name, a signature or an annotation is not
written — `# Load one task by identifier.` above `async def get_task(self, task_id: str)` is noise
an agent pays for on every read and copies into every new file.

## Rationale

bench2 (2026-09) measured the price and found no measured benefit:

- Tags were 19–24% of the non-empty lines of new code under `project/` in the first vertical — the
  stage that carried 62–64% of the template's extra cost — and agents copy the example vertical's
  proportions into their own.
- The CBM gate blocked one commit in the whole measurement; the navigation map builds its symbols
  from the AST and never read a tag.
- Before the removal, 1 545 of the 7 305 non-empty lines under `project/` were tag lines
  (`grep -E '^\s*# ([A-Z_]{3,}:|\*\*LOGIC_STEP)'` over `project/` at commit e47eeeb).

The header stays, for now, because it is the one piece with a reader: `scripts/structure_builder.py`
puts the SUMMARY into `docs/project_map.md`, and an agent that opens a file sees what it is for
before the code. Whether that earns two lines per file is measured by bench3, which compares the
template with and without the header; if the variant without it is no worse at navigation and no
more expensive, the header goes too.

## How the removal was done

To reproduce or extend it: a codemod over whole-line comments only (found with `tokenize`, so
tag-like text inside strings was untouched) deleted the label lines and turned `NOTE` and
`LOGIC_STEP` into plain comments; every `SUMMARY`, `INPUT`, `OUTPUT` and `RAISES` left on a symbol
in `project/`, `tests/` and `alembic/` was judged one by one — deleted when it restated the code,
kept as a plain comment when it said something the code does not (about 1 290 went, 660 stayed).
Token streams without comments were compared before and after for every changed file. Lines of
Python: `project/` 8 281 → 7 331, `tests/` 21 186 → 18 735, the repository 43 895 → 39 389.

## Operationalization

- `scripts/validate_cbm.py` — the header check, run by `make quality-gates`
- `tests/template/test_validate_cbm.py`
