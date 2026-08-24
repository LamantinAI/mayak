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

# ATTRIBUTE: _PROBE_CACHE_TTL_SECONDS (float)
# SUMMARY: How long a live-probe result (success or failure) is reused before check_readiness()
# fires another provider request.
# NOTE: Uncached, a Kubernetes readiness probe at the common 10s interval makes 86,400 / 10 = 8,640
# provider calls per day per replica — the finding that started this change (arithmetic and the
# "no caching anywhere" gap are recorded in the NOTE above AgentSettings.llm_readiness_check_mode in
# config_settings_agent.py; this file is where the fix actually lives). At 30s, check_readiness()
# only re-probes once the cached result is older than the TTL, so the same 10s poll interval
# produces at most 86,400 / 30 = 2,880 provider calls per day — a 3x cut. That is not a full fix for
# a provider that bills per call; it is enough that "probe" stops meaning "every readiness poll is a
# paid API call."
#
# Both outcomes are cached — success and failure alike — not success only. Caching only success
# would re-hit the provider on every single poll of a provider that has started failing, which is
# the worst possible moment: exactly when it is least able to answer and most likely already
# rate-limiting. The cost of caching failure too is the mirror case — a provider that recovers
# mid-window is still reported unhealthy for up to 30s after it is actually back — so a project that
# pairs "probe" with a Kubernetes failureThreshold tighter than this window can see a readiness flap
# driven by the cache rather than by the provider; see the NOTE in config_settings_agent.py for the
# "raise the interval to match" operational condition that follows from this.
#
# 30s over the alternatives: 10s buys nothing (the cache would already be stale by the next poll at
# the common interval), and 5 minutes (86,400 / 300 = 288 calls/day) is far cheaper but makes the
# recovery-detection lag above worse than the k8s coupling this cache is meant to soften, not worsen.
_PROBE_CACHE_TTL_SECONDS = 30.0


# CLASS: project.infrastructure.agents.llm_service_readiness._LLMServiceReadinessContract
# SUMMARY: Structural contract describing the shared LLMService state used by the readiness mixin.
class _LLMServiceReadinessContract(Protocol):
    # ATTRIBUTE: _settings (Settings)
    # SUMMARY: Validated application settings used to configure readiness behavior.
    _settings: Settings

    # ATTRIBUTE: _logger (SemanticLogger)
    # SUMMARY: Semantic logger used for readiness telemetry and failures.
    _logger: SemanticLogger

    # ATTRIBUTE: _probe_cache (dict[str, Any] | None)
    # SUMMARY: Last live-probe result recorded by check_readiness (success or failure), or None
    # before this instance has run a probe. Declared here, not just on the mixin, because
    # check_readiness accesses it through this Protocol's `self` type.
    _probe_cache: dict[str, Any] | None

    # ATTRIBUTE: _probe_cache_at (float | None)
    # SUMMARY: time.monotonic() reading taken when _probe_cache was recorded, or None before the
    # first probe. Monotonic, not wall-clock, so a system clock step cannot expire or extend the
    # cache window.
    _probe_cache_at: float | None

    # ATTRIBUTE: _probe_lock (asyncio.Lock | None)
    # SUMMARY: Per-instance lock serializing concurrent probe attempts on a cold or expired cache.
    # None until the first probe call lazily creates it (see LLMServiceReadinessMixin).
    _probe_lock: asyncio.Lock | None

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

    # FUNCTION: _execute_live_probe
    # SUMMARY: Run one uncached provider probe attempt and shape it into a readiness payload.
    async def _execute_live_probe(self, mode: str, model: str) -> dict[str, Any]: ...

    # FUNCTION: _fresh_cached_probe
    # SUMMARY: Return a stamped cached probe result inside the TTL, or None.
    def _fresh_cached_probe(self) -> dict[str, Any] | None: ...

    # FUNCTION: _cached_or_fresh_probe
    # SUMMARY: Serve a cached probe result inside the TTL, or run and cache one fresh probe.
    async def _cached_or_fresh_probe(self, mode: str, model: str) -> dict[str, Any]: ...


