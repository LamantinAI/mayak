# ADR-010: What the Gates Do Not See

## Status

Accepted (2026-09-08)

## Decision

`make quality-gates` runs none of the repository's own queries, so it cannot see a defect in SQL —
a query is only caught if some test pins its clause as literal text. The one step that opens a
connection is the migration gate below, and it asks the database about Alembic's state, not
yours; with no database it downgrades to an informational skip.
`AGENTS.md` states the two consequences and nothing else: finish with `make quality-gates`, and add
`make test-e2e` when the diff touches persistence, endpoints, wiring, or a migration. The measurements
below are why those two sentences are load-bearing rather than routine advice.

## Measured: a query defect gates cannot see on its own

On 2026-08-12, reversing `ORDER BY created_at DESC` to `ASC` in the shipped repository left every
gate green; only `make test-e2e` failed. Remeasured on 2026-08-24 with the same reversal:
`STRICT_GENERATED=1 make quality-gates` now fails with no database at all, at a plain literal-string
assertion in `tests/infrastructure/test_reference_task_repository.py` —
`assert _SELECT_BY_STATUS.split(" WHERE ", 1)[1] == "status = %s ORDER BY created_at DESC LIMIT %s"`.
That assertion is the trap, not `scripts/validate_test_quality.py`: run the validator alone against
the same reversal and it still exits 0. Its `test.sql_constant_round_trip` rule only checks that some
module pins a clause of a query constant as literal text *somewhere* — it proves the trap exists, not
that the pinned text is correct. `assert sql is _CONSTANT` counts as the same tautology as `==`, and
`"clause text" in _CONSTANT` counts as a pin, so a substring trap is legitimate, and a constant
imported from a `project.` module is the only kind the rule reads. A query with such a pinned test is
caught by the ordinary gate; a query without one stays invisible to every gate.

## Measured: the saving is the database, not the clock

`STRICT_GENERATED=1 make quality-gates-steps` took 13.1s to reach that failure on 2026-08-24, against
13.7s for a passing run of the same target — lockfile, lint, format, mypy and every validator run
before pytest is reached, and `make quality-gates` adds the `make doctor` it invokes on failure. Both
numbers move as the suite grows; reproduce with `time STRICT_GENERATED=1 make quality-gates-steps`
rather than trusting either one.

## Migrations: the skip that only fails in CI

`scripts/validate_migrations.py` runs `alembic upgrade head` and `alembic check` against a real
database. With none reachable it prints `MIGRATIONS NOT VERIFIED`, names `make db-up-worktree`, and
still exits green — `database_skip_is_allowed()` returns `False` only when `CI` is `1`, `true` or
`yes`, which is why the CI job provisions Postgres. `make doctor` repeats the same sentence rather
than re-wording it. Since 2026-09-02 two defects are read from the revision files alone and checked
before that skip, with no database needed: a second head and a `down_revision` naming no revision
(`migrations.multiple_heads`, `migrations.broken_revision_graph`).

## Operationalization

- `scripts/validate_test_quality.py` — `test.sql_constant_round_trip`.
- `scripts/validate_migrations.py` — the skip, its CI override, and the two graph checks.
- `Makefile` — `quality-gates`, `quality-gates-steps`, `test-e2e`, `db-up-worktree`.
