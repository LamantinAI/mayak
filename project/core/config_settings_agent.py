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
    # NOTE: Default changed from "probe" to "init" on 2026-08-24. In "probe" mode (non-mock),
    # every /health/ready round-trips to the real provider — llm_service_readiness.py's
    # _run_readiness_probe calls async_client.create(...) or llm.ainvoke(...). "init" checks only
    # that the client was constructed at startup, at zero ongoing provider cost, which is why it
    # stays the default: it never depends on a third party being reachable to answer "is this pod
    # up", and that dependency is exactly what "probe" trades in.
    #
    # 2026-08-24, second pass: the first pass above closed the default but was incomplete on two
    # counts, found by a completeness review of the same fix.
    #   1. check_readiness() now caches a live probe result for 30s
    #      (_PROBE_CACHE_TTL_SECONDS in llm_service_readiness.py — read that comment for the
    #      call-rate arithmetic and the success/failure caching trade-off). This bounds "probe"'s
    #      provider-call cost; it does not touch the second problem.
    #   2. health.py's _build_readiness_response still puts checks["llm"]["status"] into
    #      critical_statuses unconditionally (health.py line ~202-205), unlike the database check,
    #      which is added conditionally and only when the project actually uses one (line ~206-207).
    #      That file is out of scope for this change. So today, in "probe" mode, a provider outage
    #      — a 429, a timeout, an expired key — still turns /health/ready unhealthy, still returns
    #      503, and Kubernetes still evicts the pod, now at most once per 30s instead of once per
    #      poll but with the exact same outcome once it happens. Every replica of a service shares
    #      the same provider and the same rate limit, so they go together. The cache changes how
    #      often the question gets asked; it does not change what happens when the answer is no.
    #
    # "probe" is still the right choice when reaching the model really is the readiness question —
    # e.g. validating a freshly rotated API key before routing traffic to a replica — but it is a
    # recommendation with conditions attached now, not a bare one:
    #   - raise the Kubernetes readiness `periodSeconds` and `failureThreshold` so the effective
    #     failure window is comfortably wider than the 30s cache TTL above — otherwise the cache
    #     and the k8s failure count fight over the same window instead of the operator choosing one;
    #   - accept, explicitly, that readiness is now coupled to a third party's availability, because
    #     item 2 above means that coupling is real and this file cannot fix it alone.
    # A project that decides the unconditional-criticality behavior itself should change is making
    # a call this settings file does not get to make silently — that edit belongs in health.py,
    # as a deliberate, reviewed change, not as a side effect of picking a Literal value here.
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
            "provider, which couples this service's readiness to that provider's availability. "
            "Choose 'probe' only with a readiness-probe interval and failureThreshold raised to "
            "match; see the note above the field for why"
        ),
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