# CLASS: project.infrastructure.agents.llm_service_readiness.LLMServiceReadinessMixin
# SUMMARY: Mixin implementing LLM readiness probes and stable safe readiness payloads.
class LLMServiceReadinessMixin:
    # ATTRIBUTE: _probe_cache (dict[str, Any] | None)
    # SUMMARY: Class-level default of "never probed". LLMService.__init__ does not set this — a
    # plain class attribute already satisfies the Protocol structurally, and the first probe turns
    # it into a real instance attribute via ordinary assignment, so no __init__ edit is needed.
    _probe_cache: dict[str, Any] | None = None

    # ATTRIBUTE: _probe_cache_at (float | None)
    # SUMMARY: Class-level default paired with _probe_cache; see its comment.
    _probe_cache_at: float | None = None

    # ATTRIBUTE: _probe_lock (asyncio.Lock | None)
    # SUMMARY: Class-level default of "no lock yet". Left unconstructed at class-definition time on
    # purpose — a single asyncio.Lock() assigned here would be one object shared by every
    # LLMService instance ever created (a plain class attribute, not a per-instance one), so two
    # unrelated services would serialize probes against each other for no reason. Built lazily,
    # per instance, on first use instead — see _cached_or_fresh_probe.
    _probe_lock: asyncio.Lock | None = None

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

        return await self._cached_or_fresh_probe(mode, model)

    # FUNCTION: _execute_live_probe
    # SUMMARY: Run one uncached provider probe attempt and shape it into the live-mode readiness
    # payload, success or failure. Split out of check_readiness so _cached_or_fresh_probe can wrap
    # it with caching without duplicating the try/except/log shape.
    async def _execute_live_probe(
        self: _LLMServiceReadinessContract,
        mode: str,
        model: str,
    ) -> dict[str, Any]:
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

    # FUNCTION: _fresh_cached_probe
    # SUMMARY: Return a stamped copy of the cached probe result if it is still inside
    # _PROBE_CACHE_TTL_SECONDS, or None when there is nothing cached yet or it has expired.
    # NOTE: The stamp (`cached` / `cache_age_seconds`) exists so a reader of /health/ready — human
    # or alerting rule — cannot mistake a 25-second-old answer for a fresh round-trip that just
    # happened. Silently returning the stored dict was considered and rejected: `response_time_ms`
    # on a cached payload would otherwise read as "the provider answered in 40ms just now," which
    # is false on every cache hit after the first.
    def _fresh_cached_probe(
        self: _LLMServiceReadinessContract,
    ) -> dict[str, Any] | None:
        if self._probe_cache is None or self._probe_cache_at is None:
            return None
        age_seconds = time.monotonic() - self._probe_cache_at
        if age_seconds >= _PROBE_CACHE_TTL_SECONDS:
            return None
        stamped = dict(self._probe_cache)
        stamped["cached"] = True
        stamped["cache_age_seconds"] = round(age_seconds, 2)
        return stamped

    # FUNCTION: _cached_or_fresh_probe
    # SUMMARY: Serve a cached live-probe result inside the TTL, or run exactly one fresh probe and
    # cache it (success or failure alike) behind a per-instance lock.
    # NOTE: The lock is created lazily rather than in __init__ (which this mixin does not own) or
    # as a class-level default (see _probe_lock above). Creating it here with a plain
    # `if self._probe_lock is None: self._probe_lock = asyncio.Lock()` is safe without a second
    # lock guarding the check-and-create itself: nothing in that line awaits, and asyncio only
    # switches coroutines at an `await`, so no other coroutine can observe the attribute between
    # the check and the assignment. The classic double-checked-locking hazard applies across
    # threads or across an await point; neither is present here.
    async def _cached_or_fresh_probe(
        self: _LLMServiceReadinessContract,
        mode: str,
        model: str,
    ) -> dict[str, Any]:
        cached = self._fresh_cached_probe()
        if cached is not None:
            return cached

        if self._probe_lock is None:
            self._probe_lock = asyncio.Lock()

        async with self._probe_lock:
            # **LOGIC_STEP**: Re-check after acquiring the lock. A second caller that arrived
            # while the first was awaiting the lock must reuse the result the first caller just
            # cached, not fire a second concurrent provider request — that is the whole point of
            # the lock, and skipping this re-check would make it decorative.
            cached = self._fresh_cached_probe()
            if cached is not None:
                return cached

            result = await self._execute_live_probe(mode, model)
            self._probe_cache = result
            self._probe_cache_at = time.monotonic()
            fresh = dict(result)
            fresh["cached"] = False
            fresh["cache_age_seconds"] = 0.0
            return fresh
