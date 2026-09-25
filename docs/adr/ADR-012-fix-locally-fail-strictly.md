# ADR-012: The gate fixes locally and fails strictly

## Status

Accepted, 2026-09-25.

## Decision

Locally, before it checks anything, the gate rewrites what the machine can fix and names what it
rewrote:
- the agent wrappers `CLAUDE.md` and `AGENTS.md`, regenerated from `docs/agent_rules.md`;
- the Python the checkout changed — tracked files that differ from `HEAD` and new files — through
  `ruff format` and ruff's safe fixes (`ruff check --fix --no-unsafe-fixes`).

`make quality-gates` and `make gate-fast` both do this. Two flags turn each fix into a failure:
`STRICT_GENERATED=1` for the wrappers and `STRICT_RUFF=1` for Python. `ci-local`, CI and the
product check set both. The pre-commit hook sets `STRICT_RUFF=1` and refuses a Python file that
was staged and then edited again.

`make gate-fast` is for the edit loop. It runs format, lint, mypy over every source root and every
test suite, and the layer rules, in about three seconds. `make quality-gates` stays the one command
that says a change is done.

A narrow `pytest` measures no coverage. The full run holds the total to the floor and prints it in
one line.

## Why

- **A check whose fix is always the same command is a ritual.** In the first A/B measurement, the
  only red gate either arm saw was a generated map left stale by an edit. Both times it was fixed
  by the same command, and three more times in one session. The wrappers have been refreshed
  rather than reported since then, and in bench2 that refresh fired 13 and 10 times without once
  blocking a gate.
- **bench2 counted the same for Python.** The two arms ran the full gate 64 and 58 times. Ruff or
  mypy caused 79–86% of the red outcomes, and format or lint alone caused 63–66%. The full gate
  took about a minute, so agents started it in the background and often never read the outcome.
- **Only changed files are rewritten.** An untouched file that the gate rewrote would land in a diff
  about something else. `make ai-autofix` remains the command for the whole tree.
- **The hook is strict because it checks one thing and commits another.** It checks the working
  tree, and git commits the index. A file the hook rewrote would pass the check and still be left
  out of the commit. A file staged and then edited again is the same split, so the hook refuses it.
- **An earlier fast rung failed, and this one differs from it.** That rung was diff-only, ran no
  mypy and resolved no tests for about half of `project/`, so a green fast check could sit next to
  a red gate. `gate-fast` type-checks every test suite and is not named as a completion check
  anywhere.
- **The coverage table was noise in context.** With coverage in `pytest.ini`'s `addopts`, every
  single-file run printed a fifty-line table about files it never meant to measure. After a full
  run, the table is `uv run coverage report -m`.

## Reproduce

- The bench2 counts come from its stage transcripts, which are kept with the bench, not in this
  repository: count the `make quality-gates` invocations and the first red layer of each.
- The timings come from `/usr/bin/time -p make -s gate-fast` against
  `/usr/bin/time -p make -s quality-gates-steps`, on a warm mypy cache.

Whether this cuts the number of red gates, as the plan hopes (up to −60%), is for bench3 to
measure. It is not an effect this change proves.
