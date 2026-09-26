# FILE: tests/application/test_config.py
# SUMMARY: Unit tests for configuration management and settings validation.

import json
import os
import tomllib
import pytest
from pathlib import Path
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from project.core.composition_root import CompositionRoot
from project.core.config import (
    APP_VERSION,
    ProjectSettings,
    LLMSettings,
    PostgresSettings,
    ServerSettings,
    AgentSettings,
    Settings,
    build_postgres_dsn,
    build_sqlalchemy_postgres_dsn,
    clear_settings_override,
    set_settings_override,
)
from project.core.config_runtime import (
    _ENV_SAMPLE_PATH,
    _PYPROJECT_PATH,
    _UNKNOWN_APP_VERSION,
    _declared_app_version,
    _sample_placeholder_secrets,
)
from tests.conftest import _FixtureSettings as FixtureSettings


def _pool_kwargs(settings: FixtureSettings) -> dict[str, object]:
    set_settings_override(settings)
    try:
        with patch("project.core.composition_root.AsyncConnectionPool") as pool:
            CompositionRoot().build_dependencies()
        return dict(pool.call_args.kwargs)
    finally:
        clear_settings_override()


@pytest.mark.unit
def test_pool_checks_stale_connections(test_settings: FixtureSettings) -> None:
    assert callable(_pool_kwargs(test_settings)["check"])


@pytest.mark.unit
def test_pool_bounds_broken_tcp(test_settings: FixtureSettings) -> None:
    options = _pool_kwargs(test_settings)["kwargs"]
    assert isinstance(options, dict)
    assert options["keepalives"] == 1 and options["tcp_user_timeout"] > 0


@pytest.mark.unit
def test_small_pool_has_valid_minimum(test_settings: FixtureSettings) -> None:
    original = test_settings.postgres.pool_size
    test_settings.postgres.pool_size = 1
    try:
        options = _pool_kwargs(test_settings)
    finally:
        test_settings.postgres.pool_size = original
    assert options["min_size"] == options["max_size"] == 1


class TestProjectSettings:
    # The default name is read from docs/project_context.json, not hardcoded, so a renamed
    # project's fallback (no APP_NAME) stays its own name instead of the template's.
    @pytest.mark.unit
    def test_default_values(self) -> None:
        expected_name = json.loads(
            (Path(__file__).parents[2] / "docs" / "project_context.json").read_text(
                encoding="utf-8"
            )
        )["project_name"]

        with patch.dict(os.environ, {}, clear=True):
            settings = ProjectSettings(_env_file=None)  # type: ignore[call-arg]

        assert settings.name == expected_name
        assert settings.debug is False
        assert 0.0 <= settings.sampling_health_check_rate <= 1.0

    @pytest.mark.unit
    def test_sampling_rate_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            ProjectSettings(sampling_health_check_rate=1.5)

    @pytest.mark.unit
    def test_sampling_rate_negative(self) -> None:
        with pytest.raises(ValidationError):
            ProjectSettings(sampling_health_check_rate=-0.1)

    @pytest.mark.unit
    def test_from_env_vars(self) -> None:
        with patch.dict(os.environ, {"APP_DEBUG": "true", "APP_NAME": "TestApp"}, clear=False):
            settings = ProjectSettings()
            assert settings.debug is True
            assert settings.name == "TestApp"


