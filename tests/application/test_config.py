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


# CLASS: tests.application.test_config.TestProjectSettings
# SUMMARY: Test suite for ProjectSettings validation.
class TestProjectSettings:
    # FUNCTION: test_default_values
    # SUMMARY: Verify ProjectSettings has correct defaults.
    # NOTE: The name is read from docs/project_context.json rather than written here. It used to
    # be the literal "Mayak", so renaming a project built from this template failed a kernel test
    # about configuration defaults — and the fix looked like editing a test that had nothing to do
    # with the rename. The assertion is also stronger this way: the fallback name a deployment
    # without APP_NAME serves must be this project's own.
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

    # FUNCTION: test_sampling_rate_out_of_range
    # SUMMARY: Verify sampling rate rejects values outside 0.0-1.0.
    @pytest.mark.unit
    def test_sampling_rate_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            ProjectSettings(sampling_health_check_rate=1.5)

    # FUNCTION: test_sampling_rate_negative
    # SUMMARY: Verify sampling rate rejects negative values.
    @pytest.mark.unit
    def test_sampling_rate_negative(self) -> None:
        with pytest.raises(ValidationError):
            ProjectSettings(sampling_health_check_rate=-0.1)

    # FUNCTION: test_from_env_vars
    # SUMMARY: Verify ProjectSettings reads from APP_ prefixed env vars.
    @pytest.mark.unit
    def test_from_env_vars(self) -> None:
        with patch.dict(os.environ, {"APP_DEBUG": "true", "APP_NAME": "TestApp"}, clear=False):
            settings = ProjectSettings()
            assert settings.debug is True
            assert settings.name == "TestApp"


