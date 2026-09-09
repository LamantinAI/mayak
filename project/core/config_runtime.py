# FILE: project/core/config_runtime.py
# SUMMARY: Runtime settings facade, validation logic, and override helpers built on top of smaller settings modules.

import functools
import tomllib
from pathlib import Path
from typing import Optional

from project.core.config_settings_core import (
    LLMSettings,
    PostgresSettings,
    ProjectSettings,
    ServerSettings,
)
from project.core.config_settings_agent import AgentSettings
from project.core.config_settings_observability import ObservabilitySettings

# ATTRIBUTE: _PYPROJECT_PATH (Path)
# SUMMARY: The manifest that declares this project's version, located relative to this module.
# Resolved from __file__ rather than the working directory: the container starts the app from
# /app with the sources at /app/project/, and a relative path would break under any other cwd.
_PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"

# ATTRIBUTE: _UNKNOWN_APP_VERSION (str)
# SUMMARY: Reported when the manifest cannot be read, so the app starts and says so.
_UNKNOWN_APP_VERSION = "0.0.0+unknown"

# ATTRIBUTE: _ENV_SAMPLE_PATH (Path)
# SUMMARY: The sample environment file whose values are, by definition, placeholders.
# Resolved from __file__ for the same reason as the manifest above: the container starts from /app.
# `.dockerignore` excludes `.env` and not `.env.sample`, so the file is present in the image too.
_ENV_SAMPLE_PATH = Path(__file__).resolve().parents[2] / ".env.sample"

# ATTRIBUTE: _SECRET_KEY_MARKERS (tuple[str, ...])
# SUMMARY: Substrings that mark an environment key as carrying a credential.
_SECRET_KEY_MARKERS = ("PASSWORD", "API_KEY", "SECRET", "TOKEN")


# FUNCTION: _sample_placeholder_secrets
# SUMMARY: Read the credential placeholders `.env.sample` ships, so production can refuse to reuse them.
# OUTPUT: (dict[str, str]): Environment key to its sample value; empty when the file is unreadable.
def _sample_placeholder_secrets() -> dict[str, str]:
    # **LOGIC_STEP**: Never raise and never fail closed. A missing sample file must not stop a
    # service from starting — it only means this particular check has nothing to compare against.
    try:
        lines = _ENV_SAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    placeholders: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        # **LOGIC_STEP**: Skip empty sample values. `.env.sample` leaves the provider key blank,
        # and treating "" as a placeholder would make every unset credential look like a leak.
        if not value or not any(marker in key.upper() for marker in _SECRET_KEY_MARKERS):
            continue
        placeholders[key] = value
    return placeholders


# FUNCTION: _declared_app_version
# SUMMARY: Read the version pyproject.toml declares, falling back to a visible placeholder.
# OUTPUT: (str): The declared version, or _UNKNOWN_APP_VERSION when the manifest is unreadable.
def _declared_app_version() -> str:
    # **LOGIC_STEP**: The manifest, not importlib.metadata. Dockerfile builds the image with
    # `uv sync --frozen --no-install-project --no-dev`, so the project distribution is not
    # installed in the runtime image at all and metadata lookup raises PackageNotFoundError
    # there while succeeding locally — the version would be right on a developer's machine and
    # wrong in production. pyproject.toml is copied into the image next to the sources, so the
    # same file answers in both places. It is also the file `initialize-project` already edits.
    try:
        with _PYPROJECT_PATH.open("rb") as manifest:
            return str(tomllib.load(manifest)["project"]["version"])
    except (OSError, ValueError, KeyError, TypeError):
        # **LOGIC_STEP**: Never raise. This runs at import time of the settings module, so an
        # unreadable manifest would take the whole application down over a display string.
        # ValueError rather than tomllib.TOMLDecodeError: TOML is defined as UTF-8, and a manifest
        # carrying one bad byte — an encoding-corrupted save, a botched merge — makes tomllib.load
        # raise UnicodeDecodeError instead. Naming only TOMLDecodeError left that case uncaught and
        # the application dead on import, which is the exact failure this except exists to prevent.
        # Both are ValueError subclasses, so one name covers the family and cannot miss a third.
        return _UNKNOWN_APP_VERSION


# ATTRIBUTE: APP_VERSION (str)
# SUMMARY: Single source of truth for the application version, read from pyproject.toml.
# It reaches /health/, the OpenAPI document and every startup event. As a hardcoded "1.0.0" it
# disagreed with the manifest the moment anyone set a version there, and the six places
# `initialize-project` renames did not include it.
APP_VERSION = _declared_app_version()

# ATTRIBUTE: _SETTINGS_OVERRIDE (Optional["Settings"])
# SUMMARY: Optional in-process settings override used by tests and controlled bootstrap flows.
_SETTINGS_OVERRIDE: Optional["Settings"] = None

