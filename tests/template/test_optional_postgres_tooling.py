# FILE: tests/template/test_optional_postgres_tooling.py
# SUMMARY: The template's tools with POSTGRES_ENABLED=false: the migration gate's own rule, the query
# layer's view of the subsystem, and the static extraction of the pool's wiring.

from __future__ import annotations

from pathlib import Path

import pytest

from ai_context.extraction import extract_service_registry_entries
from ai_query.common import zone_for_path
from ai_query.handlers.overview import integrations_overview
from scripts.generate_ai_context import build_architecture_rules
from scripts.validate_migrations import (
    collect_migration_issues,
    get_migrations_rule_playbook,
)
from scripts.validate_migrations import main as migrations_main

_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestStaticAnalysisSurvivesTheBranch:
    @pytest.mark.unit
    def test_db_pool_still_resolves_to_its_class(self) -> None:
        # This is the regression that made the branch order non-negotiable.
        # ast.walk visits an If node as test -> body -> orelse, so an assignment inside an
        # `else` is processed last and overwrites the binding metadata: db_pool degrades to
        # class=null / confidence=low, and docs/ai_context_map.json starts lying about the
        # kernel's own service. Declaring None above the branch avoids it. Measured, and this
        # test is what keeps it measured.
        entries = extract_service_registry_entries(
            _REPO_ROOT / "project" / "core" / "composition_root.py",
            _REPO_ROOT,
            "shared",
        )

        assert entries["db_pool"]["class"] == "AsyncConnectionPool"
        assert entries["db_pool"]["confidence"] == "high"
        assert entries["db_pool"]["resolution_status"] == "resolved"

    # Verify nobody reintroduces the `else` that breaks the extractor.
    @pytest.mark.unit
    def test_composition_root_has_no_else_branch_for_the_pool(self) -> None:
        source = (_REPO_ROOT / "project" / "core" / "composition_root.py").read_text(
            encoding="utf-8"
        )
        branch = source.split("if settings.postgres.enabled:", 1)[1].split(
            "llm_service = LLMService(", 1
        )[0]

        assert "else:" not in branch


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


# Verify the agent learns the project's composition from a query, not from reading .env.
class TestAgentCanSeeTheSubsystemState:
    # Verify docs/project_context.json finally reaches the query layer.
    @pytest.mark.unit
    def test_overview_reports_declared_integrations(self) -> None:
        # overview and bootstrap were built only from the generated maps, so the
        # one file where a project declares its external systems was invisible to every query.
        integrations = integrations_overview()

        assert "postgres" in integrations
        assert integrations["postgres"]["type"] == "database"

    # Verify the reported state follows the environment rather than the description text.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("false", False), ("true", True), ("0", False), ("1", True)],
    )
    def test_overview_reports_the_live_toggle(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
        expected: bool,
    ) -> None:
        monkeypatch.setenv("POSTGRES_ENABLED", value)

        assert integrations_overview()["postgres"]["enabled"] is expected

    @pytest.mark.unit
    def test_overview_names_the_toggle_variable(self) -> None:
        assert integrations_overview()["postgres"]["toggle"] == "POSTGRES_ENABLED"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "path",
        [
            "docker-compose.yml",
            "docker-compose.postgres.yml",
            "entrypoint.sh",
            "Dockerfile",
            "alembic/env.py",
        ],
    )
    def test_deployment_paths_are_indexed_for_before_edit(self, path: str) -> None:
        rules = build_architecture_rules()

        result = zone_for_path(path, rules)

        assert result["zone"] != "unclassified", path
