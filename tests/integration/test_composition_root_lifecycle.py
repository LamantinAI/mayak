# FILE: tests/integration/test_composition_root_lifecycle.py
# SUMMARY: Smoke test that the assembled FastAPI app responds to /health/ inside a real lifespan.

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from tests.conftest import registered_paths


# CLASS: tests.integration.test_composition_root_lifecycle.TestCompositionRootLifecycle
# SUMMARY: End-to-end check that CompositionRoot.build_application returns a responsive FastAPI app.
class TestCompositionRootLifecycle:
    # FUNCTION: test_lifecycle_full_loop_health_ok
    @pytest.mark.integration
    async def test_lifecycle_full_loop_health_ok(
        self, fastapi_app: FastAPI, async_client: AsyncClient
    ) -> None:
        response = await async_client.get("/health/")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, dict)
        assert body.get("status") == "healthy"

    # FUNCTION: test_assembled_app_has_routes
    @pytest.mark.integration
    async def test_assembled_app_has_routes(self, fastapi_app: FastAPI) -> None:
        # The kernel ships at minimum /health/ and /health/ready — assert both are wired.
        paths = registered_paths(fastapi_app)
        assert "/health/" in paths
        assert "/health/ready" in paths
