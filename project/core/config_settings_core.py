# FILE: project/core/config_settings_core.py
# SUMMARY: Core Pydantic settings models for project, LLM, server, and PostgreSQL configuration.

from typing import Optional

from pydantic import (
    AnyHttpUrl,
    Field,
    PostgresDsn,
    SecretStr,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from project.core.config_builders import (
    build_postgres_dsn,
    build_sqlalchemy_postgres_dsn,
)


class ProjectSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="APP_",
    )

    name: str = Field(default="Mayak", min_length=1, description="Project name")

    debug: bool = False

    sampling_health_check_rate: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="Sampling rate for health check logs",
    )

    # The gate is project/launcher/main.py's `if settings.observability.full_trace_enabled`,
    # never APP_DEBUG — the two flags were deliberately separated so an eval stand can collect
    # full pipeline traces without also turning on reload and verbose stdout. Both this line and
    # the description below said "debug mode", which sent readers to the wrong switch.
    log_dir: str = Field(
        default="logs",
        min_length=1,
        description="Directory for file-based NDJSON logs; written only when ENABLE_FULL_TRACE=true",
    )

    log_max_files: int = Field(
        default=50,
        ge=0,
        description="Max log files to keep (0=unlimited)",
    )


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="OPENAI_COMPATIBLE_",
    )

    api_key: SecretStr = Field(
        default=SecretStr(""), description="API key for OpenAI compatible service"
    )

    base_url: Optional[AnyHttpUrl] = Field(
        default=None,
        description="Base URL for OpenAI compatible API",
    )

    model: str = Field(
        default="gpt-3.5-turbo",
        min_length=1,
        description="Model name for OpenAI compatible API",
    )

    # Handed to ChatOpenAI, which loads the matching encoding lazily — only when something asks it
    # to count tokens, which the kernel never does. A project that does count, and runs without
    # network access, vendors the encoding for THIS model and points TIKTOKEN_CACHE_DIR at it —
    # a cache built for a different model promises offline counting it cannot deliver.
    tiktoken_model_name: str = Field(
        default="gpt-4o-mini",
        description="Tiktoken model name for token counting",
    )

    request_timeout: int = Field(
        default=60,
        ge=5,
        description="Timeout in seconds for a single LLM API request",
    )


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="SERVER_",
    )

    # bandit flags 0.0.0.0 as B104 (bind to all interfaces). Inside a container that is the
    # only correct value — binding 127.0.0.1 makes the service unreachable from outside its own
    # network namespace, so the published port answers nothing. Exposure is decided by the port
    # mapping and the network in front of it, not here. Suppressed with a reason rather than left
    # failing: a security gate that stays red for a finding nobody will fix is how a gate stops
    # being read at all.
    host: str = Field(
        default="0.0.0.0",  # nosec B104
        min_length=1,
        description="Host to bind",
    )

    port: int = Field(default=8000, description="Port to bind")

    workers: int = Field(default=1, description="Number of worker processes")

    cors_origins: list[str] = Field(default=["*"], description="Allowed CORS origins")

    # Default true keeps deployments that never set this variable behaving as before it existed.
    # The reason this needs to be a setting at all — a wildcard origin combined with credentials
    # is the actual vulnerability, not the wildcard alone — is explained where the guard that
    # depends on it lives: project/core/config_runtime.py, Settings.validate_runtime.
    cors_allow_credentials: bool = Field(
        default=True, description="Whether CORS responses allow credentials"
    )

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: list[str]) -> list[str]:
        # Normalize each origin and reject empty lists after cleanup.
        normalized = [origin.strip() for origin in value if origin.strip()]
        if not normalized:
            raise ValueError("SERVER_CORS_ORIGINS must contain at least one origin")
        return normalized


class PostgresSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="POSTGRES_",
    )

    # Upper bound on connections in the shared AsyncConnectionPool.
    # Lives under PostgresSettings, not the agent settings class, so anyone tuning the database
    # finds the one setting that decides how many connections it holds where they'd look first.
    pool_size: int = Field(
        default=20,
        gt=0,
        description="PostgreSQL connection pool size",
    )

    enabled: bool = Field(
        default=True,
        description=(
            "Set POSTGRES_ENABLED=false for a project that needs no relational store — a "
            "vector-search-only or stateless service. The kernel then starts without a pool, "
            "skips Alembic, and stops reporting the database as a critical readiness check. "
            "Default true keeps every existing project unchanged."
        ),
    )

    user: str = Field(default="postgres", min_length=1, description="PostgreSQL username")

    password: SecretStr = Field(
        default=SecretStr("postgres"),
        description="PostgreSQL password",
    )

    # Deliberately generic. The previous default was the snake_case spelling of a retired name of
    # this template, and it survived a rename sweep because the neutrality gate looked only for the
    # concatenated and hyphenated forms. A database name has no reason to encode the template's
    # identity at all, and a name carrying no brand cannot go stale when the brand changes again.
    # The underscored spelling is now in SUPERSEDED_TEMPLATE_NAMES, so the gate would catch it.
    db: str = Field(default="app", min_length=1, description="PostgreSQL database name")

    host: str = Field(default="localhost", min_length=1, description="PostgreSQL host address")

    port: int = Field(default=5432, description="PostgreSQL port number")

    @property
    def database_url(self) -> PostgresDsn:
        # Delegate DSN construction to the shared builder used by psycopg runtime clients.
        return build_postgres_dsn(
            user=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.db,
        )

    @property
    def sqlalchemy_database_url(self) -> PostgresDsn:
        # Keep SQLAlchemy and Alembic on the psycopg v3 dialect.
        return build_sqlalchemy_postgres_dsn(
            user=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.db,
        )
