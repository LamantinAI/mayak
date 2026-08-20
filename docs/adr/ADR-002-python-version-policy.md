# ADR-002: Python Version Policy

## Status

Accepted. Amended 2026-08-06: the support floor moved up to match the local toolchain, and the
compatibility lane was removed with it.

## Decision

The supported floor is `requires-python` in `pyproject.toml`. The local toolchain is
`.python-version`. `mypy` targets the floor, never the local toolchain. None of those numbers is
repeated in prose anywhere — `CLAUDE.md` and `docs/architecture_rules.json` render them from the
two files, and this document deliberately names neither.

While the two numbers are equal, the template ships **no** compatibility lane: a job that re-runs
the suite on the interpreter the main gate already used cannot fail, and a gate that cannot fail is
worse than no gate — it reports coverage it does not have.

When the toolchain moves ahead of the floor again, the lane comes back, in both places at once:

- `.github/workflows/ci.yml` — a job that passes `--python <floor>` to **both** `uv sync` and
  `uv run`.
- `Makefile` — the same two commands behind a target, with `UV_PROJECT_ENVIRONMENT` pointed at a
  separate virtualenv, and that target added to `ci-local`.

## Rationale

The original policy assumed the two numbers would differ and that a CI lane would prove the floor.
Neither held. The lane installed the floor interpreter with `actions/setup-python` and then ran
`uv sync --frozen`, which honours `.python-version` — so it built the environment on the local
toolchain and reported compatibility it never tested. When the lane was finally made real, the
suite failed on the declared floor: five tests on 3.10, four on 3.11, three on 3.12, none on 3.13.
The failures were in the template's own tooling, not in the runtime it ships.

Raising the floor to the toolchain is the honest resolution of that gap. The alternative — teaching
the tooling to run on an older interpreter — buys compatibility nobody had asked for, in code no
consumer of the template runs.

## Operationalization

- `pyproject.toml` — `requires-python`, `[tool.ruff].target-version`, `[tool.mypy].python_version`
- `.python-version`
- `.github/workflows/ci.yml`
- `Makefile` — `ci-local`

## Consequences

- The declared floor is a version the whole test suite actually passes on. It was not before.
- A project that must run on an older interpreter forks the floor and reinstates the lane above.
- `tomllib` is available unconditionally, which removed a hand-rolled TOML scanner in
  `scripts/validate_dependencies.py` and an `ImportError` fallback in `scripts/structure_builder.py`
  that existed only for the old floor.
- Moving the local toolchain forward is now a dependency question, not a policy one: the pinned set
  has to grow wheels for the newer interpreter first.
