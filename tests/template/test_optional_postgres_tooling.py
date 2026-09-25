# FILE: tests/template/test_optional_postgres_tooling.py
# SUMMARY: The migration gate with POSTGRES_ENABLED=false: its own informational rule, not the skip path.

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_migrations import (
    collect_migration_issues,
    get_migrations_rule_playbook,
)
from scripts.validate_migrations import main as migrations_main

_REPO_ROOT = Path(__file__).resolve().parents[2]


# Verify the migration gate distinguishes "not used" from "temporarily unreachable".
class TestMigrationGateWithoutPostgres:
    # Verify the gate passes with a dedicated informational rule, not the skip path.
    @pytest.mark.unit
    def test_disabled_database_reports_its_own_rule(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("POSTGRES_ENABLED", "false")

        issues = collect_migration_issues(_REPO_ROOT)

        assert [issue.rule_id for issue in issues] == ["migrations.database_disabled"]
        assert issues[0].severity == "info"

    @pytest.mark.unit
    def test_disabled_database_passes_even_in_ci(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POSTGRES_ENABLED", "false")
        monkeypatch.setenv("CI", "true")
        monkeypatch.delenv("MIGRATIONS_ALLOW_SKIP", raising=False)

        assert migrations_main([]) == 0

    # Verify the strict CI behaviour added earlier is untouched for projects that use a database.
    @pytest.mark.unit
    def test_enabled_database_still_fails_in_ci_without_a_server(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setenv("POSTGRES_ENABLED", "true")
        monkeypatch.setenv("CI", "true")
        monkeypatch.delenv("MIGRATIONS_ALLOW_SKIP", raising=False)
        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: False)

        assert migrations_main([]) == 1

    @pytest.mark.unit
    def test_disabled_rule_has_a_playbook(self) -> None:
        playbook = get_migrations_rule_playbook("migrations.database_disabled")

        assert playbook is not None
        assert playbook["stop_widening_condition"]