class TestLLMSettings:
    # Verify raw LLM settings no longer require an API key before runtime validation.
    @pytest.mark.unit
    def test_api_key_defaults_to_empty_string(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = LLMSettings(_env_file=None)  # type: ignore[call-arg]

        assert settings.api_key.get_secret_value() == ""

    @pytest.mark.unit
    def test_valid_settings(self) -> None:
        with patch.dict(
            os.environ,
            {"OPENAI_COMPATIBLE_API_KEY": "sk-test123"},
            clear=True,
        ):
            settings = LLMSettings(_env_file=None)  # type: ignore[call-arg]
            assert settings.api_key.get_secret_value() == "sk-test123"
            assert settings.model == "gpt-3.5-turbo"
            assert settings.base_url is None


class TestPostgresSettings:
    @pytest.mark.unit
    def test_database_url_format(self) -> None:
        settings = PostgresSettings(
            user="myuser",
            password=SecretStr("mypass"),
            db="mydb",
            host="dbhost",
            port=5433,
        )
        url = str(settings.database_url)
        assert "myuser" in url
        assert "mypass" in url
        assert "dbhost" in url
        assert "5433" in url
        assert "mydb" in url
        assert url.startswith("postgresql://")

    @pytest.mark.unit
    def test_special_chars_in_password(self) -> None:
        settings = PostgresSettings(
            user="user",
            password=SecretStr("p@ss:w/rd"),
            db="testdb",
            host="localhost",
            port=5432,
        )
        url = str(settings.database_url)
        # Special chars should be URL-encoded in the resulting URL
        assert "p@ss:w/rd" not in url or "%40" in url or "%3A" in url

    # Verify runtime and Alembic can rely on the same PostgreSQL DSN builder.
    @pytest.mark.unit
    def test_shared_builder_matches_settings_property(self) -> None:
        settings = PostgresSettings(
            user="svc_user",
            password=SecretStr("p@ss:w/rd"),
            db="template_db",
            host="db.internal",
            port=6543,
        )

        shared_url = build_postgres_dsn(
            user=settings.user,
            password=settings.password.get_secret_value(),
            host=settings.host,
            port=settings.port,
            database=settings.db,
        )

        assert str(shared_url) == str(settings.database_url)

    # Verify Alembic/SQLAlchemy use the psycopg v3 URL while runtime keeps libpq-compatible DSNs.
    @pytest.mark.unit
    def test_sqlalchemy_database_url_uses_psycopg_v3_dialect(self) -> None:
        settings = PostgresSettings(
            user="svc_user",
            password=SecretStr("p@ss:w/rd"),
            db="template_db",
            host="db.internal",
            port=6543,
        )

        sqlalchemy_url = build_sqlalchemy_postgres_dsn(
            user=settings.user,
            password=settings.password.get_secret_value(),
            host=settings.host,
            port=settings.port,
            database=settings.db,
        )

        assert str(sqlalchemy_url) == str(settings.sqlalchemy_database_url)
        assert str(settings.sqlalchemy_database_url).startswith("postgresql+psycopg://")
        assert str(settings.database_url).startswith("postgresql://")

    @pytest.mark.unit
    def test_default_values(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = PostgresSettings(_env_file=None)  # type: ignore[call-arg]

        assert settings.user == "postgres"
        assert settings.password.get_secret_value() == "postgres"
        assert settings.db == "app"
        assert settings.host == "localhost"
        assert settings.port == 5432


class TestAgentSettings:
    @pytest.mark.unit
    def test_temperature_range(self) -> None:
        with pytest.raises(ValidationError):
            AgentSettings(default_llm_temperature=3.0)

    @pytest.mark.unit
    def test_max_tokens_positive(self) -> None:
        with pytest.raises(ValidationError):
            AgentSettings(max_tokens=0)

    @pytest.mark.unit
    def test_llm_readiness_timeout_positive(self) -> None:
        with pytest.raises(ValidationError):
            AgentSettings(llm_readiness_timeout_seconds=0)

    @pytest.mark.unit
    def test_llm_mode_accepts_mock(self) -> None:
        settings = AgentSettings(llm_mode="mock")

        assert settings.llm_mode == "mock"

    # Verify the template defaults to mock mode for local and CI friendliness.
    @pytest.mark.unit
    def test_default_llm_mode_is_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AGENT_LLM_MODE", raising=False)
        settings = AgentSettings(_env_file=None)  # type: ignore[call-arg]

        assert settings.llm_mode == "mock"


class TestSettings:
    @pytest.mark.unit
    def test_settings_initializes_all_sections(self, test_settings: FixtureSettings) -> None:
        assert hasattr(test_settings, "project")
        assert hasattr(test_settings, "llm")
        assert hasattr(test_settings, "server")
        assert hasattr(test_settings, "postgres")
        assert hasattr(test_settings, "agent")

    @pytest.mark.unit
    def test_settings_types(self, test_settings: FixtureSettings) -> None:
        assert isinstance(test_settings.project, ProjectSettings)
        assert isinstance(test_settings.llm, LLMSettings)
        assert isinstance(test_settings.server, ServerSettings)
        assert isinstance(test_settings.postgres, PostgresSettings)
        assert isinstance(test_settings.agent, AgentSettings)

    @pytest.mark.unit
    def test_runtime_validation_rejects_wildcard_cors_in_production(self) -> None:
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "test_api_key"}, clear=False):
            settings = Settings()
            settings.project.debug = False
            settings.server.cors_origins = ["*"]
            with pytest.raises(ValueError) as exc_info:
                settings.validate_runtime()
            assert "SERVER_CORS_ORIGINS" in str(exc_info.value)

    # Pins the membership check (`"*" in cors_origins`) in Settings.validate_runtime, not the
    # old `== ["*"]` equality — a future "simplification" back to equality must fail here first.
    @pytest.mark.unit
    def test_runtime_validation_rejects_wildcard_mixed_with_real_origins(self) -> None:
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "test_api_key"}, clear=False):
            settings = Settings()
            settings.project.debug = False
            settings.server.cors_origins = ["https://app.example.com", "*"]
            with pytest.raises(ValueError) as exc_info:
                settings.validate_runtime()
            assert "SERVER_CORS_ORIGINS" in str(exc_info.value)

    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "cors_origins", [["*"], ["https://app.example.com", "*"]], ids=["alone", "mixed"]
    )
    def test_runtime_validation_allows_wildcard_cors_in_debug_mode(
        self, cors_origins: list[str]
    ) -> None:
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "test_api_key"}, clear=False):
            settings = Settings()
            settings.project.debug = True
            settings.server.cors_origins = cors_origins

            settings.validate_runtime()

    # The sibling test above already covers this via the cors_allow_credentials default (True);
    # this one pins the flag explicitly so the case keeps failing if that default ever changes.
    @pytest.mark.unit
    def test_runtime_validation_rejects_wildcard_with_credentials_enabled(self) -> None:
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "test_api_key"}, clear=False):
            settings = Settings()
            settings.project.debug = False
            settings.server.cors_allow_credentials = True
            settings.server.cors_origins = ["*"]
            with pytest.raises(ValueError) as exc_info:
                settings.validate_runtime()
            assert "SERVER_CORS_ORIGINS" in str(exc_info.value)
            assert "SERVER_CORS_ALLOW_CREDENTIALS" in str(exc_info.value)

    @pytest.mark.unit
    def test_runtime_validation_rejects_wildcard_mixed_with_credentials_enabled(self) -> None:
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "test_api_key"}, clear=False):
            settings = Settings()
            settings.project.debug = False
            settings.server.cors_allow_credentials = True
            settings.server.cors_origins = ["https://app.example.com", "*"]
            with pytest.raises(ValueError) as exc_info:
                settings.validate_runtime()
            assert "SERVER_CORS_ORIGINS" in str(exc_info.value)

    # Wildcard CORS is safe only without credentials.
    # no-assert-ok: success means validate_runtime() returns without raising.
    @pytest.mark.unit
    def test_runtime_validation_allows_wildcard_when_credentials_disabled(self) -> None:
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "test_api_key"}, clear=False):
            settings = Settings()
            settings.project.debug = False
            # Settings() reads the ambient .env: without this, the password guard stays quiet
            # locally but fires on a checkout with no .env — it failed CI that way.
            settings.postgres.enabled = False
            settings.server.cors_allow_credentials = False
            settings.server.cors_origins = ["*"]

            settings.validate_runtime()

    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    def test_runtime_validation_allows_explicit_origins_with_credentials_enabled(self) -> None:
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "test_api_key"}, clear=False):
            settings = Settings()
            settings.project.debug = False
            # Same reason as the test above — an assertion that validate_runtime()
            # returns has to neutralise every guard it is not about.
            settings.postgres.enabled = False
            settings.server.cors_allow_credentials = True
            settings.server.cors_origins = ["https://app.example.com"]

            settings.validate_runtime()

    # Verify SERVER_CORS_ALLOW_CREDENTIALS=false is not just read but actually changes the
    # CORSMiddleware behaviour — the settings object alone proves nothing about the response.
    @pytest.mark.unit
    async def test_cors_allow_credentials_setting_reaches_the_middleware(self) -> None:
        settings = FixtureSettings()
        settings.server.cors_origins = ["https://app.example.com"]
        settings.server.cors_allow_credentials = False

        set_settings_override(settings)
        try:
            app = CompositionRoot().build_application()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/health/", headers={"Origin": "https://app.example.com"}
                )
        finally:
            clear_settings_override()

        assert response.status_code == 200
        assert "access-control-allow-credentials" not in response.headers

    # Proves the wiring, not the guard: CompositionRoot must actually pass cors_origins to
    # CORSMiddleware, not a hardcoded list — validate_runtime() alone does not prove that.
    @pytest.mark.unit
    async def test_only_configured_origins_are_echoed_by_the_middleware(self) -> None:
        settings = FixtureSettings()
        settings.server.cors_origins = ["https://app.example.com"]
        settings.server.cors_allow_credentials = True

        set_settings_override(settings)
        try:
            app = CompositionRoot().build_application()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                configured = await client.get(
                    "/health/", headers={"Origin": "https://app.example.com"}
                )
                stranger = await client.get(
                    "/health/", headers={"Origin": "https://evil.attacker.test"}
                )
        finally:
            clear_settings_override()

        assert configured.status_code == 200
        assert configured.headers["access-control-allow-origin"] == "https://app.example.com"
        assert "access-control-allow-origin" not in stranger.headers

    # Preflight (OPTIONS with Access-Control-Request-Method) exercises a CORSMiddleware code
    # path a plain GET never reaches, so a narrowed allow_methods could break it unnoticed.
    @pytest.mark.unit
    async def test_a_preflight_answers_for_the_methods_the_application_serves(self) -> None:
        settings = FixtureSettings()
        settings.server.cors_origins = ["https://app.example.com"]

        set_settings_override(settings)
        try:
            app = CompositionRoot().build_application()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                preflight = await client.options(
                    "/health/",
                    headers={
                        "Origin": "https://app.example.com",
                        "Access-Control-Request-Method": "GET",
                    },
                )
        finally:
            clear_settings_override()

        assert preflight.status_code == 200
        assert "GET" in preflight.headers["access-control-allow-methods"]

    @pytest.mark.unit
    def test_runtime_validation_requires_api_key_in_live_mode(self) -> None:
        local_settings = FixtureSettings()
        local_settings.agent.llm_mode = "live"
        local_settings.llm.api_key = SecretStr("")

        with pytest.raises(ValueError) as exc_info:
            local_settings.validate_runtime()

        assert "AGENT_LLM_MODE=live" in str(exc_info.value)

    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    def test_runtime_validation_allows_mock_mode_without_api_key(self) -> None:
        local_settings = FixtureSettings()
        local_settings.agent.llm_mode = "mock"
        local_settings.llm.api_key = SecretStr("")

        local_settings.validate_runtime()

    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    def test_runtime_validation_accepts_debug_defaults(
        self, test_settings: FixtureSettings
    ) -> None:
        test_settings.validate_runtime()


