# FILE: project/core/config_settings_observability.py
# SUMMARY: The one switch that turns the kernel's terse logging into a full on-disk trace.

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# CLASS: project.core.config_settings_observability.ObservabilitySettings
# SUMMARY: Everything the operator can change about how much the service records.
class ObservabilitySettings(BaseSettings):
    # ATTRIBUTE: model_config (SettingsConfigDict)
    # SUMMARY: Read from the process environment, falling back to .env, names case-insensitive.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ATTRIBUTE: full_trace_enabled (bool)
    # SUMMARY: Record the whole run to a file instead of only its summary.
    # NOTE: One flag, three consequences, all in one direction — more on disk. It opens
    # the NDJSON file in `project/launcher/main.py`, drops the level to DEBUG so span events
    # survive, and lets `_build_full_trace_extras` attach the LLM prompt and completion bodies.
    # NOTE: Deliberately not APP_DEBUG. Debug is about developer ergonomics — reload, verbose
    # stdout — and an eval stand wants the trace without them, while a developer wants the
    # ergonomics without a growing file of prompt text on disk. Coupling the two takes one of
    # those two people's choice away.
    # NOTE: Renamed from `deep_trace_enabled` / ENABLE_DEEP_TRACE — that name described a depth
    # of tracing this flag does not control; what it controls is whether the run is written out
    # in full. A `.env` still setting the retired variable gets the default, with no warning, so
    # rename it when taking this kernel version.
    full_trace_enabled: bool = Field(
        default=False,
        validation_alias="ENABLE_FULL_TRACE",
        description=(
            "Write the full NDJSON trace file and record LLM prompt and completion bodies. "
            "Independent of APP_DEBUG. Off by default: it puts prompt text on disk."
        ),
    )
