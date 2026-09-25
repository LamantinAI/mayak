# FILE: tests/application/test_optional_postgres.py
# SUMMARY: Guards for POSTGRES_ENABLED — the kernel must assemble, report ready and accept its
# settings when a project declares it needs no relational store. The template's tools under the
# same flag are tested in tests/template/test_optional_postgres_tooling.py.

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from starlette.testclient import TestClient

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
        # services dict literal by key name, and tests/template/test_generate_ai_context.py
        # asserts db_pool is among the kernel's shared services.
        assert "db_pool" in services

    # Verify the default path is untouched, so existing projects keep working.
    @pytest.mark.unit
    def test_pool_is_built_when_enabled(self, fastapi_app: FastAPI) -> None:
        assert fastapi_app.state.services["db_pool"] is not None


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
