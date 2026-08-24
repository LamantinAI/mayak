# Architecture Decision Records

Use this folder when a project rule feels stricter than usual and you need the reason before changing it.

Current ADRs:
- `ADR-001-pragmatic-cbm.md`
- `ADR-002-python-version-policy.md`
- `ADR-003-mock-first-llm-mode.md`
- `ADR-004-module-size-limit.md`
- `ADR-005-agent-workflow-lifecycle.md`
- `ADR-006-optional-postgres.md`
- `ADR-007-autocommit-and-explicit-transactions.md`

Guidelines:
- Add a short ADR when a durable architectural or workflow decision changes.
- Prefer concise rationale over long narratives.
- Link to the affected files or scripts when the rule is operationalized in code.

When an ADR is the right place — one of three, and only one holds any given fact
(`CLAUDE.md`, "Where a fact goes — one fact, one place"):

| The fact is needed | It goes |
|---|---|
| while editing one file | a comment in that file |
| while editing any of several files under one convention | **here** |
| while somewhere else entirely, or tied to no code | the agent's external memory |

An ADR is for the decision no single file owns: CBM strictness governs every module, the size budget
governs every module, the Python floor governs the whole toolchain. Write it once here and point at
it from the code, rather than repeating the reasoning in each governed file. Do not restate an ADR's
content in a memory node, or a memory node's content here — two copies of one fact drift, and nothing
in this repository can notice.
