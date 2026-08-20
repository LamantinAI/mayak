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


# FUNCTION: _settings_without_postgres
# SUMMARY: Build test settings that declare no relational store.
# OUTPUT: (FixtureSettings): Settings with the database switched off.
def _settings_without_postgres() -> FixtureSettings:
    settings = FixtureSettings()
    settings.postgres.enabled = False
    return settings


# CLASS: tests.application.test_optional_postgres.TestKernelAssemblesWithoutPostgres
# SUMMARY: Verify the composition root produces a working registry with no pool in it.
class TestKernelAssemblesWithoutPostgres:
    # FUNCTION: test_pool_is_absent_but_key_remains
    # SUMMARY: Verify db_pool becomes None while the registry keeps its shape.
    @pytest.mark.unit
    def test_pool_is_absent_but_key_remains(self, app_without_postgres: FastAPI) -> None:
        services = app_without_postgres.state.services

        assert services["db_pool"] is None
        # **LOGIC_STEP**: The key itself must survive: ai_context/extraction.py reads the
        # services dict literal by key name, and tests/application/test_generate_ai_context.py
        # asserts db_pool is among the kernel's shared services.
        assert "db_pool" in services

    # FUNCTION: test_pool_is_built_when_enabled
    # SUMMARY: Verify the default path is untouched, so existing projects keep working.
    @pytest.mark.unit
    def test_pool_is_built_when_enabled(self, fastapi_app: FastAPI) -> None:
        assert fastapi_app.state.services["db_pool"] is not None


# CLASS: tests.application.test_optional_postgres.TestStaticAnalysisSurvivesTheBranch
# SUMMARY: Verify the conditional pool construction keeps the service registry resolvable.
class TestStaticAnalysisSurvivesTheBranch:
    # FUNCTION: test_db_pool_still_resolves_to_its_class
    # SUMMARY: Verify the generated context map still names the pool class with high confidence.
    @pytest.mark.unit
    def test_db_pool_still_resolves_to_its_class(self) -> None:
        # **LOGIC_STEP**: This is the regression that made the branch order non-negotiable.
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

    # FUNCTION: test_composition_root_has_no_else_branch_for_the_pool
    # SUMMARY: Verify nobody reintroduces the `else` that breaks the extractor.
    @pytest.mark.unit
    def test_composition_root_has_no_else_branch_for_the_pool(self) -> None:
        source = (_REPO_ROOT / "project" / "core" / "composition_root.py").read_text(
            encoding="utf-8"
        )
        branch = source.split("if settings.postgres.enabled:", 1)[1].split(
            "llm_service = LLMService(", 1
        )[0]

        assert "else:" not in branch


# CLASS: tests.application.test_optional_postgres.TestReadinessWithoutPostgres
# SUMMARY: Verify a project without a relational store still reports itself ready.
class TestReadinessWithoutPostgres:
    # FUNCTION: test_ready_returns_200_and_marks_database_disabled
    # SUMMARY: Verify the absent pool is reported as disabled rather than as a fault.
    @pytest.mark.unit
    def test_ready_returns_200_and_marks_database_disabled(
        self,
        app_without_postgres: FastAPI,
    ) -> None:
        # **LOGIC_STEP**: Without this, POSTGRES_ENABLED=false produced a service that starts
        # and then reports 503 forever — a flag that is technically honoured and practically
        # useless.
        client = TestClient(app_without_postgres)

        response = client.get("/health/ready")

        assert response.status_code == 200
        assert response.json()["checks"]["database"]["status"] == "disabled"

    # FUNCTION: test_missing_pool_is_still_a_fault_when_database_is_enabled
    # SUMMARY: Verify the disabled branch does not mask a genuinely broken configuration.
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


