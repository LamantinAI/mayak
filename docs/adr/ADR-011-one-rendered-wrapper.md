# ADR-011: One rendered contract, imported under the other agent's name

Date: 2026-09-09

## Context

Two agents look for two different files. Codex reads `AGENTS.md`; Claude Code reads `CLAUDE.md` and,
measured on CLI 2.1.219, reads nothing when only `AGENTS.md` is present. Its documentation is
explicit — "Claude Code reads `CLAUDE.md`, not `AGENTS.md`" — and there is no setting that changes
it.

The template answered that by rendering `docs/agent_rules.md` twice, once under each name. The two
files were byte-identical once the file name and the reader's name were folded away, and a test
existed to keep them that way. Twelve hundred words stored twice, with a gate to prove the copies
agree, is exactly what this repository tells every project not to do.

Claude Code resolves `@path` references in `CLAUDE.md` at session start and expands the file in
place. So the second name can be an import rather than a copy.

## Decision

`AGENTS.md` is the one rendered contract. `CLAUDE.md` is generated too, but carries only a block
comment and the bare line `@AGENTS.md`.

An import, not a symlink. Git on Windows without Developer Mode checks a symlink out as a text file
holding the target's name, and the rules would then reach no one — the same trap that already
applies to the `.claude/skills` links.

Claude Code strips block HTML comments before the file reaches the model, so the comment explaining
all this costs an agent nothing and is still there for whoever opens the file.

## Consequences

Startup context is unchanged: the import expands to the same words the copy used to hold. What
changes is that there is one place to edit and no second copy to keep equal.

`CLAUDE.md` must stay empty of rules. A rule written below the import would reach Claude Code and
never reach Codex — the drift the deleted equality test used to catch. `test_sync_agent_docs.py`
now pins that instead: strip the comment, remove the import, and nothing may remain.

The import must stay a bare line. Inside backticks or a fenced block it is prose, not an import, and
every Claude session then starts with no rules while every gate stays green. That is pinned too.

Every pointer that used to send an agent to `CLAUDE.md` for the contract now names `AGENTS.md` —
playbook `read_first` lists, `core_entrypoints`, `READ_LAST_PATHS`, both skills' `minimal_read_set`,
and the ADRs that quote the contract. `CLAUDE.md` keeps its own name only where the subject is the
file itself: the generated-path list, the pre-edit hook, and the two answers about what one edit
regenerates.

## Verification

Reproduce the measurement rather than trusting this paragraph: put a marker rule in an `AGENTS.md`
in an empty directory, add a `CLAUDE.md` holding `@AGENTS.md`, and ask
`claude -p --disallowed-tools Read Glob Grep Bash` for the marker. With the import the marker comes
back; with `AGENTS.md` alone the answer is that no such instruction is loaded.