# ATTRIBUTE: GUARDS_RELAXED_BY_DEBUG (tuple[str, ...])
# SUMMARY: The startup checks in validate_runtime() that APP_DEBUG=true switches off, each named
# by the variable it protects.
# NOTE: One flag, three guards — composition_root.py hard-wires FastAPI(debug=False), so
# "Starlette's own error page" is never a real effect of this flag and documentation must not
# claim it is. This tuple is what README.md and .env.sample are checked against, and what the
# startup event lists, so the three places cannot describe the flag three different ways.
# A new guard gated on `not self.project.debug` belongs here first; the test in
# tests/application/test_env_sample_matches_code.py counts them.
GUARDS_RELAXED_BY_DEBUG = (
    "SERVER_CORS_ORIGINS",
    "POSTGRES_PASSWORD",
    "OPENAI_COMPATIBLE_API_KEY",
)


# CLASS: project.core.config_runtime.Settings
# SUMMARY: Main settings facade that combines all configuration sections.
class Settings:
    # ATTRIBUTE: project (ProjectSettings)
    # SUMMARY: Project-specific configuration.
    project: ProjectSettings

    # ATTRIBUTE: llm (LLMSettings)
    # SUMMARY: LLM service configuration.
    llm: LLMSettings

    # ATTRIBUTE: server (ServerSettings)
    # SUMMARY: FastAPI server configuration.
    server: ServerSettings

    # ATTRIBUTE: postgres (PostgresSettings)
    # SUMMARY: PostgreSQL database configuration.
    postgres: PostgresSettings

    # ATTRIBUTE: agent (AgentSettings)
    # SUMMARY: Agent runtime configuration: LLM behaviour, prompts, readiness.
    agent: AgentSettings

    # ATTRIBUTE: observability (ObservabilitySettings)
    # SUMMARY: Observability / full-trace configuration.
    observability: ObservabilitySettings

    # FUNCTION: __init__
    # SUMMARY: Initialize all configuration sections.
    def __init__(self) -> None:
        # **LOGIC_STEP**: Instantiate all settings sections from environment-backed models.
        self.project = ProjectSettings()
        self.llm = LLMSettings()
        self.server = ServerSettings()
        self.postgres = PostgresSettings()
        self.agent = AgentSettings()
        self.observability = ObservabilitySettings()

    # FUNCTION: validate_runtime
    # SUMMARY: Validate cross-setting runtime requirements before application startup.
    # RAISES: ValueError: If a production-critical configuration is unsafe or incomplete.
    def validate_runtime(self) -> None:
        # **LOGIC_STEP**: Trim the API key once so later checks can reason about actual availability.
        api_key = self.llm.api_key.get_secret_value().strip()

        # **LOGIC_STEP**: Reject wildcard CORS in non-debug mode with a membership test, not
        # equality: Starlette's CORSMiddleware decides allow-all with
        # `allow_all_origins = "*" in allow_origins` (a membership test over the whole list), so
        # an equality check against `["*"]` alone would miss
        # SERVER_CORS_ORIGINS=["https://app.example.com", "*"]. With that list, APP_DEBUG=false
        # and credentials on, a request carrying `Origin: https://evil.attacker.test` gets back
        # `access-control-allow-origin: https://evil.attacker.test` and
        # `access-control-allow-credentials: true` — the textbook credentialed-wildcard hole: any
        # site can make authenticated cross-origin requests as a logged-in user.
        #
        # The guard is narrowed to match Starlette's own condition for the dangerous branch —
        # `if self.allow_all_origins and self.allow_credentials: self.allow_explicit_origin(...)`
        # in CORSMiddleware — refusing the wildcard only when SERVER_CORS_ALLOW_CREDENTIALS
        # (ServerSettings.cors_allow_credentials, default true) is actually enabled. A wildcard
        # WITHOUT credentials is the ordinary, safe public-API shape: no credential ever rides on
        # the response, so echoing the origin back is harmless. `not self.project.debug` is
        # deliberate too — a wildcard under APP_DEBUG=true is intentional for local development
        # and must keep working regardless of the credentials flag.
        if (
            not self.project.debug
            and self.server.cors_allow_credentials
            and "*" in self.server.cors_origins
        ):
            raise ValueError(
                "SERVER_CORS_ORIGINS must not contain '*' while SERVER_CORS_ALLOW_CREDENTIALS=true "
                "and APP_DEBUG=false. Starlette's CORSMiddleware treats a wildcard anywhere in the "
                "list as allow-all — not only a single-element ['*'] — and echoes back any "
                "requesting Origin with credentials allowed, letting any site make authenticated "
                "cross-origin requests as a logged-in user. Either list the exact origins this "
                "deployment serves, or set SERVER_CORS_ALLOW_CREDENTIALS=false if this API is "
                "meant to be public and never sends credentials."
            )

        # **LOGIC_STEP**: Collect what `.env.sample` offers as placeholders, so the checks below
        # reject the sample's own values and not just one hardcoded word.
        # NOTE: Compared against the sample file's placeholders, not only the literal "postgres":
        # `your_postgres_password` — the value this very repository ships in .env.sample — along
        # with `changeme` and `password`, would all start in production without a word if only
        # one literal were checked. Comparing against the sample file keeps the two in step by
        # construction: change the placeholder there and the guard follows, with no second place
        # to remember.
        placeholder_secrets = _sample_placeholder_secrets()

        # **LOGIC_STEP**: Reject default PostgreSQL password in non-debug mode — but only when
        # the project actually connects to PostgreSQL. Otherwise a service that declared
        # POSTGRES_ENABLED=false would be forced to invent a password for a database it never
        # opens, which is the kind of pointless ceremony that teaches people to ignore guards.
        if self.postgres.enabled and not self.project.debug:
            password = self.postgres.password.get_secret_value()
            # The literal stays alongside the sample lookup on purpose: "postgres" is the image's
            # own default and does not appear in .env.sample, so dropping it would reopen the hole.
            if password == "postgres" or password == placeholder_secrets.get("POSTGRES_PASSWORD"):
                # **LOGIC_STEP**: The message names the file, because this now fires on a fresh
                # checkout: .env.sample ships APP_DEBUG=false next to a placeholder password, so
                # "it will not start" must arrive with the edit that fixes it.
                raise ValueError(
                    "POSTGRES_PASSWORD must be set to a non-default value when APP_DEBUG=false — "
                    "it still holds the placeholder from .env.sample. Set a real password in .env, "
                    "or set APP_DEBUG=true for local development."
                )

        # **LOGIC_STEP**: The same rule for the provider credential. Today .env.sample ships it
        # empty, so this cannot fire — it fires the day someone writes a placeholder there, which
        # is exactly when it would otherwise reach production unnoticed.
        # Gated on live mode for the same reason the password check is gated on postgres.enabled:
        # a mock-mode deployment never sends that key anywhere, and refusing to start over a
        # credential nothing reads is the pointless ceremony that teaches people to ignore guards.
        if (
            self.agent.llm_mode == "live"
            and not self.project.debug
            and api_key
            and api_key == placeholder_secrets.get("OPENAI_COMPATIBLE_API_KEY")
        ):
            raise ValueError(
                "OPENAI_COMPATIBLE_API_KEY still holds the placeholder from .env.sample"
            )

        # **LOGIC_STEP**: Require an API key for any configuration that performs external LLM calls.
        if self.agent.llm_mode == "live" and not api_key:
            raise ValueError("OPENAI_COMPATIBLE_API_KEY is required when AGENT_LLM_MODE=live")

        # **LOGIC_STEP**: Validate prompt directory and default prompt file existence, so a
        # misconfigured AGENT_SYSTEM_PROMPT_NAME fails at startup instead of the first time an
        # agent tries to read the file.
        prompts_dir = self.agent.prompts_dir.resolve()
        if not prompts_dir.exists() or not prompts_dir.is_dir():
            raise ValueError(
                f"AGENT_PROMPTS_DIR does not exist or is not a directory: {prompts_dir}"
            )

        system_prompt = prompts_dir / self.agent.system_prompt_name
        if not system_prompt.is_file():
            raise ValueError(
                f"AGENT_SYSTEM_PROMPT_NAME names a file that does not exist: {system_prompt}"
            )


# FUNCTION: get_settings
# SUMMARY: Get the lazily initialized cached settings instance.
@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    # **LOGIC_STEP**: Return the explicit override when tests or bootstrap code install one.
    if _SETTINGS_OVERRIDE is not None:
        return _SETTINGS_OVERRIDE
    return Settings()


# FUNCTION: set_settings_override
# SUMMARY: Install an explicit settings instance override for subsequent get_settings() calls.
def set_settings_override(settings: Settings) -> None:
    # **LOGIC_STEP**: Clear the cached instance so the override becomes the single source of truth.
    global _SETTINGS_OVERRIDE
    get_settings.cache_clear()
    _SETTINGS_OVERRIDE = settings


# FUNCTION: clear_settings_override
# SUMMARY: Remove any explicit settings override and reset the cached lazy instance.
def clear_settings_override() -> None:
    # **LOGIC_STEP**: Drop the override and invalidate the cache for the next caller.
    global _SETTINGS_OVERRIDE
    _SETTINGS_OVERRIDE = None
    get_settings.cache_clear()
