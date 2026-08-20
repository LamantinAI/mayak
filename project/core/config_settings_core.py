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


# CLASS: project.core.config_settings_core.ProjectSettings
# SUMMARY: Core application settings for project configuration.
class ProjectSettings(BaseSettings):
    # ATTRIBUTE: model_config (SettingsConfigDict)
    # SUMMARY: Pydantic configuration for project-scoped environment variables.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="APP_",
    )

    # ATTRIBUTE: name (str)
    # SUMMARY: Project name used for identification.
    name: str = Field(default="Mayak", min_length=1, description="Project name")

    # ATTRIBUTE: debug (bool)
    # SUMMARY: Flag to enable or disable debug mode.
    debug: bool = False

    # ATTRIBUTE: sampling_health_check_rate (float)
    # SUMMARY: Sampling rate for health check logs.
    sampling_health_check_rate: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="Sampling rate for health check logs",
    )

    # ATTRIBUTE: log_dir (str)
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

    # ATTRIBUTE: log_max_files (int)
    # SUMMARY: Maximum number of log files to retain (0 disables rotation).
    log_max_files: int = Field(
        default=50,
        ge=0,
        description="Max log files to keep (0=unlimited)",
    )


# CLASS: project.core.config_settings_core.LLMSettings
# SUMMARY: Configuration for OpenAI-compatible LLM services.
class LLMSettings(BaseSettings):
    # ATTRIBUTE: model_config (SettingsConfigDict)
    # SUMMARY: Pydantic configuration for LLM environment variables.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="OPENAI_COMPATIBLE_",
    )

    # ATTRIBUTE: api_key (SecretStr)
    # SUMMARY: API key for the configured provider.
    api_key: SecretStr = Field(
        default=SecretStr(""), description="API key for OpenAI compatible service"
    )

    # ATTRIBUTE: base_url (Optional[AnyHttpUrl])
    # SUMMARY: Base URL for the provider API endpoint.
    base_url: Optional[AnyHttpUrl] = Field(
        default=None,
        description="Base URL for OpenAI compatible API",
    )

    # ATTRIBUTE: model (str)
    # SUMMARY: Model name to use for the provider API.
    model: str = Field(
        default="gpt-3.5-turbo",
        min_length=1,
        description="Model name for OpenAI compatible API",
    )

    # ATTRIBUTE: tiktoken_model_name (str)
    # SUMMARY: Tiktoken model name used for token counting.
    # Handed to ChatOpenAI, which loads the matching encoding lazily — only when something asks it
    # to count tokens, which the kernel never does. A project that does count, and runs without
    # network access, vendors the encoding for THIS model and points TIKTOKEN_CACHE_DIR at it. The
    # template used to ship such a cache; it held cl100k_base while this default needs o200k_base,
    # so it promised offline counting it could not deliver.
    tiktoken_model_name: str = Field(
        default="gpt-4o-mini",
        description="Tiktoken model name for token counting",
    )

    # ATTRIBUTE: request_timeout (int)
    # SUMMARY: Timeout in seconds for a single LLM API request.
    request_timeout: int = Field(
        default=60,
        ge=5,
        description="Timeout in seconds for a single LLM API request",
    )


