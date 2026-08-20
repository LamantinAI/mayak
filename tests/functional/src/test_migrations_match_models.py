# FILE: tests/functional/src/test_migrations_match_models.py
# SUMMARY: Run the migration gate where a database actually exists.
# NOTE: scripts/validate_migrations.py is in `make quality-gates`, and on a developer machine with
# no Postgres it prints "Database is not reachable — migration validation skipped" and returns 0.
# That is the right call locally, but it left the gate unable to fail anywhere: nothing in the
# Makefile sets CI=1, so `database_skip_is_allowed()` was true on every run in this repository, and
# a second Alembic head or an ORM model without its migration would have travelled all the way to
# a deployment. The functional stack is the one place that has a real database and has already
# migrated it, so the check belongs here — with require_database=True, which turns the skip itself
# into a failure.

import pytest

from scripts.validate_migrations import ROOT_DIR, collect_migration_issues, postgres_is_enabled

# **LOGIC_STEP**: Marked at module level so `-m e2e` selects it with the rest of the suite.
pytestmark = pytest.mark.e2e


# FUNCTION: test_alembic_head_matches_the_orm_metadata
# SUMMARY: Verify `alembic upgrade head` succeeds and `alembic check` reports no pending changes.
def test_alembic_head_matches_the_orm_metadata() -> None:
    if not postgres_is_enabled():
        pytest.skip("POSTGRES_ENABLED=false — this project declares no relational store")

    # **LOGIC_STEP**: require_database=True is the whole point. Without it an unreachable database
    # downgrades to an info-level skip and this test would pass by not running the check — the
    # exact failure mode it exists to close.
    issues = collect_migration_issues(ROOT_DIR, require_database=True)

    assert issues == [], "\n".join(f"{issue.rule_id}: {issue.message}" for issue in issues)