# Production startup must refuse the credential placeholders .env.sample ships; the guard
# compares against that file directly, so it cannot drift from the actual placeholder.
class TestPlaceholderCredentialsAreRefusedInProduction:
    @staticmethod
    def _production_settings() -> FixtureSettings:
        settings = FixtureSettings()
        settings.project.debug = False
        settings.server.cors_origins = ["https://example.com"]
        settings.postgres.enabled = True
        settings.agent.llm_mode = "mock"
        return settings

    @pytest.mark.unit
    def test_sample_file_exposes_the_password_placeholder(self) -> None:
        placeholders = _sample_placeholder_secrets()

        assert "POSTGRES_PASSWORD" in placeholders
        assert placeholders["POSTGRES_PASSWORD"].strip() != ""
        # The sample leaves the provider key blank. An empty value is not a
        # placeholder — treating it as one would make every unset credential look like a leak.
        assert "OPENAI_COMPATIBLE_API_KEY" not in placeholders

    @pytest.mark.unit
    def test_production_refuses_the_sample_password(self) -> None:
        settings = self._production_settings()
        settings.postgres.password = SecretStr(_sample_placeholder_secrets()["POSTGRES_PASSWORD"])

        with pytest.raises(ValueError) as exc_info:
            settings.validate_runtime()

        assert "POSTGRES_PASSWORD" in str(exc_info.value)

    # Verify the literal "postgres" stays rejected — it is not in the sample file.
    @pytest.mark.unit
    def test_production_still_refuses_the_image_default_password(self) -> None:
        settings = self._production_settings()
        settings.postgres.password = SecretStr("postgres")

        with pytest.raises(ValueError) as exc_info:
            settings.validate_runtime()

        assert "POSTGRES_PASSWORD" in str(exc_info.value)

    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    def test_production_accepts_a_real_password(self) -> None:
        settings = self._production_settings()
        settings.postgres.password = SecretStr("Zh8-quiet-harbour-91")

        settings.validate_runtime()

    @pytest.mark.unit
    def test_missing_sample_file_does_not_stop_the_service(self, tmp_path: Path) -> None:
        # The check exists to catch a forgotten placeholder, not to become a new
        # reason a service cannot boot. With no file to compare against it has nothing to say.
        with patch("project.core.config_runtime._ENV_SAMPLE_PATH", tmp_path / "absent.env.sample"):
            assert _sample_placeholder_secrets() == {}

            settings = self._production_settings()
            settings.postgres.password = SecretStr("your_postgres_password")
            settings.validate_runtime()

    @pytest.mark.unit
    def test_provider_key_placeholder_is_refused_when_the_sample_grows_one(
        self, tmp_path: Path
    ) -> None:
        sample = tmp_path / ".env.sample"
        sample.write_text(
            "# comment\nAPP_DEBUG=false\nOPENAI_COMPATIBLE_API_KEY=your_api_key_here\n",
            encoding="utf-8",
        )

        with patch("project.core.config_runtime._ENV_SAMPLE_PATH", sample):
            settings = self._production_settings()
            settings.postgres.password = SecretStr("Zh8-quiet-harbour-91")
            settings.llm.api_key = SecretStr("your_api_key_here")
            settings.agent.llm_mode = "live"

            with pytest.raises(ValueError) as exc_info:
                settings.validate_runtime()

        assert "OPENAI_COMPATIBLE_API_KEY" in str(exc_info.value)

    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    def test_the_provider_key_rule_leaves_mock_mode_alone(self, tmp_path: Path) -> None:
        # The provider-key rule is gated like the password rule — refusing to start over a
        # credential mock mode never reads would just teach people to ignore the guard.
        sample = tmp_path / ".env.sample"
        sample.write_text("OPENAI_COMPATIBLE_API_KEY=your_api_key_here\n", encoding="utf-8")

        with patch("project.core.config_runtime._ENV_SAMPLE_PATH", sample):
            settings = self._production_settings()
            settings.postgres.password = SecretStr("Zh8-quiet-harbour-91")
            settings.llm.api_key = SecretStr("your_api_key_here")
            settings.agent.llm_mode = "mock"

            settings.validate_runtime()

    @pytest.mark.unit
    def test_sample_path_points_at_the_repository_file(self) -> None:
        assert _ENV_SAMPLE_PATH == Path(__file__).resolve().parents[2] / ".env.sample"
        assert _ENV_SAMPLE_PATH.is_file()