# CLASS: tests.application.test_optional_postgres.TestMigrationGateWithoutPostgres
# SUMMARY: Verify the migration gate distinguishes "not used" from "temporarily unreachable".
class TestMigrationGateWithoutPostgres:
    # FUNCTION: test_disabled_database_reports_its_own_rule
    # SUMMARY: Verify the gate passes with a dedicated informational rule, not the skip path.
    @pytest.mark.unit
    def test_disabled_database_reports_its_own_rule(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("POSTGRES_ENABLED", "false")

        issues = collect_migration_issues(_REPO_ROOT)

        assert [issue.rule_id for issue in issues] == ["migrations.database_disabled"]
        assert issues[0].severity == "info"

    # FUNCTION: test_disabled_database_passes_even_in_ci
    # SUMMARY: Verify CI does not demand a database the project declared it does not use.
    @pytest.mark.unit
    def test_disabled_database_passes_even_in_ci(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POSTGRES_ENABLED", "false")
        monkeypatch.setenv("CI", "true")
        monkeypatch.delenv("MIGRATIONS_ALLOW_SKIP", raising=False)

        assert migrations_main([]) == 0

    # FUNCTION: test_enabled_database_still_fails_in_ci_without_a_server
    # SUMMARY: Verify the strict CI behaviour added earlier is untouched for projects that use a database.
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

    # FUNCTION: test_disabled_rule_has_a_playbook
    # SUMMARY: Verify the new rule carries remediation guidance like every other rule.
    @pytest.mark.unit
    def test_disabled_rule_has_a_playbook(self) -> None:
        playbook = get_migrations_rule_playbook("migrations.database_disabled")

        assert playbook is not None
        assert playbook["stop_widening_condition"]


# CLASS: tests.application.test_optional_postgres.TestRuntimeValidationWithoutPostgres
# SUMMARY: Verify configuration guards do not demand credentials for an unused database.
class TestRuntimeValidationWithoutPostgres:
    # FUNCTION: test_default_password_is_accepted_when_database_is_disabled
    # SUMMARY: Verify a project without a database is not forced to invent a password.
    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    def test_default_password_is_accepted_when_database_is_disabled(self) -> None:
        settings = _settings_without_postgres()
        settings.postgres.password = SecretStr("postgres")
        settings.server.cors_origins = ["http://127.0.0.1:8000"]

        settings.validate_runtime()  # no-assert-ok: the assertion is that this does not raise

    # FUNCTION: test_default_password_is_still_rejected_when_database_is_enabled
    # SUMMARY: Verify the guard is intact for projects that do use a database.
    @pytest.mark.unit
    def test_default_password_is_still_rejected_when_database_is_enabled(self) -> None:
        settings = FixtureSettings()
        settings.postgres.password = SecretStr("postgres")

        with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
            settings.validate_runtime()


# CLASS: tests.application.test_optional_postgres.TestFlagReachesTheContainer
# SUMMARY: Verify the toggle survives the trip from the shell into the running image.
class TestFlagReachesTheContainer:
    # FUNCTION: test_compose_passes_the_toggle_into_the_app_service
    # SUMMARY: Verify docker-compose.yml forwards POSTGRES_ENABLED instead of relying on .env alone.
    @pytest.mark.unit
    def test_compose_passes_the_toggle_into_the_app_service(self) -> None:
        # **LOGIC_STEP**: A literal check, because the thing being guarded is a literal line in a
        # config file. Without it, `POSTGRES_ENABLED=false make smoke` reached the Makefile — which
        # correctly dropped the postgres overlay — but not entrypoint.sh, which read `true` from
        # .env, ran alembic against a database nobody started, and exited 1. The mode was then
        # testable only by editing .env by hand, which is how the gap survived unnoticed.
        compose = (_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        assert "POSTGRES_ENABLED: ${POSTGRES_ENABLED:-true}" in compose


# CLASS: tests.application.test_optional_postgres.TestAgentCanSeeTheSubsystemState
# SUMMARY: Verify the agent learns the project's composition from a query, not from reading .env.
class TestAgentCanSeeTheSubsystemState:
    # FUNCTION: test_overview_reports_declared_integrations
    # SUMMARY: Verify docs/project_context.json finally reaches the query layer.
    @pytest.mark.unit
    def test_overview_reports_declared_integrations(self) -> None:
        # **LOGIC_STEP**: overview and bootstrap were built only from the generated maps, so the
        # one file where a project declares its external systems was invisible to every query.
        integrations = integrations_overview()

        assert "postgres" in integrations
        assert integrations["postgres"]["type"] == "database"

    # FUNCTION: test_overview_reports_the_live_toggle
    # SUMMARY: Verify the reported state follows the environment rather than the description text.
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

    # FUNCTION: test_overview_names_the_toggle_variable
    # SUMMARY: Verify the agent is told which variable to change, not just the current state.
    @pytest.mark.unit
    def test_overview_names_the_toggle_variable(self) -> None:
        assert integrations_overview()["postgres"]["toggle"] == "POSTGRES_ENABLED"

    # FUNCTION: test_deployment_paths_are_indexed_for_before_edit
    # SUMMARY: Verify the files this feature edits carry guidance instead of an unindexed error.
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
