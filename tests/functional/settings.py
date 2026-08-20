from pydantic_settings import BaseSettings

from utils.helpers import PostgresSettings, ServiceSettings


# Settings initialization
postgres_settings = PostgresSettings()
service_settings = ServiceSettings()


# CLASS: tests.functional.settings.TestSettings
# SUMMARY: Base settings class for functional tests.
class TestSettings(BaseSettings):
    postgres_url: str = postgres_settings.database_url
    service_url: str = service_settings.get_host()

    # Additional settings can be added here
    test_timeout: int = 30


# Global settings instance for use in tests
test_settings = TestSettings()
