# FILE: tests/db/test_migrations_match_models.py
# SUMMARY: Verify the migrations build a schema that matches the ORM metadata, inside `make test`.
# scripts/validate_migrations.py is in `make quality-gates`, and on a machine with no database
# reachable through .env it reports "MIGRATIONS NOT VERIFIED" and returns 0 — the right call for a
# gate, and the reason it could never fail locally. This test is the same check with a database
# that always exists: the db tier's own, where the session fixture has already run
# `alembic upgrade head` and `alembic check` through that script. It lived in the functional suite
# until 2026-09-24, where a migration that disagreed with the ORM was caught only by `make test-e2e`
# (the mutation baseline in docs/mutations/, `migration_title_shorter`).

from scripts.validate_migrations import MigrationIssue


# SUMMARY: Verify `alembic check` reports no difference between the migrated schema and the models.
def test_alembic_head_matches_the_orm_metadata(migration_issues: list[MigrationIssue]) -> None:
    assert migration_issues == [], "\n".join(
        f"{issue.rule_id}: {issue.message}" for issue in migration_issues
    )