# APP_VERSION must be read from pyproject.toml, not a literal, so bumping the version in one
# place actually changes what /health/, the OpenAPI document, and startup logs report.
class TestAppVersionFollowsTheManifest:
    @pytest.mark.unit
    def test_app_version_equals_the_declared_version(self) -> None:
        manifest = Path(__file__).resolve().parents[2] / "pyproject.toml"
        declared = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]["version"]

        assert APP_VERSION == declared

    @pytest.mark.unit
    def test_manifest_path_resolves_from_the_module_not_the_working_directory(self) -> None:
        # The image is built with --no-install-project, so this module-relative path is the
        # only way the version metadata reaches production — a relative path resolves differently.
        assert _PYPROJECT_PATH == Path(__file__).resolve().parents[2] / "pyproject.toml"
        assert _PYPROJECT_PATH.is_file()

    @pytest.mark.unit
    def test_unreadable_manifest_degrades_instead_of_killing_the_import(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # This runs at import time of the settings module; raising here would
        # take the whole application down over a display string.
        monkeypatch.setattr("project.core.config_runtime._PYPROJECT_PATH", tmp_path / "absent.toml")

        assert _declared_app_version() == _UNKNOWN_APP_VERSION

    # A corrupted UTF-8 manifest raises UnicodeDecodeError; version is read at import time, so
    # an uncaught error would take the app down over a display string instead of degrading.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "shape",
        ["absent", "is_a_directory", "invalid_toml", "no_version_key", "wrong_type", "bad_bytes"],
    )
    def test_every_way_a_manifest_can_be_unusable_degrades_the_same_way(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shape: str
    ) -> None:
        manifest = tmp_path / "pyproject.toml"
        if shape == "is_a_directory":
            manifest.mkdir()
        elif shape == "invalid_toml":
            manifest.write_text("[project\nname =", encoding="utf-8")
        elif shape == "no_version_key":
            manifest.write_text('[project]\nname = "x"\n', encoding="utf-8")
        elif shape == "wrong_type":
            manifest.write_text('project = "not a table"\n', encoding="utf-8")
        elif shape == "bad_bytes":
            # Exercise real corrupted bytes, not a mocked decoder.
            manifest.write_bytes(b'[project]\nversion = "1.0.0"\nname = "\xff\xfe"\n')
        monkeypatch.setattr("project.core.config_runtime._PYPROJECT_PATH", manifest)

        assert _declared_app_version() == _UNKNOWN_APP_VERSION