# CLASS: tests.application.test_config.TestLLMSettings
# SUMMARY: Test suite for LLMSettings validation.
class TestLLMSettings:
    # FUNCTION: test_api_key_defaults_to_empty_string
    # SUMMARY: Verify raw LLM settings no longer require an API key before runtime validation.
    @pytest.mark.unit
    def test_api_key_defaults_to_empty_string(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = LLMSettings(_env_file=None)  # type: ignore[call-arg]

        assert settings.api_key.get_secret_value() == ""

    # FUNCTION: test_valid_settings
    # SUMMARY: Verify LLMSettings accepts valid configuration.
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


# CLASS: tests.application.test_config.TestPostgresSettings
# SUMMARY: Test suite for PostgresSettings validation and URL generation.
class TestPostgresSettings:
    # FUNCTION: test_database_url_format
    # SUMMARY: Verify database_url is constructed correctly from components.
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

    # FUNCTION: test_special_chars_in_password
    # SUMMARY: Verify special characters in password are URL-encoded.
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

    # FUNCTION: test_shared_builder_matches_settings_property
    # SUMMARY: Verify runtime and Alembic can rely on the same PostgreSQL DSN builder.
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

    # FUNCTION: test_sqlalchemy_database_url_uses_psycopg_v3_dialect
    # SUMMARY: Verify Alembic/SQLAlchemy use the psycopg v3 URL while runtime keeps libpq-compatible DSNs.
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

    # FUNCTION: test_default_values
    # SUMMARY: Verify PostgresSettings has correct defaults.
    @pytest.mark.unit
    def test_default_values(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = PostgresSettings(_env_file=None)  # type: ignore[call-arg]

        assert settings.user == "postgres"
        assert settings.password.get_secret_value() == "postgres"
        assert settings.db == "app"
        assert settings.host == "localhost"
        assert settings.port == 5432


# CLASS: tests.application.test_config.TestAgentSettings
# SUMMARY: Test suite for AgentSettings validation.
class TestAgentSettings:
    # FUNCTION: test_temperature_range
    # SUMMARY: Verify temperature rejects out of range values.
    @pytest.mark.unit
    def test_temperature_range(self) -> None:
        with pytest.raises(ValidationError):
            AgentSettings(default_llm_temperature=3.0)

    # FUNCTION: test_max_tokens_positive
    # SUMMARY: Verify max_tokens rejects zero or negative values.
    @pytest.mark.unit
    def test_max_tokens_positive(self) -> None:
        with pytest.raises(ValidationError):
            AgentSettings(max_tokens=0)

    # FUNCTION: test_llm_readiness_timeout_positive
    # SUMMARY: Verify LLM readiness timeout rejects zero or negative values.
    @pytest.mark.unit
    def test_llm_readiness_timeout_positive(self) -> None:
        with pytest.raises(ValidationError):
            AgentSettings(llm_readiness_timeout_seconds=0)

    # FUNCTION: test_llm_mode_accepts_mock
    # SUMMARY: Verify agent settings allow switching to deterministic mock mode.
    @pytest.mark.unit
    def test_llm_mode_accepts_mock(self) -> None:
        settings = AgentSettings(llm_mode="mock")

        assert settings.llm_mode == "mock"

    # FUNCTION: test_default_llm_mode_is_mock
    # SUMMARY: Verify the template defaults to mock mode for local and CI friendliness.
    @pytest.mark.unit
    def test_default_llm_mode_is_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AGENT_LLM_MODE", raising=False)
        settings = AgentSettings(_env_file=None)  # type: ignore[call-arg]

        assert settings.llm_mode == "mock"


# CLASS: tests.application.test_config.TestSettings
# SUMMARY: Test suite for the composite Settings class.
class TestSettings:
    # FUNCTION: test_settings_initializes_all_sections
    # SUMMARY: Verify Settings creates all configuration sections.
    @pytest.mark.unit
    def test_settings_initializes_all_sections(self, test_settings: FixtureSettings) -> None:
        assert hasattr(test_settings, "project")
        assert hasattr(test_settings, "llm")
        assert hasattr(test_settings, "server")
        assert hasattr(test_settings, "postgres")
        assert hasattr(test_settings, "agent")

    # FUNCTION: test_settings_types
    # SUMMARY: Verify each settings section is the correct type.
    @pytest.mark.unit
    def test_settings_types(self, test_settings: FixtureSettings) -> None:
        assert isinstance(test_settings.project, ProjectSettings)
        assert isinstance(test_settings.llm, LLMSettings)
        assert isinstance(test_settings.server, ServerSettings)
        assert isinstance(test_settings.postgres, PostgresSettings)
        assert isinstance(test_settings.agent, AgentSettings)

    # FUNCTION: test_runtime_validation_rejects_wildcard_cors_in_production
    # SUMMARY: Verify production runtime validation rejects wildcard CORS.
    @pytest.mark.unit
    def test_runtime_validation_rejects_wildcard_cors_in_production(self) -> None:
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "test_api_key"}, clear=False):
            settings = Settings()
            settings.project.debug = False
            settings.server.cors_origins = ["*"]
            with pytest.raises(ValueError) as exc_info:
                settings.validate_runtime()
            assert "SERVER_CORS_ORIGINS" in str(exc_info.value)

    # FUNCTION: test_runtime_validation_rejects_wildcard_mixed_with_real_origins
    # SUMMARY: Verify a wildcard mixed into a list of real origins is caught, not only ["*"] alone.
    # NOTE: Pins the membership check (`"*" in self.server.cors_origins`) in
    # Settings.validate_runtime (project/core/config_runtime.py) so a future "simplification" back
    # to the old `== ["*"]` equality check fails here first. Why membership and not equality, the
    # Starlette mechanism it guards against, and the reproduction with its date all live in that
    # comment — not repeated here.
    @pytest.mark.unit
    def test_runtime_validation_rejects_wildcard_mixed_with_real_origins(self) -> None:
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "test_api_key"}, clear=False):
            settings = Settings()
            settings.project.debug = False
            settings.server.cors_origins = ["https://app.example.com", "*"]
            with pytest.raises(ValueError) as exc_info:
                settings.validate_runtime()
            assert "SERVER_CORS_ORIGINS" in str(exc_info.value)

    # FUNCTION: test_runtime_validation_allows_wildcard_cors_in_debug_mode
    # SUMMARY: Verify a wildcard origin still passes when APP_DEBUG=true, mixed-in or alone.
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

    # FUNCTION: test_runtime_validation_rejects_wildcard_with_credentials_enabled
    # SUMMARY: Verify the vulnerability (wildcard + credentials) is still refused explicitly.
    # NOTE: test_runtime_validation_rejects_wildcard_cors_in_production already covers this via the
    # cors_allow_credentials default (True) — this test pins the flag explicitly so the case keeps
    # failing even if that default ever changes.
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

    # FUNCTION: test_runtime_validation_rejects_wildcard_mixed_with_credentials_enabled
    # SUMMARY: Verify a wildcard mixed into a real origin list is still refused when credentials
    # are enabled — the membership check and the credentials gate compose correctly.
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

    # FUNCTION: test_runtime_validation_allows_wildcard_when_credentials_disabled
    # SUMMARY: Verify the newly legitimate configuration passes: a wildcard is safe once no
    # credential ever rides on the response.
    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    def test_runtime_validation_allows_wildcard_when_credentials_disabled(self) -> None:
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "test_api_key"}, clear=False):
            settings = Settings()
            settings.project.debug = False
            # **LOGIC_STEP**: Declare no relational store, because this test asserts that
            # validate_runtime() RETURNS, and with debug off every other guard in that method is
            # live too. The placeholder-password guard is the one that bites: `Settings()` reads
            # the ambient .env, so on a developer machine POSTGRES_PASSWORD holds the real value
            # `make init-project` generated and the guard stays quiet, while on a fresh checkout
            # with no .env it falls back to the driver default and the guard fires. Measured
            # 2026-08-24: this test and the one below passed locally and failed three CI jobs for
            # exactly that reason. Reproduce either state with `mv .env /tmp/ && pytest …`.
            # The sibling refusal tests never hit this because they assert a raise, and the
            # debug-mode test never hits it because the guard is gated on `not debug`.
            settings.postgres.enabled = False
            settings.server.cors_allow_credentials = False
            settings.server.cors_origins = ["*"]

            settings.validate_runtime()

    # FUNCTION: test_runtime_validation_allows_explicit_origins_with_credentials_enabled
    # SUMMARY: Verify explicit origins with credentials on is not a false positive of the guard.
    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    def test_runtime_validation_allows_explicit_origins_with_credentials_enabled(self) -> None:
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "test_api_key"}, clear=False):
            settings = Settings()
            settings.project.debug = False
            # **LOGIC_STEP**: Same reason as the test above — an assertion that validate_runtime()
            # returns has to neutralise every guard it is not about.
            settings.postgres.enabled = False
            settings.server.cors_allow_credentials = True
            settings.server.cors_origins = ["https://app.example.com"]

            settings.validate_runtime()

    # FUNCTION: test_cors_allow_credentials_setting_reaches_the_middleware
    # SUMMARY: Verify SERVER_CORS_ALLOW_CREDENTIALS=false is not just read but actually changes the
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

    # FUNCTION: test_only_configured_origins_are_echoed_by_the_middleware
    # SUMMARY: Verify the configured origin LIST is what CORSMiddleware answers with, so a
    # deployment that lists its own origins cannot be silently serving every origin instead.
    # NOTE: This is the wiring, not the guard. Settings.validate_runtime() refuses a wildcard in
    # `settings.server.cors_origins`, and the tests above prove that refusal — but the guard reads
    # the settings object, and nothing read what CompositionRoot actually handed to CORSMiddleware.
    # Measured on 2026-09-02: replacing `allow_origins=settings.server.cors_origins` with a literal
    # `["*"]` in composition_root.py left `STRICT_GENERATED=1 make quality-gates-steps` at exit 0,
    # every test green, while a request carrying `Origin: https://evil.attacker.test` came back
    # with that origin echoed and `access-control-allow-credentials: true` — the credentialed
    # wildcard the guard exists to prevent, reached by bypassing the setting the guard checks.
    # `grep -rn access-control-allow-origin tests/` was empty before this test; the second request
    # below is what makes the mutation red.
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

    # FUNCTION: test_a_preflight_answers_for_the_methods_the_application_serves
    # SUMMARY: Verify the preflight branch of CORSMiddleware answers, which a plain GET never
    # reaches — so a narrowed allow_methods cannot break every browser client unnoticed.
    # NOTE: Added after an independent review of the test above showed its blind spot: Starlette
    # applies `allow_methods` only to an OPTIONS request carrying Access-Control-Request-Method, so
    # narrowing the list to ["POST"] left all 836 tests green while a browser's preflight for a GET
    # would have received 400. The origin assertion above and this one are the two halves of "the
    # CORS configuration reaches the middleware".
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

    # FUNCTION: test_runtime_validation_requires_api_key_in_live_mode
    # SUMMARY: Verify live LLM mode rejects missing provider credentials.
    @pytest.mark.unit
    def test_runtime_validation_requires_api_key_in_live_mode(self) -> None:
        local_settings = FixtureSettings()
        local_settings.agent.llm_mode = "live"
        local_settings.llm.api_key = SecretStr("")

        with pytest.raises(ValueError) as exc_info:
            local_settings.validate_runtime()

        assert "AGENT_LLM_MODE=live" in str(exc_info.value)

    # FUNCTION: test_runtime_validation_allows_mock_mode_without_api_key
    # SUMMARY: Verify mock mode can run without provider credentials.
    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    def test_runtime_validation_allows_mock_mode_without_api_key(self) -> None:
        local_settings = FixtureSettings()
        local_settings.agent.llm_mode = "mock"
        local_settings.llm.api_key = SecretStr("")

        local_settings.validate_runtime()

    # FUNCTION: test_runtime_validation_accepts_debug_defaults
    # SUMMARY: Verify debug settings can pass runtime validation with local defaults.
    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    def test_runtime_validation_accepts_debug_defaults(
        self, test_settings: FixtureSettings
    ) -> None:
        test_settings.validate_runtime()


