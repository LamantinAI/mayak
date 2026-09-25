# FILE: tests/application/test_optional_postgres.py
# SUMMARY: Guards for POSTGRES_ENABLED — the kernel must assemble, report ready and stay
# statically analysable when a project declares it needs no relational store.

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from starlette.testclient import TestClient

from ai_context.extraction import extract_service_registry_entries
from ai_query.common import zone_for_path
from ai_query.handlers.overview import integrations_overview
from scripts.generate_ai_context import build_architecture_rules
from scripts.validate_migrations import (
    collect_migration_issues,
    get_migrations_rule_playbook,
)
from scripts.validate_migrations import main as migrations_main
from tests.conftest import _FixtureSettings as FixtureSettings

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _settings_without_postgres() -> FixtureSettings:
    settings = FixtureSettings()
    settings.postgres.enabled = False
    return settings


class TestKernelAssemblesWithoutPostgres:
    @pytest.mark.unit
    def test_pool_is_absent_but_key_remains(self, app_without_postgres: FastAPI) -> None:
        services = app_without_postgres.state.services

        assert services["db_pool"] is None
        # The key itself must survive: ai_context/extraction.py reads the
        # services dict literal by key name, and tests/application/test_generate_ai_context.py
        # asserts db_pool is among the kernel's shared services.
        assert "db_pool" in services

    # Verify the default path is untouched, so existing projects keep working.
    @pytest.mark.unit
    def test_pool_is_built_when_enabled(self, fastapi_app: FastAPI) -> None:
        assert fastapi_app.state.services["db_pool"] is not None


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


class TestReadinessWithoutPostgres:
    @pytest.mark.unit
    def test_ready_returns_200_and_marks_database_disabled(
        self,
        app_without_postgres: FastAPI,
    ) -> None:
        # Without this, POSTGRES_ENABLED=false produced a service that starts
        # and then reports 503 forever — a flag that is technically honoured and practically
        # useless.
        client = TestClient(app_without_postgres)

        response = client.get("/health/ready")

        assert response.status_code == 200
        assert response.json()["checks"]["database"]["status"] == "disabled"

    @pytest.mark.unit
    def test_missing_pool_is_still_a_fault_when_database_is_enabled(
        self,
        fastapi_app: FastAPI,
    ) -> None:
        fastapi_app.state.services["db_pool"] = None
        client = TestClient(fastapi_app)

        response = client.get("/health/ready")

        assert response.status_code == 503
        assert response.json()["checks"]["database"]["status"] == "unhealthy"


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


class TestRuntimeValidationWithoutPostgres:
    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    def test_default_password_is_accepted_when_database_is_disabled(self) -> None:
        settings = _settings_without_postgres()
        settings.postgres.password = SecretStr("postgres")
        settings.server.cors_origins = ["http://127.0.0.1:8000"]

        settings.validate_runtime()  # no-assert-ok: the assertion is that this does not raise

    @pytest.mark.unit
    def test_default_password_is_still_rejected_when_database_is_enabled(self) -> None:
        settings = FixtureSettings()
        settings.postgres.password = SecretStr("postgres")

        with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
            settings.validate_runtime()


class TestFlagReachesTheContainer:
    @pytest.mark.unit
    def test_compose_passes_the_toggle_into_the_app_service(self) -> None:
        # A literal check, because the thing being guarded is a literal line in a
        # config file. Without it, `POSTGRES_ENABLED=false make smoke` reached the Makefile — which
        # correctly dropped the postgres overlay — but not entrypoint.sh, which read `true` from
        # .env, ran alembic against a database nobody started, and exited 1. The mode was then
        # testable only by editing .env by hand, which is how the gap survived unnoticed.
        compose = (_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        assert "POSTGRES_ENABLED: ${POSTGRES_ENABLED:-true}" in compose


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
