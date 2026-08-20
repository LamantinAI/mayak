# FILE: project/core/config_settings_agent.py
# SUMMARY: Settings for the agent runtime — LLM behaviour, prompt location, readiness strategy.
# NOTE: This file and its prefix were called LangGraph until the template stopped shipping that
# library. Nothing here was ever LangGraph-specific; the name simply outlived the dependency, and
# a settings class named after a package the code does not import is a false clue for the next
# reader. AGENT_ says what these knobs actually configure.

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# CLASS: project.core.config_settings_agent.AgentSettings
# SUMMARY: Configuration for agent execution: LLM mode, generation limits, prompts, readiness.
class AgentSettings(BaseSettings):
    # ATTRIBUTE: model_config (SettingsConfigDict)
    # SUMMARY: Pydantic configuration for AGENT_-prefixed environment variables.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="AGENT_",
    )

    # ATTRIBUTE: default_llm_temperature (float)
    # SUMMARY: Default LLM temperature.
    default_llm_temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Default LLM temperature",
    )

    # ATTRIBUTE: max_tokens (int)
    # SUMMARY: Maximum tokens for LLM responses.
    # 4096 rather than the more common 2000. A structured answer — several sections with nested
    # bullet lists — crosses 2000 tokens easily, and the provider truncates mid-sentence rather
    # than failing, so the defect surfaces as a streamed reply that simply stops. 4096 covers
    # roughly 3000-3500 words of a non-Latin script (which costs more tokens per word than
    # English) and is supported by every OpenAI-compatible provider worth targeting.
    # A project that answers in short form lowers it via AGENT_MAX_TOKENS.
    max_tokens: int = Field(default=4096, gt=0, description="Maximum tokens for LLM responses")

    # ATTRIBUTE: max_llm_call_retries (int)
    # SUMMARY: Maximum retry attempts for LLM calls.
    max_llm_call_retries: int = Field(
        default=3,
        gt=0,
        description="Maximum retry attempts for LLM calls",
    )

    # ATTRIBUTE: llm_mode (Literal["live", "mock"])
    # SUMMARY: Select whether the agent uses a real provider or deterministic mock backend.
    llm_mode: Literal["live", "mock"] = Field(
        default="mock",
        description="Execution mode for the LLM backend",
    )

    # ATTRIBUTE: prompts_dir (Path)
    # SUMMARY: Base directory for system prompt files.
    prompts_dir: Path = Field(default=Path("project/prompts"), description="Prompts directory")

    # ATTRIBUTE: llm_readiness_check_mode (Literal["probe", "init"])
    # SUMMARY: Strategy for readiness checks against the configured LLM service.
    llm_readiness_check_mode: Literal["probe", "init"] = Field(
        default="probe",
        description="Readiness check mode for LLM service",
    )

    # ATTRIBUTE: llm_readiness_timeout_seconds (float)
    # SUMMARY: Timeout in seconds for external LLM readiness probes.
    llm_readiness_timeout_seconds: float = Field(
        default=5.0,
        gt=0.0,
        description="Timeout in seconds for LLM readiness probe calls",
    )

    # ATTRIBUTE: system_prompt_name (str)
    # SUMMARY: Default filename for the system prompt.
    system_prompt_name: str = Field(
        default="example_assistant_prompt.txt",
        description="Default filename for the system prompt",
    )

    # FUNCTION: validate_system_prompt_name
    # SUMMARY: Ensure the configured prompt name targets a text file.
    @field_validator("system_prompt_name")
    @classmethod
    def validate_system_prompt_name(cls, value: str) -> str:
        # **LOGIC_STEP**: Require a .txt suffix to match prompt loader expectations.
        if not value.endswith(".txt"):
            raise ValueError("AGENT_SYSTEM_PROMPT_NAME must end with .txt")
        return value
