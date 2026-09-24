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


# SUMMARY: Core application settings for project configuration.
class ProjectSettings(BaseSettings):
    # SUMMARY: Pydantic configuration for project-scoped environment variables.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="APP_",
    )

    # SUMMARY: Project name used for identification.
    name: str = Field(default="Mayak", min_length=1, description="Project name")

    # SUMMARY: Flag to enable or disable debug mode.
    debug: bool = False

    # SUMMARY: Sampling rate for health check logs.
    sampling_health_check_rate: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="Sampling rate for health check logs",
    )

    # SUMMARY: Directory for file-based NDJSON logs, written only when ENABLE_FULL_TRACE is on.
    # The gate is project/launcher/main.py's `if settings.observability.full_trace_enabled`,
    # never APP_DEBUG — the two flags were deliberately separated so an eval stand can collect
    # full pipeline traces without also turning on reload and verbose stdout. Both this line and
    # the description below said "debug mode", which sent readers to the wrong switch.
    log_dir: str = Field(
        default="logs",
        min_length=1,
        description="Directory for file-based NDJSON logs; written only when ENABLE_FULL_TRACE=true",
    )

    # SUMMARY: Maximum number of log files to retain (0 disables rotation).
    log_max_files: int = Field(
        default=50,
        ge=0,
        description="Max log files to keep (0=unlimited)",
    )


# SUMMARY: Configuration for OpenAI-compatible LLM services.
class LLMSettings(BaseSettings):
    # SUMMARY: Pydantic configuration for LLM environment variables.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="OPENAI_COMPATIBLE_",
    )

    # SUMMARY: API key for the configured provider.
    api_key: SecretStr = Field(
        default=SecretStr(""), description="API key for OpenAI compatible service"
    )

    # SUMMARY: Base URL for the provider API endpoint.
    base_url: Optional[AnyHttpUrl] = Field(
        default=None,
        description="Base URL for OpenAI compatible API",
    )

    # SUMMARY: Model name to use for the provider API.
    model: str = Field(
        default="gpt-3.5-turbo",
        min_length=1,
        description="Model name for OpenAI compatible API",
    )

    # SUMMARY: Tiktoken model name used for token counting.
    # Handed to ChatOpenAI, which loads the matching encoding lazily — only when something asks it
    # to count tokens, which the kernel never does. A project that does count, and runs without
    # network access, vendors the encoding for THIS model and points TIKTOKEN_CACHE_DIR at it —
    # a cache built for a different model promises offline counting it cannot deliver.
    tiktoken_model_name: str = Field(
        default="gpt-4o-mini",
        description="Tiktoken model name for token counting",
    )

    # SUMMARY: Timeout in seconds for a single LLM API request.
    request_timeout: int = Field(
        default=60,
        ge=5,
        description="Timeout in seconds for a single LLM API request",
    )


# SUMMARY: Configuration for the FastAPI server runtime.
class ServerSettings(BaseSettings):
    # SUMMARY: Pydantic configuration for server environment variables.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="SERVER_",
    )

    # SUMMARY: Host to bind.
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

    # SUMMARY: Port to bind.
    port: int = Field(default=8000, description="Port to bind")

    # SUMMARY: Number of worker processes.
    workers: int = Field(default=1, description="Number of worker processes")

    # SUMMARY: Allowed CORS origins.
    cors_origins: list[str] = Field(default=["*"], description="Allowed CORS origins")

    # SUMMARY: Whether CORS responses include Access-Control-Allow-Credentials.
    # Default true keeps deployments that never set this variable behaving as before it existed.
    # The reason this needs to be a setting at all — a wildcard origin combined with credentials
    # is the actual vulnerability, not the wildcard alone — is explained where the guard that
    # depends on it lives: project/core/config_runtime.py, Settings.validate_runtime.
    cors_allow_credentials: bool = Field(
        default=True, description="Whether CORS responses allow credentials"
    )

    # SUMMARY: Normalize CORS origins by trimming whitespace and dropping empty values.
    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: list[str]) -> list[str]:
        # Normalize each origin and reject empty lists after cleanup.
        normalized = [origin.strip() for origin in value if origin.strip()]
        if not normalized:
            raise ValueError("SERVER_CORS_ORIGINS must contain at least one origin")
        return normalized


# SUMMARY: Configuration for PostgreSQL database connectivity.
class PostgresSettings(BaseSettings):
    # SUMMARY: Pydantic configuration for PostgreSQL environment variables.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="POSTGRES_",
    )

    # SUMMARY: Upper bound on connections in the shared AsyncConnectionPool.
    # Lives under PostgresSettings, not the agent settings class, so anyone tuning the database
    # finds the one setting that decides how many connections it holds where they'd look first.
    pool_size: int = Field(
        default=20,
        gt=0,
        description="PostgreSQL connection pool size",
    )

    # SUMMARY: Whether this project uses PostgreSQL at all. False builds no pool, runs no
    # migrations, and drops the database from the readiness contract.
    enabled: bool = Field(
        default=True,
        description=(
            "Set POSTGRES_ENABLED=false for a project that needs no relational store — a "
            "vector-search-only or stateless service. The kernel then starts without a pool, "
            "skips Alembic, and stops reporting the database as a critical readiness check. "
            "Default true keeps every existing project unchanged."
        ),
    )

    # SUMMARY: PostgreSQL username.
    user: str = Field(default="postgres", min_length=1, description="PostgreSQL username")

    # SUMMARY: PostgreSQL password.
    password: SecretStr = Field(
        default=SecretStr("postgres"),
        description="PostgreSQL password",
    )

    # SUMMARY: PostgreSQL database name.
    # Deliberately generic. The previous default was the snake_case spelling of a retired name of
    # this template, and it survived a rename sweep because the neutrality gate looked only for the
    # concatenated and hyphenated forms. A database name has no reason to encode the template's
    # identity at all, and a name carrying no brand cannot go stale when the brand changes again.
    # The underscored spelling is now in SUPERSEDED_TEMPLATE_NAMES, so the gate would catch it.
    db: str = Field(default="app", min_length=1, description="PostgreSQL database name")

    # SUMMARY: PostgreSQL host address.
    host: str = Field(default="localhost", min_length=1, description="PostgreSQL host address")

    # SUMMARY: PostgreSQL port number.
    port: int = Field(default=5432, description="PostgreSQL port number")

    # SUMMARY: Generate complete PostgreSQL connection URL from individual parameters.
    # OUTPUT: (PostgresDsn): Complete PostgreSQL connection URL.
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

    # SUMMARY: Generate SQLAlchemy-compatible PostgreSQL URL using the psycopg v3 dialect.
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
