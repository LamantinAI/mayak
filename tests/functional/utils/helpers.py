# FILE: tests/functional/utils/helpers.py
# SUMMARY: Helper classes and settings for functional tests.
# NOTE: Relaxed functional-test helper module. Do not copy its patterns into project/** production code.

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from project.core.config_builders import build_postgres_dsn

# ATTRIBUTE: ApiResponse (type)
# SUMMARY: What every request helper in conftest.py returns — {"status": int, "body": Any}.
ApiResponse = dict[str, Any]

# ATTRIBUTE: SendRequest (type)
# SUMMARY: The shape of the request-helper fixtures. Arguments are deliberately unconstrained:
# the five verbs differ (DELETE takes a path alone), and pinning each one separately would buy
# nothing a functional test can use.
SendRequest = Callable[..., Awaitable[ApiResponse]]

# ATTRIBUTE: LoadTestData (type)
# SUMMARY: The shape of the load_test_data fixture.
LoadTestData = Callable[[str, list[dict[str, Any]]], Awaitable[None]]


# CLASS: tests.functional.utils.helpers.CommonSettings
# SUMMARY: Base settings class with protocol, host, and port.
# ATTRIBUTE: protocol (str): Connection protocol (e.g., http://).
# ATTRIBUTE: host (str): Host address.
# ATTRIBUTE: port (int): Port number.
# NOTE: The three fields carried `= ...` as their "required" marker. Pydantic accepts it; mypy
# reads it as assigning an EllipsisType to a str, which is one of the errors that kept this suite
# outside the type gate. Defaults matching the compose stack say the same thing in a checkable
# way — and a missing SERVER_HOST now fails as a connection error to test-app rather than as a
# validation error, which is the same diagnosis one step later.
class CommonSettings(BaseSettings):
    protocol: str = Field(default="http://")
    host: str = Field(default="test-app")
    port: int = Field(default=8000)

    # FUNCTION: get_host
    # SUMMARY: Returns the full URL with protocol, host, and port.
    # OUTPUT: (str): Full URL string.
    def get_host(self) -> str:
        # **LOGIC_STEP**: Format and return the full host URL.
        return f"{self.protocol}{self.host}:{self.port}"


# CLASS: tests.functional.utils.helpers.PostgresSettings
# SUMMARY: Settings for PostgreSQL connection.
# EXTENDS: pydantic_settings.BaseSettings
# ATTRIBUTE: user (str): Database user.
# ATTRIBUTE: password (str): Database password.
# ATTRIBUTE: db (str): Database name.
# ATTRIBUTE: host (str): Database host.
# ATTRIBUTE: port (int): Database port.
class PostgresSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="POSTGRES_",
    )

    user: str = Field(default="postgres")
    password: SecretStr = Field(default=SecretStr("postgres"))
    db: str = Field(default="test_db")
    host: str = Field(default="test-db")
    port: int = Field(default=5432)

    # FUNCTION: database_url
    # SUMMARY: Returns the database connection string.
    # OUTPUT: (str): PostgreSQL connection URL.
    @property
    def database_url(self) -> str:
        # **LOGIC_STEP**: Built by the same helper production uses, not by an f-string. The local
        # copy interpolated user and password raw, so a password containing @ : / or % produced a
        # DSN that parsed differently from the one the application builds from the same .env —
        # the functional suite would fail to connect against a configuration that works.
        return str(
            build_postgres_dsn(
                user=self.user,
                password=self.password.get_secret_value(),
                host=self.host,
                port=self.port,
                database=self.db,
            )
        )


# CLASS: tests.functional.utils.helpers.ServiceSettings
# SUMMARY: Settings for service API connection.
# EXTENDS: tests.functional.utils.helpers.CommonSettings
class ServiceSettings(CommonSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="SERVER_",
    )
