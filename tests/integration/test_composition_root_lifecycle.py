# FILE: tests/integration/test_composition_root_lifecycle.py
# SUMMARY: Smoke test that the assembled FastAPI app responds to /health/ inside a real lifespan.

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from project.core import composition_root
from project.core.config import clear_settings_override, set_settings_override
from project.launcher.main import create_app
from tests.conftest import _FixtureSettings, registered_paths


# A pool that only counts what the lifespan does with it.
class _CountingPool:
    made: list[_CountingPool] = []
    check_connection = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.opened = self.closed = 0
        _CountingPool.made.append(self)

    async def open(self) -> None:
        self.opened += 1

    async def close(self) -> None:
        self.closed += 1


# End-to-end check that CompositionRoot.build_application returns a responsive FastAPI app.
class TestCompositionRootLifecycle:
    # Built the way every worker builds it. The shared `fastapi_app` has no lifespan, so this test
    # once named one it never entered (GPT-6 Astra, 2026-09-27): the pool opens on the way in,
    # serves a request, and closes on the way out.
    @pytest.mark.integration
    async def test_lifecycle_full_loop_health_ok(
        self, test_settings: _FixtureSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(composition_root, "AsyncConnectionPool", _CountingPool)
        monkeypatch.setattr(_CountingPool, "made", [])
        set_settings_override(test_settings)
        try:
            app = create_app()
            async with app.router.lifespan_context(app):
                (pool,) = _CountingPool.made
                assert (pool.opened, pool.closed) == (1, 0)
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get("/health/")
        finally:
            clear_settings_override()

        assert (response.status_code, response.json().get("status")) == (200, "healthy")
        assert (pool.opened, pool.closed) == (1, 1)

    @pytest.mark.integration
    async def test_assembled_app_has_routes(self, fastapi_app: FastAPI) -> None:
        # The kernel ships at minimum /health/ and /health/ready — assert both are wired.
        paths = registered_paths(fastapi_app)
        assert "/health/" in paths
        assert "/health/ready" in paths
