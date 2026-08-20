# FILE: tests/conftest.py
# SUMMARY: Global test configuration and fixtures for the FastAPI backend test suite.

import pytest
import os
from typing import Any, AsyncGenerator, Generator
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI

from project.core.composition_root import CompositionRoot
from project.core.config import (
    Settings,
    clear_settings_override,
    get_settings,
    set_settings_override,
)
from pydantic import SecretStr

from project.core.logging import setup_logging, get_logger

# Functional tests run inside Docker only; exclude from local collection.
collect_ignore_glob = ["functional/*"]

# Setup logging for tests to ensure logs are captured but don't spam stdout unless failed
setup_logging(level="DEBUG", force=True)
logger = get_logger(__name__)


# CLASS: tests.conftest._FixtureSettings
# SUMMARY: Test-specific settings override for isolated test environments.
# EXTENDS: project.core.config.Settings
class _FixtureSettings(Settings):
    # FUNCTION: __init__
    # SUMMARY: Initializes test settings with safe defaults.
    def __init__(self) -> None:
        # **LOGIC_STEP**: Set required env vars before base init to satisfy validation.
        os.environ.setdefault("OPENAI_COMPATIBLE_API_KEY", "test_api_key")
        # **LOGIC_STEP**: Initialize base settings.
        super().__init__()
        # **LOGIC_STEP**: Run tests on the production path. With debug=True, Starlette answers
        # unhandled exceptions from its own ServerErrorMiddleware and the app's registered
        # Exception handler is never reached — so the whole suite silently exercised a code path
        # that production does not use, and validate_runtime()'s CORS/password guards were off too.
        self.project.debug = False

        # Server config
        self.server.host = "127.0.0.1"
        self.server.port = 8000
        self.server.workers = 1
        # **LOGIC_STEP**: A concrete origin, because validate_runtime() rejects the wildcard
        # once debug is off — which is exactly the guard this suite should be running under.
        self.server.cors_origins = ["http://127.0.0.1:8000"]

        # LLM config
        self.llm.api_key = SecretStr("test_api_key")
        self.llm.model = "test-model"
        self.agent.llm_mode = "mock"
        self.agent.llm_readiness_check_mode = "init"

        # Database config
        # **LOGIC_STEP**: Pin the toggle explicitly. Settings read the developer's own .env, so
        # without this line a checkout running with POSTGRES_ENABLED=false makes every test that
        # exercises the database-enabled path silently assert the opposite of what it claims.
        self.postgres.enabled = True
        self.postgres.user = "test_user"
        self.postgres.password = SecretStr("test_password")
        self.postgres.db = "test_db"
        self.postgres.host = "localhost"
        self.postgres.port = 5432


# FUNCTION: test_settings
# SUMMARY: Provide test-specific settings for isolated test environments.
# OUTPUT: (_FixtureSettings): Test settings instance.
@pytest.fixture(scope="session")
def test_settings() -> _FixtureSettings:
    # **LOGIC_STEP**: Return test settings instance.
    return _FixtureSettings()


# FUNCTION: fastapi_app
# SUMMARY: Provide a FastAPI application instance for testing with mocked settings.
# INPUT: test_settings (_FixtureSettings): Test settings fixture.
# OUTPUT: (FastAPI): FastAPI application instance.
@pytest.fixture(scope="function")
def fastapi_app(test_settings: _FixtureSettings) -> Generator[FastAPI, None, None]:
    # **LOGIC_STEP**: Install one canonical settings override used by all runtime modules.
    set_settings_override(test_settings)
    try:
        # **LOGIC_STEP**: Build FastAPI application with test settings.
        composition_root = CompositionRoot()
        app = composition_root.build_application()

        # **LOGIC_STEP**: Also override dependency injection for endpoints.
        app.dependency_overrides[get_settings] = lambda: test_settings

        yield app
    finally:
        clear_settings_override()


# FUNCTION: app_without_postgres
# SUMMARY: Assemble the application with the relational store switched off.
# OUTPUT: (Generator[FastAPI, None, None]): Application built with postgres.enabled False.
# NOTE: Here rather than in a vertical's own test file, which is where it lived until 2026-08-14 —
# in two files at once, in two shapes. Every storage-backed vertical needs it to prove its half of
# ADR-006 (routes absent, service key present and None), so a project's second vertical copied it
# and the day the shipped vertical was deleted its copy went with it. Takes no arguments and builds
# its own application, so it neither collides with `fastapi_app` nor depends on it.
@pytest.fixture(scope="function")
def app_without_postgres() -> Generator[FastAPI, None, None]:
    settings = _FixtureSettings()
    settings.postgres.enabled = False
    set_settings_override(settings)
    try:
        yield CompositionRoot().build_application()
    finally:
        clear_settings_override()


# FUNCTION: async_client
# SUMMARY: Provide an AsyncClient for HTTP testing of FastAPI endpoints.
# INPUT: fastapi_app (FastAPI): FastAPI application fixture.
# OUTPUT: (AsyncGenerator[AsyncClient, None]): AsyncClient instance.
@pytest.fixture(scope="function")
async def async_client(fastapi_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    # **LOGIC_STEP**: Create AsyncClient with ASGITransport.
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# FUNCTION: pin_postgres_toggle
# SUMMARY: Pin POSTGRES_ENABLED for the whole suite so tests never depend on the developer's .env.
# OUTPUT: (Generator[None, None, None]): Yields with the variable set to the enabled default.
@pytest.fixture(autouse=True)
def pin_postgres_toggle(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    # **LOGIC_STEP**: Settings and the migration gate both read POSTGRES_ENABLED from the
    # environment and from .env. Without pinning it here, a checkout that declares no relational
    # store makes every test of the database-enabled path assert the opposite of its own name —
    # and the CI leg that runs the suite with the database off fails for the wrong reason.
    # Tests that want the disabled path override this with their own monkeypatch.setenv.
    monkeypatch.setenv("POSTGRES_ENABLED", "true")
    yield


# FUNCTION: log_capture
# SUMMARY: Capture log messages for verifying logging behavior in tests.
# OUTPUT: (Generator[list, None, None]): List that accumulates log entries.
@pytest.fixture
def log_capture() -> Generator[list, None, None]:
    # **LOGIC_STEP**: Create list to capture log messages.
    captured_logs = []

    # **LOGIC_STEP**: Mock the underlying logger methods.
    with patch("project.core.logging.logger.SemanticLogger.log_event") as mock_log:

        def side_effect(event_type: str, msg: str, *args: Any, **kwargs: Any) -> None:
            captured_logs.append(
                {"event_type": event_type, "msg": msg, "args": args, "kwargs": kwargs}
            )

        mock_log.side_effect = side_effect
        yield captured_logs


# FUNCTION: registered_paths
# SUMMARY: List the paths an assembled application actually serves.
# INPUT: app (FastAPI): Application built by CompositionRoot.
# OUTPUT: (set[str]): Path templates, e.g. {"/health/", "/reference-tasks/{task_id}"}.
# NOTE: Tests used to read `app.routes` directly. Starlette 1.x stopped flattening included
# routers into that list — `include_router` now leaves an opaque `_IncludedRouter` entry with no
# `path`, so the same comprehension silently returned only the docs routes and an assertion that
# a vertical is registered started failing while the endpoint served fine. The OpenAPI document
# is the application's own answer to "what do I serve" and is public API, so it cannot drift the
# same way. Routes hidden with include_in_schema=False are invisible here; the kernel ships none.
def registered_paths(app: FastAPI) -> set[str]:
    return set(app.openapi()["paths"])