# CLASS: project.core.config_settings_core.ServerSettings
# SUMMARY: Configuration for the FastAPI server runtime.
class ServerSettings(BaseSettings):
    # ATTRIBUTE: model_config (SettingsConfigDict)
    # SUMMARY: Pydantic configuration for server environment variables.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="SERVER_",
    )

    # ATTRIBUTE: host (str)
    # SUMMARY: Host to bind.
    # NOTE: bandit flags 0.0.0.0 as B104 (bind to all interfaces). Inside a container that is the
    # only correct value — binding 127.0.0.1 makes the service unreachable from outside its own
    # network namespace, so the published port answers nothing. Exposure is decided by the port
    # mapping and the network in front of it, not here. Suppressed with a reason rather than left
    # failing: the CI security job had been red on main since before 2026-08-05 for this one
    # finding, which is how a gate stops being read.
    host: str = Field(
        default="0.0.0.0",  # nosec B104
        min_length=1,
        description="Host to bind",
    )

    # ATTRIBUTE: port (int)
    # SUMMARY: Port to bind.
    port: int = Field(default=8000, description="Port to bind")

    # ATTRIBUTE: workers (int)
    # SUMMARY: Number of worker processes.
    workers: int = Field(default=1, description="Number of worker processes")

    # ATTRIBUTE: cors_origins (list[str])
    # SUMMARY: Allowed CORS origins.
    cors_origins: list[str] = Field(default=["*"], description="Allowed CORS origins")

    # FUNCTION: validate_cors_origins
    # SUMMARY: Normalize CORS origins by trimming whitespace and dropping empty values.
    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: list[str]) -> list[str]:
        # **LOGIC_STEP**: Normalize each origin and reject empty lists after cleanup.
        normalized = [origin.strip() for origin in value if origin.strip()]
        if not normalized:
            raise ValueError("SERVER_CORS_ORIGINS must contain at least one origin")
        return normalized


# CLASS: project.core.config_settings_core.PostgresSettings
# SUMMARY: Configuration for PostgreSQL database connectivity.
class PostgresSettings(BaseSettings):
    # ATTRIBUTE: model_config (SettingsConfigDict)
    # SUMMARY: Pydantic configuration for PostgreSQL environment variables.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="POSTGRES_",
    )

    # ATTRIBUTE: pool_size (int)
    # SUMMARY: Upper bound on connections in the shared AsyncConnectionPool.
    # It used to be AGENT_DB_POOL_SIZE — a database knob living in the agent settings class, so
    # the one setting that decides how many connections the service holds was the last place
    # anyone tuning the database would look.
    pool_size: int = Field(
        default=20,
        gt=0,
        description="PostgreSQL connection pool size",
    )

    # ATTRIBUTE: enabled (bool)
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

    # ATTRIBUTE: user (str)
    # SUMMARY: PostgreSQL username.
    user: str = Field(default="postgres", min_length=1, description="PostgreSQL username")

    # ATTRIBUTE: password (SecretStr)
    # SUMMARY: PostgreSQL password.
    password: SecretStr = Field(
        default=SecretStr("postgres"),
        description="PostgreSQL password",
    )

    # ATTRIBUTE: db (str)
    # SUMMARY: PostgreSQL database name.
    # Deliberately generic. The previous default was the snake_case spelling of a retired name of
    # this template, and it survived a rename sweep because the neutrality gate looked only for the
    # concatenated and hyphenated forms. A database name has no reason to encode the template's
    # identity at all, and a name carrying no brand cannot go stale when the brand changes again.
    # The underscored spelling is now in SUPERSEDED_TEMPLATE_NAMES, so the gate would catch it.
    db: str = Field(default="app", min_length=1, description="PostgreSQL database name")

    # ATTRIBUTE: host (str)
    # SUMMARY: PostgreSQL host address.
    host: str = Field(default="localhost", min_length=1, description="PostgreSQL host address")

    # ATTRIBUTE: port (int)
    # SUMMARY: PostgreSQL port number.
    port: int = Field(default=5432, description="PostgreSQL port number")

    # FUNCTION: database_url
    # SUMMARY: Generate complete PostgreSQL connection URL from individual parameters.
    # OUTPUT: (PostgresDsn): Complete PostgreSQL connection URL.
    @property
    def database_url(self) -> PostgresDsn:
        # **LOGIC_STEP**: Delegate DSN construction to the shared builder used by psycopg runtime clients.
        return build_postgres_dsn(
            user=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.db,
        )

    # FUNCTION: sqlalchemy_database_url
    # SUMMARY: Generate SQLAlchemy-compatible PostgreSQL URL using the psycopg v3 dialect.
    @property
    def sqlalchemy_database_url(self) -> PostgresDsn:
        # **LOGIC_STEP**: Keep SQLAlchemy and Alembic on the psycopg v3 dialect.
        return build_sqlalchemy_postgres_dsn(
            user=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.db,
        )
