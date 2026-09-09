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
    # NOTE: In "probe" mode (non-mock), every /health/ready round-trips to the real provider —
    # llm_service_readiness.py's _run_readiness_probe calls async_client.create(...) or
    # llm.ainvoke(...). "init" checks only that the client was constructed at startup, at zero
    # ongoing provider cost, which is why it is the default: it never depends on a third party
    # being reachable to answer "is this pod up", and that dependency is exactly what "probe"
    # trades in.
    #
    # check_readiness() caches a live probe result for 30s (_PROBE_CACHE_TTL_SECONDS in
    # llm_service_readiness.py — read that comment for the call-rate arithmetic and the
    # success/failure caching trade-off), bounding "probe"'s provider-call cost. Whether the LLM
    # check gets a vote in the overall readiness verdict is `llm_readiness_critical` below
    # (AGENT_LLM_READINESS_CRITICAL, default false) — see ADR-008 for the general principle,
    # rather than repeating it here. What is specific to *this* field, not to that principle:
    # "probe" mode still round-trips the provider on every poll (bounded by the 30s cache above)
    # even with `llm_readiness_critical=false`; that cost does not go away, only the eviction
    # consequence does. A project that sets `llm_readiness_critical=true` together with "probe"
    # should also raise the Kubernetes readiness `periodSeconds` and `failureThreshold` to
    # comfortably exceed the 30s cache TTL — otherwise the cache and the k8s failure count fight
    # over the same window instead of the operator choosing one.
    llm_readiness_check_mode: Literal["probe", "init"] = Field(
        default="init",
        # The description states what each mode does and the one consequence a reader who never
        # sees this file still has to know. The derivation behind it — the cache TTL, the call
        # arithmetic, the criticality chain in health.py — is in the NOTE above and is not
        # repeated here: a schema string and a code comment that both carry the same reasoning
        # are two copies that drift, and only one of them is anywhere near the code that would
        # change.
        description=(
            "Readiness check mode for the LLM service: 'init' (default) verifies only that the "
            "provider client was constructed at startup, at zero ongoing cost; 'probe' calls the "
            "provider on every poll. Whether that call can fail the overall verdict is a separate "
            "setting, llm_readiness_critical (default false) — see the note above the field for why"
        ),
    )

    # ATTRIBUTE: llm_readiness_timeout_seconds (float)
    # SUMMARY: Timeout in seconds for external LLM readiness probes.
    llm_readiness_timeout_seconds: float = Field(
        default=5.0,
        gt=0.0,
        description="Timeout in seconds for LLM readiness probe calls",
    )

    # ATTRIBUTE: llm_readiness_critical (bool)
    # SUMMARY: Whether an unhealthy LLM check can flip the overall /health/ready verdict.
    # NOTE: Default false. Full reasoning in ADR-008 — this is a pointer, not a second copy of it.
    # The short version: the shipped reference vertical is storage-only and never calls the model,
    # and every replica of a deployment shares the same third-party provider, so an unconditional
    # vote turns one provider's bad minute into every replica failing readiness at once, with
    # nowhere for Kubernetes to evict to. The check still runs and `checks.llm` in the response
    # body still reports its real status either way (see health.py's `critical` flag per check) —
    # this setting only decides whether that status joins the critical set. Set true when this
    # deployment's own endpoints genuinely cannot answer without the model.
    llm_readiness_critical: bool = Field(
        default=False,
        description=(
            "Whether the LLM readiness check counts toward the overall /health/ready verdict. "
            "The check always runs and is always reported in checks.llm; this only controls "
            "whether an unhealthy result returns HTTP 503. See ADR-008 for the reasoning behind "
            "the default of false."
        ),
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
