# ADR-006: PostgreSQL Is Optional

## Status

Accepted (2026-08-05)

## Decision

The kernel treats PostgreSQL as an optional subsystem, switched by a single boolean:
`POSTGRES_ENABLED`, default `true`. When it is `false` the composition root builds no connection
pool, the entrypoint runs no migrations, `/health/ready` reports the database as `disabled`
rather than failing, and the migration gate returns `migrations.database_disabled` (severity
`info`). Every existing project is unaffected: omitting the variable keeps the previous
behaviour exactly.

## Rationale

The kernel required PostgreSQL in seven places, from the pool construction through the container
entrypoint to the readiness contract. A service that needs only vector search, or no persistence
at all, could not start on this template — the container exited 1 before the application was
reached. That is a hard constraint on what the template can be used for, and it was never a
deliberate decision, only an accumulated default.

Two shapes were rejected:

- **`storage_backend: Literal["postgres", "none"]`.** It advertises a choice of backends that
  does not exist, and invites the next reader to extend the literal with whatever store their
  project happens to use — a document store, a vector index — none of which substitute for a
  relational one. They are separate capabilities that would each need their own flag. A name
  that promises more than the code delivers is the same class of defect the 2026-08-05 audit
  spent a day removing.
- **Generation-time profiles that physically remove the PostgreSQL code.** One-way door: a
  project generated as `bare` could no longer accept a kernel update through the ordinary
  diff-and-merge path this template depends on, and the estimate was three to four times the
  cost of the flag.

## Operationalization

- `project/core/config_settings_core.py` — `PostgresSettings.enabled`
- `project/core/composition_root.py` — conditional pool construction
- `project/infrastructure/api/endpoints/health.py` — `disabled` status, dropped from criticals
- `project/core/config_runtime.py` — default-password guard applies only when enabled
- `scripts/validate_migrations.py` — `migrations.database_disabled`
- `entrypoint.sh`, `Makefile`, `docker-compose.yml`, `docker-compose.postgres.yml` — the base
  compose file matters as much as the overlay: it is what forwards `POSTGRES_ENABLED` into the
  container. Without that line the Makefile dropped the database service while `entrypoint.sh`,
  reading only `.env`, still ran Alembic against a server nobody started.
- `ai_query/handlers/overview.py` — the agent-visible state
- `tests/application/test_optional_postgres.py`

## Consequences

- A vertical that needs the database must handle `db_pool is None` explicitly; mypy enforces it
  through the `AsyncConnectionPool | None` signature of `build_reference_services`.
- The conditional construction **must** be written as `db_pool = None` above the `if`, with no
  `else` branch. `ai_context/extraction.py` resolves the service registry statically and
  `ast.walk` visits an `If` node as `test → body → orelse`, so an assignment in `else` is
  processed last and overwrites the binding metadata: `db_pool` degrades from
  `class=AsyncConnectionPool / confidence=high` to `class=null / confidence=low`, and
  `docs/ai_context_map.json` starts misdescribing the kernel's own service. Measured, and guarded
  by `test_composition_root_has_no_else_branch_for_the_pool`.
- `POSTGRES_ENABLED` is deliberately **not** `MIGRATIONS_ALLOW_SKIP`. The latter is the emergency
  hatch for "the database is temporarily unreachable" and stays an error-suppressor; this flag
  describes the project's composition. One switch covering both would hide a real outage behind a
  design choice.
- `sqlalchemy`, `alembic` and `psycopg` are still installed unconditionally. Moving them to
  extras needs conditional imports in three modules and is a separate decision.
- A bare `docker compose up` now starts the application without a database. The database lives in
  `docker-compose.postgres.yml`, layered on by `make smoke`; run `docker compose -f
  docker-compose.yml -f docker-compose.postgres.yml up` to get the old behaviour by hand.
- When a **third** optional subsystem appears, copying "one field plus one `if`" a third time
  stops being reasonable. That is the point to introduce a shared `is_enabled()` protocol on the
  settings models — not before.