# CLASS: tests.application.test_config.TestPlaceholderCredentialsAreRefusedInProduction
# SUMMARY: Verify production startup refuses the credential placeholders .env.sample ships.
# NOTE: The password check compared against the literal "postgres" and nothing else. Measured:
# `your_postgres_password` — this repository's own sample value — along with `changeme` and
# `password`, all started in production silently. The comparison now reads the
# sample file, so the guard cannot drift away from the placeholder it is meant to catch.
class TestPlaceholderCredentialsAreRefusedInProduction:
    # FUNCTION: _production_settings
    # SUMMARY: Build a settings object that looks like production with PostgreSQL enabled.
    @staticmethod
    def _production_settings() -> FixtureSettings:
        settings = FixtureSettings()
        settings.project.debug = False
        settings.server.cors_origins = ["https://example.com"]
        settings.postgres.enabled = True
        settings.agent.llm_mode = "mock"
        return settings

    # FUNCTION: test_sample_file_exposes_the_password_placeholder
    # SUMMARY: Verify the parser finds the credential placeholders the sample actually ships.
    @pytest.mark.unit
    def test_sample_file_exposes_the_password_placeholder(self) -> None:
        placeholders = _sample_placeholder_secrets()

        assert "POSTGRES_PASSWORD" in placeholders
        assert placeholders["POSTGRES_PASSWORD"].strip() != ""
        # **LOGIC_STEP**: The sample leaves the provider key blank. An empty value is not a
        # placeholder — treating it as one would make every unset credential look like a leak.
        assert "OPENAI_COMPATIBLE_API_KEY" not in placeholders

    # FUNCTION: test_production_refuses_the_sample_password
    # SUMMARY: Verify the exact value shipped in .env.sample cannot start a production service.
    @pytest.mark.unit
    def test_production_refuses_the_sample_password(self) -> None:
        settings = self._production_settings()
        settings.postgres.password = SecretStr(_sample_placeholder_secrets()["POSTGRES_PASSWORD"])

        with pytest.raises(ValueError) as exc_info:
            settings.validate_runtime()

        assert "POSTGRES_PASSWORD" in str(exc_info.value)

    # FUNCTION: test_production_still_refuses_the_image_default_password
    # SUMMARY: Verify the literal "postgres" stays rejected — it is not in the sample file.
    @pytest.mark.unit
    def test_production_still_refuses_the_image_default_password(self) -> None:
        settings = self._production_settings()
        settings.postgres.password = SecretStr("postgres")

        with pytest.raises(ValueError) as exc_info:
            settings.validate_runtime()

        assert "POSTGRES_PASSWORD" in str(exc_info.value)

    # FUNCTION: test_production_accepts_a_real_password
    # SUMMARY: Verify a password that is neither the sample value nor the image default passes.
    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    def test_production_accepts_a_real_password(self) -> None:
        settings = self._production_settings()
        settings.postgres.password = SecretStr("Zh8-quiet-harbour-91")

        settings.validate_runtime()

    # FUNCTION: test_missing_sample_file_does_not_stop_the_service
    # SUMMARY: Verify an image without .env.sample starts instead of failing closed.
    @pytest.mark.unit
    def test_missing_sample_file_does_not_stop_the_service(self, tmp_path: Path) -> None:
        # **LOGIC_STEP**: The check exists to catch a forgotten placeholder, not to become a new
        # reason a service cannot boot. With no file to compare against it has nothing to say.
        with patch("project.core.config_runtime._ENV_SAMPLE_PATH", tmp_path / "absent.env.sample"):
            assert _sample_placeholder_secrets() == {}

            settings = self._production_settings()
            settings.postgres.password = SecretStr("your_postgres_password")
            settings.validate_runtime()

    # FUNCTION: test_provider_key_placeholder_is_refused_when_the_sample_grows_one
    # SUMMARY: Verify the same rule covers the provider credential, not only the password.
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

    # FUNCTION: test_the_provider_key_rule_leaves_mock_mode_alone
    # SUMMARY: Verify a deployment that never calls the provider is not blocked by its credential.
    # no-assert-ok: the assertion is that validate_runtime() does not raise; it returns None.
    @pytest.mark.unit
    def test_the_provider_key_rule_leaves_mock_mode_alone(self, tmp_path: Path) -> None:
        # **LOGIC_STEP**: Same gating as the password check, which fires only when postgres.enabled.
        # Refusing to start over a credential nothing reads is the pointless ceremony that teaches
        # people to ignore guards.
        sample = tmp_path / ".env.sample"
        sample.write_text("OPENAI_COMPATIBLE_API_KEY=your_api_key_here\n", encoding="utf-8")

        with patch("project.core.config_runtime._ENV_SAMPLE_PATH", sample):
            settings = self._production_settings()
            settings.postgres.password = SecretStr("Zh8-quiet-harbour-91")
            settings.llm.api_key = SecretStr("your_api_key_here")
            settings.agent.llm_mode = "mock"

            settings.validate_runtime()

    # FUNCTION: test_sample_path_points_at_the_repository_file
    # SUMMARY: Verify the resolved path is the sample this repository ships, not a stray file.
    @pytest.mark.unit
    def test_sample_path_points_at_the_repository_file(self) -> None:
        assert _ENV_SAMPLE_PATH == Path(__file__).resolve().parents[2] / ".env.sample"
        assert _ENV_SAMPLE_PATH.is_file()


