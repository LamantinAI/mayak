# FILE: tests/application/test_health_endpoints.py
# SUMMARY: Endpoint tests for liveness and readiness health contracts.

import asyncio
import time
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from fastapi import FastAPI

from tests.conftest import _FixtureSettings as FixtureSettings


# FUNCTION: _mock_pool_with_migration_state
# SUMMARY: Build a connection pool double whose alembic_version lookup answers the given state.
# OUTPUT: (tuple[AsyncMock, MagicMock]): The connection double and the pool double.
def _mock_pool_with_migration_state(*, migrated: bool) -> tuple[AsyncMock, MagicMock]:
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=(migrated,))

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=cursor)

    mock_pool = MagicMock()
    connection_ctx = MagicMock()
    connection_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    connection_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_pool.connection.return_value = connection_ctx
    return mock_conn, mock_pool


# CLASS: tests.application.test_health_endpoints.TestHealthEndpoints
# SUMMARY: Test suite for health and readiness endpoint behavior.
class TestHealthEndpoints:
    # FUNCTION: test_liveness_returns_healthy
    # SUMMARY: Verify the cheap liveness endpoint always returns healthy.
    @pytest.mark.unit
    async def test_liveness_returns_healthy(self, async_client: AsyncClient) -> None:
        response = await async_client.get("/health/")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    # FUNCTION: test_readiness_returns_healthy_when_critical_dependencies_are_available
    # SUMMARY: Verify readiness returns HTTP 200 when critical checks succeed.
    @pytest.mark.unit
    async def test_readiness_returns_healthy_when_critical_dependencies_are_available(
        self,
        async_client: AsyncClient,
        test_settings: FixtureSettings,
    ) -> None:
        with patch(
            "project.infrastructure.api.endpoints.health._check_database",
            return_value={
                "status": "healthy",
                "message": "Database connection successful",
                "response_time_ms": 1.23,
            },
        ):
            response = await async_client.get("/health/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["checks"]["database"]["status"] == "healthy"

    # FUNCTION: test_readiness_returns_503_when_database_is_unavailable
    # SUMMARY: Verify readiness fails with HTTP 503 when a critical dependency is unhealthy.
    @pytest.mark.unit
    async def test_readiness_returns_503_when_database_is_unavailable(
        self,
        async_client: AsyncClient,
        test_settings: FixtureSettings,
    ) -> None:
        with patch(
            "project.infrastructure.api.endpoints.health._check_database",
            return_value={
                "status": "unhealthy",
                "message": "Database connection failed",
                "response_time_ms": None,
            },
        ):
            response = await async_client.get("/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["checks"]["database"]["status"] == "unhealthy"
        assert data["checks"]["database"]["message"] == "Database connection failed"

    # FUNCTION: test_readiness_database_failure_message_hides_internal_details
    # SUMMARY: Verify database readiness responses never leak raw exception details.
    @pytest.mark.unit
    async def test_readiness_database_failure_message_hides_internal_details(
        self,
        async_client: AsyncClient,
        test_settings: FixtureSettings,
    ) -> None:
        with patch(
            "project.infrastructure.api.endpoints.health._check_database",
            return_value={
                "status": "unhealthy",
                "message": "postgres://internal-user:secret@db.internal/template",  # allow-secret: fixture proving the DSN never reaches the client
                "response_time_ms": None,
            },
        ):
            response = await async_client.get("/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["checks"]["database"]["message"] == "Database connection failed"
        assert "secret" not in response.text
        assert "db.internal" not in response.text

    # FUNCTION: test_readiness_uses_llm_probe_result
    # SUMMARY: Verify readiness surfaces real LLM probe failures as unhealthy. Since ADR-008 this
    # also requires llm_readiness_critical=true — the check surfacing "unhealthy" and that status
    # reaching the HTTP code are two different things now; see
    # test_readiness_llm_unhealthy_not_critical_by_default for the default-false half.
    @pytest.mark.unit
    async def test_readiness_uses_llm_probe_result(
        self,
        async_client: AsyncClient,
        fastapi_app: FastAPI,
        test_settings: FixtureSettings,
    ) -> None:
        original_mode = test_settings.agent.llm_readiness_check_mode
        original_critical = test_settings.agent.llm_readiness_critical
        test_settings.agent.llm_readiness_check_mode = "probe"
        test_settings.agent.llm_readiness_critical = True

        llm_service = fastapi_app.state.services["llm_service"]
        original_check_readiness = llm_service.check_readiness
        llm_service.check_readiness = AsyncMock(
            return_value={
                "status": "unhealthy",
                "message": "LLM provider probe failed",
                "mode": "probe",
                "provider_reachable": False,
                "error": "TimeoutError",
            }
        )

        try:
            with patch(
                "project.infrastructure.api.endpoints.health._check_database",
                return_value={
                    "status": "healthy",
                    "message": "Database connection successful",
                    "response_time_ms": 1.23,
                },
            ):
                response = await async_client.get("/health/ready")
        finally:
            test_settings.agent.llm_readiness_check_mode = original_mode
            test_settings.agent.llm_readiness_critical = original_critical
            llm_service.check_readiness = original_check_readiness

        assert response.status_code == 503
        data = response.json()
        assert data["checks"]["llm"]["mode"] == "probe"
        assert data["checks"]["llm"]["provider_reachable"] is False

    # FUNCTION: test_readiness_llm_failure_message_hides_internal_details
    # SUMMARY: Verify LLM readiness payloads stay stable when the underlying failure contains
    # secrets. Sets llm_readiness_critical=true (since ADR-008, default false) so the failure
    # still reaches the HTTP status this test asserts on, alongside the message.
    @pytest.mark.unit
    async def test_readiness_llm_failure_message_hides_internal_details(
        self,
        async_client: AsyncClient,
        fastapi_app: FastAPI,
        test_settings: FixtureSettings,
    ) -> None:
        original_critical = test_settings.agent.llm_readiness_critical
        test_settings.agent.llm_readiness_critical = True

        llm_service = fastapi_app.state.services["llm_service"]
        original_check_readiness = llm_service.check_readiness
        llm_service.check_readiness = AsyncMock(
            return_value={
                "status": "unhealthy",
                "message": "provider timeout secret-token=abc123",
                "mode": "probe",
                "provider_reachable": False,
                "error": "TimeoutError",
            }
        )

        try:
            with patch(
                "project.infrastructure.api.endpoints.health._check_database",
                return_value={
                    "status": "healthy",
                    "message": "Database connection successful",
                    "response_time_ms": 1.23,
                },
            ):
                response = await async_client.get("/health/ready")
        finally:
            test_settings.agent.llm_readiness_critical = original_critical
            llm_service.check_readiness = original_check_readiness

        assert response.status_code == 503
        assert "secret-token" not in response.text

    # FUNCTION: test_readiness_llm_unhealthy_not_critical_by_default
    # SUMMARY: ADR-008 regression guard: with AGENT_LLM_READINESS_CRITICAL at its default of
    # false, an unreachable LLM provider must not evict a replica — the check still runs and its
    # unhealthy status is still visible in checks.llm, it just does not vote on the verdict.
    @pytest.mark.unit
    async def test_readiness_llm_unhealthy_not_critical_by_default(
        self,
        async_client: AsyncClient,
        fastapi_app: FastAPI,
        test_settings: FixtureSettings,
    ) -> None:
        llm_service = fastapi_app.state.services["llm_service"]
        original_check_readiness = llm_service.check_readiness
        llm_service.check_readiness = AsyncMock(
            return_value={
                "status": "unhealthy",
                "message": "LLM provider probe failed",
                "mode": "probe",
                "provider_reachable": False,
            }
        )

        try:
            with patch(
                "project.infrastructure.api.endpoints.health._check_database",
                return_value={
                    "status": "healthy",
                    "message": "Database connection successful",
                    "response_time_ms": 1.23,
                },
            ):
                response = await async_client.get("/health/ready")
        finally:
            llm_service.check_readiness = original_check_readiness

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["checks"]["llm"]["status"] == "unhealthy"
        assert data["checks"]["llm"]["critical"] is False

    # FUNCTION: test_readiness_llm_unhealthy_critical_when_configured
    # SUMMARY: ADR-008 regression guard: AGENT_LLM_READINESS_CRITICAL=true restores the old
    # behavior on demand — an unhealthy LLM check flips the overall verdict to 503.
    @pytest.mark.unit
    async def test_readiness_llm_unhealthy_critical_when_configured(
        self,
        async_client: AsyncClient,
        fastapi_app: FastAPI,
        test_settings: FixtureSettings,
    ) -> None:
        original_critical = test_settings.agent.llm_readiness_critical
        test_settings.agent.llm_readiness_critical = True

        llm_service = fastapi_app.state.services["llm_service"]
        original_check_readiness = llm_service.check_readiness
        llm_service.check_readiness = AsyncMock(
            return_value={
                "status": "unhealthy",
                "message": "LLM provider probe failed",
                "mode": "probe",
                "provider_reachable": False,
            }
        )

        try:
            with patch(
                "project.infrastructure.api.endpoints.health._check_database",
                return_value={
                    "status": "healthy",
                    "message": "Database connection successful",
                    "response_time_ms": 1.23,
                },
            ):
                response = await async_client.get("/health/ready")
        finally:
            test_settings.agent.llm_readiness_critical = original_critical
            llm_service.check_readiness = original_check_readiness

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["checks"]["llm"]["status"] == "unhealthy"
        assert data["checks"]["llm"]["critical"] is True

    # FUNCTION: test_readiness_llm_healthy_returns_200_when_critical
    # SUMMARY: Verify llm_readiness_critical=true does not itself cause a 503 — only an actually
    # unhealthy LLM check does; a healthy one still returns 200 with the flag visible in the body.
    @pytest.mark.unit
    async def test_readiness_llm_healthy_returns_200_when_critical(
        self,
        async_client: AsyncClient,
        test_settings: FixtureSettings,
    ) -> None:
        original_critical = test_settings.agent.llm_readiness_critical
        test_settings.agent.llm_readiness_critical = True

        try:
            with patch(
                "project.infrastructure.api.endpoints.health._check_database",
                return_value={
                    "status": "healthy",
                    "message": "Database connection successful",
                    "response_time_ms": 1.23,
                },
            ):
                response = await async_client.get("/health/ready")
        finally:
            test_settings.agent.llm_readiness_critical = original_critical

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["checks"]["llm"]["status"] == "healthy"
        assert data["checks"]["llm"]["critical"] is True

    # FUNCTION: test_readiness_database_unhealthy_still_503_regardless_of_llm_criticality
    # SUMMARY: Regression guard for the critical_statuses refactor: the database's own conditional
    # criticality (ADR-006) is untouched by the new, independent llm_readiness_critical flag — a
    # healthy LLM plus llm_readiness_critical=true must not mask a real database fault.
    @pytest.mark.unit
    async def test_readiness_database_unhealthy_still_503_regardless_of_llm_criticality(
        self,
        async_client: AsyncClient,
        test_settings: FixtureSettings,
    ) -> None:
        original_critical = test_settings.agent.llm_readiness_critical
        test_settings.agent.llm_readiness_critical = True

        try:
            with patch(
                "project.infrastructure.api.endpoints.health._check_database",
                return_value={
                    "status": "unhealthy",
                    "message": "Database connection failed",
                    "response_time_ms": None,
                },
            ):
                response = await async_client.get("/health/ready")
        finally:
            test_settings.agent.llm_readiness_critical = original_critical

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["checks"]["database"]["status"] == "unhealthy"
        assert data["checks"]["database"]["critical"] is True

    # FUNCTION: test_check_database_exercises_real_probe_under_budget
    # SUMMARY: Cover the real _run_db_probe body by mocking only the connection pool, not the probe itself. Ensures pool.connection() is awaited as an async context manager and both probe queries run.
    @pytest.mark.unit
    async def test_check_database_exercises_real_probe_under_budget(self) -> None:
        from project.infrastructure.api.endpoints import health as health_module

        mock_conn, mock_pool = _mock_pool_with_migration_state(migrated=True)

        result = await health_module._check_database(pool=mock_pool)

        assert result["status"] == "healthy"
        assert result["message"] == "Database connection successful"
        assert isinstance(result["response_time_ms"], (int, float))
        assert result["response_time_ms"] >= 0

        assert mock_conn.execute.await_count == 2
        # **LOGIC_STEP**: The probe must borrow a connection from the pool with no arguments —
        # a timeout or a keyword slipped in here would change the behaviour under load and the
        # bare call count would not notice.
        mock_pool.connection.assert_called_once_with()

    # FUNCTION: test_check_database_reports_unmigrated_schema
    # SUMMARY: Regression guard: a reachable database with no alembic_version table is not ready. SELECT 1 alone answered "healthy" against an empty database, which is exactly the state a fresh checkout is in before `make migrate`.
    @pytest.mark.unit
    async def test_check_database_reports_unmigrated_schema(self) -> None:
        from project.infrastructure.api.endpoints import health as health_module

        _, mock_pool = _mock_pool_with_migration_state(migrated=False)

        result = await health_module._check_database(pool=mock_pool)

        assert result["status"] == "unhealthy"
        assert "make migrate" in result["message"]

    # FUNCTION: test_check_database_returns_unhealthy_on_timeout
    # SUMMARY: Regression guard: when the inner DB probe stalls past READINESS_DB_TIMEOUT_SECONDS, _check_database must return status='unhealthy' within ~timeout+overhead — never block past the budget. Closes the bug where readiness probe could hang up to system TCP keepalive (~2h).
    @pytest.mark.unit
    async def test_check_database_returns_unhealthy_on_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from project.infrastructure.api.endpoints import health as health_module

        # **LOGIC_STEP**: Use a small timeout for the test so the assertion budget is also small.
        monkeypatch.setattr(health_module, "READINESS_DB_TIMEOUT_SECONDS", 0.1)

        async def hang_probe(pool: object) -> float:
            await asyncio.sleep(5.0)
            return 0.0

        monkeypatch.setattr(health_module, "_run_db_probe", hang_probe)

        start = time.monotonic()
        result = await health_module._check_database(pool=object())  # type: ignore[arg-type]
        elapsed = time.monotonic() - start

        assert result["status"] == "unhealthy"
        assert "timed out" in result["message"]
        # **LOGIC_STEP**: Probe must release within ~timeout + small overhead, not 5s.
        assert elapsed < 1.0, f"probe blocked too long: {elapsed:.2f}s"
