# FILE: project/infrastructure/agents/llm_service_readiness.py
# SUMMARY: Readiness mixin for LLMService covering provider probes and safe health responses.

import asyncio
import time
from typing import Any, Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from project.core.config import Settings
from project.core.logging import SemanticLogger
from project.domain.exceptions import ExternalServiceError

# ATTRIBUTE: _LLM_PROVIDER_UNAVAILABLE_MESSAGE (str)
# SUMMARY: Stable readiness message used when the provider client is not available.
_LLM_PROVIDER_UNAVAILABLE_MESSAGE = "LLM service is unavailable"

# ATTRIBUTE: _LLM_PROVIDER_PROBE_FAILED_MESSAGE (str)
# SUMMARY: Stable readiness message used when the provider probe fails.
_LLM_PROVIDER_PROBE_FAILED_MESSAGE = "LLM provider probe failed"


# CLASS: project.infrastructure.agents.llm_service_readiness._LLMServiceReadinessContract
# SUMMARY: Structural contract describing the shared LLMService state used by the readiness mixin.
class _LLMServiceReadinessContract(Protocol):
    # ATTRIBUTE: _settings (Settings)
    # SUMMARY: Validated application settings used to configure readiness behavior.
    _settings: Settings

    # ATTRIBUTE: _logger (SemanticLogger)
    # SUMMARY: Semantic logger used for readiness telemetry and failures.
    _logger: SemanticLogger

    # FUNCTION: get_llm
    # SUMMARY: Return the configured provider client when available.
    def get_llm(self) -> BaseChatModel | None: ...

    # FUNCTION: is_mock_mode
    # SUMMARY: Report whether the service operates in deterministic mock mode.
    # OUTPUT: (bool): True when mock mode is enabled.
    def is_mock_mode(self) -> bool: ...

    # FUNCTION: _run_readiness_probe
    # SUMMARY: Execute the configured provider probe used by readiness checks.
    async def _run_readiness_probe(self) -> None: ...


# CLASS: project.infrastructure.agents.llm_service_readiness.LLMServiceReadinessMixin
# SUMMARY: Mixin implementing LLM readiness probes and stable safe readiness payloads.
class LLMServiceReadinessMixin:
    # FUNCTION: _run_readiness_probe
    # SUMMARY: Execute a minimal network probe against the configured LLM provider.
    async def _run_readiness_probe(
        self: _LLMServiceReadinessContract,
    ) -> None:
        llm = self.get_llm()
        if llm is None:
            raise ExternalServiceError("LLM provider client is not initialized")
        async_client = getattr(llm, "async_client", None)
        if async_client is not None and hasattr(async_client, "create"):
            await async_client.create(
                model=self._settings.llm.model,
                messages=[{"role": "user", "content": "Reply with OK."}],
                max_tokens=1,
            )
            return

        await llm.ainvoke([HumanMessage(content="Reply with OK.")])

    # FUNCTION: check_readiness
    # SUMMARY: Check whether the configured LLM service is ready according to configured readiness mode.
    async def check_readiness(
        self: _LLMServiceReadinessContract,
    ) -> dict[str, Any]:
        if self.is_mock_mode():
            return {
                "status": "healthy",
                "message": "Mock LLM mode enabled",
                "mode": self._settings.agent.llm_readiness_check_mode,
                "model": self._settings.llm.model,
                "provider_reachable": None,
                "backend_mode": "mock",
            }

        try:
            llm = self.get_llm()
        except Exception as error:
            self._logger.log_error(
                error_type="llm_health_check_failed",
                message="LLM service health check failed",
                exception=error,
            )
            return {
                "status": "unhealthy",
                "message": _LLM_PROVIDER_UNAVAILABLE_MESSAGE,
                "mode": self._settings.agent.llm_readiness_check_mode,
                "provider_reachable": False,
            }

        model = getattr(llm, "model_name", self._settings.llm.model)
        mode = self._settings.agent.llm_readiness_check_mode

        if mode == "init":
            return {
                "status": "healthy",
                "message": "LLM client initialized",
                "mode": mode,
                "model": model,
                "provider_reachable": None,
                "backend_mode": "live",
            }

        start_ns = time.perf_counter_ns()
        try:
            await asyncio.wait_for(
                self._run_readiness_probe(),
                timeout=self._settings.agent.llm_readiness_timeout_seconds,
            )
            duration_ms = (time.perf_counter_ns() - start_ns) / 1e6
            self._logger.log_llm_call(
                model,
                duration_ms=duration_ms,
                success=True,
            )
            return {
                "status": "healthy",
                "message": "LLM provider reachable",
                "mode": mode,
                "model": model,
                "provider_reachable": True,
                "response_time_ms": round(duration_ms, 2),
                "backend_mode": "live",
            }
        except Exception as error:
            duration_ms = (time.perf_counter_ns() - start_ns) / 1e6
            self._logger.log_llm_call(
                model,
                duration_ms=duration_ms,
                success=False,
                error=str(error),
            )
            self._logger.log_error(
                error_type="llm_readiness_probe_failed",
                message="LLM readiness probe failed",
                exception=error,
            )
            return {
                "status": "unhealthy",
                "message": _LLM_PROVIDER_PROBE_FAILED_MESSAGE,
                "mode": mode,
                "model": model,
                "provider_reachable": False,
                "response_time_ms": round(duration_ms, 2),
                "error": type(error).__name__,
                "backend_mode": "live",
            }