# CLASS: tests.application.test_config.TestAppVersionFollowsTheManifest
# SUMMARY: Verify the version the service reports is the one pyproject.toml declares.
# NOTE: APP_VERSION was the literal "1.0.0" and reached /health/, the OpenAPI document and every
# startup event. Setting a version in pyproject.toml changed none of them: nothing compared the
# two, and the rename checklist did not mention the literal.
class TestAppVersionFollowsTheManifest:
    # FUNCTION: test_app_version_equals_the_declared_version
    # SUMMARY: Verify APP_VERSION is read from the manifest rather than written twice.
    @pytest.mark.unit
    def test_app_version_equals_the_declared_version(self) -> None:
        manifest = Path(__file__).resolve().parents[2] / "pyproject.toml"
        declared = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]["version"]

        assert APP_VERSION == declared

    # FUNCTION: test_manifest_path_resolves_from_the_module_not_the_working_directory
    # SUMMARY: Verify the path the reader uses points at the real manifest.
    @pytest.mark.unit
    def test_manifest_path_resolves_from_the_module_not_the_working_directory(self) -> None:
        # **LOGIC_STEP**: The container runs from /app with sources at /app/project/, and the
        # image is built with --no-install-project, so this path is the only way the version
        # reaches production. A relative path would resolve differently there.
        assert _PYPROJECT_PATH == Path(__file__).resolve().parents[2] / "pyproject.toml"
        assert _PYPROJECT_PATH.is_file()

    # FUNCTION: test_unreadable_manifest_degrades_instead_of_killing_the_import
    # SUMMARY: Verify a missing manifest yields a visible placeholder rather than an exception.
    @pytest.mark.unit
    def test_unreadable_manifest_degrades_instead_of_killing_the_import(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # **LOGIC_STEP**: This runs at import time of the settings module; raising here would
        # take the whole application down over a display string.
        monkeypatch.setattr("project.core.config_runtime._PYPROJECT_PATH", tmp_path / "absent.toml")

        assert _declared_app_version() == _UNKNOWN_APP_VERSION

    # FUNCTION: test_every_way_a_manifest_can_be_unusable_degrades_the_same_way
    # SUMMARY: Verify no shape of broken manifest escapes as an exception at import time.
    # NOTE: The first version of this guard covered "absent" and "no version key" only, and named
    # tomllib.TOMLDecodeError in the except clause. TOML is defined as UTF-8, so a manifest with
    # one corrupted byte raises UnicodeDecodeError instead — uncaught, at import time, taking the
    # whole application down over a display string. Driving the cases from a table is what makes
    # the next unlisted shape a visible gap rather than a silent one.
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
            # **LOGIC_STEP**: A real encoding-corrupted manifest, not a synthetic one — this is
            # the shape that used to raise UnicodeDecodeError straight through the except clause.
            manifest.write_bytes(b'[project]\nversion = "1.0.0"\nname = "\xff\xfe"\n')
        monkeypatch.setattr("project.core.config_runtime._PYPROJECT_PATH", manifest)

        assert _declared_app_version() == _UNKNOWN_APP_VERSION
