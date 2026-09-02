# FILE: project/core/config.py
# SUMMARY: Stable facade that re-exports configuration builders, settings models, and runtime helpers.

from project.core.config_builders import (
    build_postgres_dsn,
    build_sqlalchemy_postgres_dsn,
)
from project.core.config_runtime import (
    APP_VERSION,
    GUARDS_RELAXED_BY_DEBUG,
    Settings,
    clear_settings_override,
    get_settings,
    set_settings_override,
)
from project.core.config_settings_core import (
    LLMSettings,
    PostgresSettings,
    ProjectSettings,
    ServerSettings,
)
from project.core.config_settings_agent import AgentSettings
from project.core.config_settings_observability import ObservabilitySettings

# ATTRIBUTE: __all__ (list[str])
# SUMMARY: Public configuration symbols re-exported by the stable config facade.
__all__ = [
    "APP_VERSION",
    "GUARDS_RELAXED_BY_DEBUG",
    "LLMSettings",
    "AgentSettings",
    "ObservabilitySettings",
    "PostgresSettings",
    "ProjectSettings",
    "ServerSettings",
    "Settings",
    "build_postgres_dsn",
    "build_sqlalchemy_postgres_dsn",
    "clear_settings_override",
    "get_settings",
    "set_settings_override",
]
