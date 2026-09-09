# FILE: tests/application/test_llm_service.py
# SUMMARY: Unit tests for mock-mode LLM service behavior and readiness semantics.

from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from project.core.config import Settings
from project.infrastructure.agents import llm_service_readiness as readiness_module
from project.infrastructure.agents.llm_service import LLMService
from tests.conftest import _FixtureSettings as FixtureSettings


# CLASS: tests.application.test_llm_service.DummyTool
# SUMMARY: Lightweight tool stub exposing only the stable name used by mock-mode routing.
class DummyTool:
    name = "example_lookup"


# CLASS: tests.application.test_llm_service.TestLLMService
# SUMMARY: Verify deterministic mock-mode behavior without external providers.
class TestLLMService:
    # FUNCTION: _create_service
    # SUMMARY: Build an LLMService instance with patched settings.
    # INPUT: test_settings (FixtureSettings): Test settings fixture.
    # OUTPUT: (LLMService): LLM service configured for the requested mode.
    def _create_service(self, test_settings: FixtureSettings) -> LLMService:
        with patch(
            "project.infrastructure.agents.llm_service.get_settings",
            return_value=test_settings,
        ):
            return LLMService()

    # FUNCTION: test_check_readiness_reports_mock_mode_as_healthy
    # SUMMARY: Verify mock mode does not depend on external provider reachability.
    @pytest.mark.unit
    async def test_check_readiness_reports_mock_mode_as_healthy(
        self,
        test_settings: FixtureSettings,
    ) -> None:
        service = self._create_service(test_settings)

        readiness = await service.check_readiness()

        assert readiness["status"] == "healthy"
        assert readiness["backend_mode"] == "mock"
        assert readiness["provider_reachable"] is None

    # FUNCTION: test_call_generates_a_mock_tool_call_whatever_the_prompt_says
    # SUMMARY: Regression guard: without this, the mock requires one of five English words in the
    # prompt, so an agentic vertical with a domain vocabulary cannot be exercised without a live
    # provider.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "prompt",
        [
            "Show template capabilities for this backend",
            "今週のトレーニング計画を立てて",
            "wie viele Einträge gibt es",
        ],
    )
    async def test_call_generates_a_mock_tool_call_whatever_the_prompt_says(
        self,
        test_settings: FixtureSettings,
        prompt: str,
    ) -> None:
        service = self._create_service(test_settings).bind_tools([DummyTool()])

        response = await service.call([HumanMessage(content=prompt)])

        assert isinstance(response, AIMessage)
        assert response.tool_calls
        assert response.tool_calls[0]["name"] == "example_lookup"

    # FUNCTION: test_no_tools_bound_means_no_tool_call
    # SUMMARY: Verify the plain-text branch still exists: with nothing bound, mock mode answers in words.
    @pytest.mark.unit
    async def test_no_tools_bound_means_no_tool_call(
        self,
        test_settings: FixtureSettings,
    ) -> None:
        service = self._create_service(test_settings)

        response = await service.call([HumanMessage(content="Show template capabilities")])

        assert isinstance(response, AIMessage)
        assert not response.tool_calls

    # FUNCTION: test_call_summarizes_trailing_tool_output
    # SUMMARY: Verify mock mode turns tool results into a final assistant answer.
    @pytest.mark.unit
    async def test_call_summarizes_trailing_tool_output(
        self,
        test_settings: FixtureSettings,
    ) -> None:
        service = self._create_service(test_settings)

        response = await service.call(
            [
                HumanMessage(content="Show template capabilities"),
                ToolMessage(
                    content='{"status":"ok"}',
                    name="example_lookup",
                    tool_call_id="tool-1",
                ),
            ]
        )

        assert "Tool-assisted summary" in str(response.content)
        assert "example_lookup" in str(response.content)


# CLASS: tests.application.test_llm_service.SecondDummyTool
# SUMMARY: A second tool stub, so selection can be observed rather than assumed.
class SecondDummyTool:
    name = "example_second"


# CLASS: tests.application.test_llm_service.TestMockToolSelectionIsDeterministic
# SUMMARY: Verify mock mode always picks the first bound tool.
class TestMockToolSelectionIsDeterministic:
    # FUNCTION: test_first_bound_tool_wins_with_several_tools
    # SUMMARY: Verify selection is positional and stable, not arbitrary among the bound tools.
    @pytest.mark.unit
    @pytest.mark.parametrize("attempt", range(5))
    async def test_first_bound_tool_wins_with_several_tools(
        self,
        test_settings: FixtureSettings,
        attempt: int,
    ) -> None:
        # **LOGIC_STEP**: Every existing test binds exactly one tool, so replacing
        # `self._mock_tools[0]` with a random choice changed nothing observable and the suite
        # stayed green. Determinism is the entire promise of mock mode (ADR-003) — with one tool
        # bound it cannot be tested at all, so two are bound here and the choice repeated.
        del attempt
        with patch(
            "project.infrastructure.agents.llm_service.get_settings",
            return_value=test_settings,
        ):
            service = LLMService()
        service = service.bind_tools([DummyTool(), SecondDummyTool()])

        response = await service.call(
            [HumanMessage(content="Show template capabilities for this backend")]
        )

        assert isinstance(response, AIMessage)
        assert response.tool_calls
        assert response.tool_calls[0]["name"] == DummyTool.name


# CLASS: tests.application.test_llm_service.TestBindingIsPerCaller
# SUMMARY: Verify one vertical binding tools cannot change what another vertical sees.
class TestBindingIsPerCaller:
    # FUNCTION: test_a_second_binding_does_not_steal_the_first_ones_tools
    # SUMMARY: Regression guard: CompositionRoot builds one LLMService for the whole app; if
    # bind_tools wrote into it directly instead of returning a bound copy, the last vertical to
    # bind would replace every earlier binding with no error.
    @pytest.mark.unit
    async def test_a_second_binding_does_not_steal_the_first_ones_tools(
        self,
        test_settings: FixtureSettings,
    ) -> None:
        with patch(
            "project.infrastructure.agents.llm_service.get_settings",
            return_value=test_settings,
        ):
            shared = LLMService()

        first = shared.bind_tools([DummyTool()])
        second = shared.bind_tools([SecondDummyTool()])

        first_response = await first.call([HumanMessage(content="anything at all")])
        second_response = await second.call([HumanMessage(content="anything at all")])

        assert isinstance(first_response, AIMessage)
        assert isinstance(second_response, AIMessage)
        assert first_response.tool_calls[0]["name"] == DummyTool.name
        assert second_response.tool_calls[0]["name"] == SecondDummyTool.name
        # **LOGIC_STEP**: The shared instance stays unbound. A vertical that never called
        # bind_tools must not inherit another vertical's tools through the composition root.
        shared_response = await shared.call([HumanMessage(content="anything at all")])
        assert isinstance(shared_response, AIMessage)
        assert not shared_response.tool_calls


# CLASS: tests.application.test_llm_service.TestLiveReadinessBranches
# SUMMARY: Verify the live-mode readiness paths that health tests replace with a mock.
class TestLiveReadinessBranches:
    # FUNCTION: _live_service
    # SUMMARY: Build a service in live mode with the requested readiness check mode.
    # OUTPUT: (LLMService): Service whose settings declare live mode.
    def _live_service(self, mode: Literal["probe", "init"]) -> LLMService:
        # **LOGIC_STEP**: A fresh settings object, never the session-scoped `test_settings`
        # fixture. Flipping llm_mode to "live" on the shared instance would leak into every test
        # that runs after this class and make failures depend on collection order.
        settings = FixtureSettings()
        settings.agent.llm_mode = "live"
        settings.agent.llm_readiness_check_mode = mode
        with patch(
            "project.infrastructure.agents.llm_service.get_settings",
            return_value=settings,
        ):
            return LLMService()

    # FUNCTION: test_init_mode_reports_healthy_without_touching_the_provider
    # SUMMARY: Verify `init` answers from the client's existence and never calls out.
    @pytest.mark.unit
    async def test_init_mode_reports_healthy_without_touching_the_provider(self) -> None:
        service = self._live_service("init")

        with patch.object(service, "_run_readiness_probe") as probe:
            readiness = await service.check_readiness()

        assert readiness["status"] == "healthy"
        assert readiness["backend_mode"] == "live"
        assert readiness["provider_reachable"] is None
        probe.assert_not_called()

    # FUNCTION: test_probe_failure_reports_unhealthy_instead_of_raising
    # SUMMARY: Verify an unreachable provider degrades readiness rather than crashing the endpoint.
    @pytest.mark.unit
    async def test_probe_failure_reports_unhealthy_instead_of_raising(self) -> None:
        # **LOGIC_STEP**: /health/ready calls this. An exception escaping here turns a readiness
        # probe into a 500 from the health endpoint itself — the one endpoint that must answer
        # while everything else is broken. The health tests replace check_readiness with an
        # AsyncMock, so this branch had no coverage anywhere.
        service = self._live_service("probe")

        async def _boom() -> None:
            raise TimeoutError("provider did not answer")

        with patch.object(service, "_run_readiness_probe", _boom):
            readiness = await service.check_readiness()

        assert readiness["status"] == "unhealthy"
        assert readiness["backend_mode"] == "live"
        assert readiness["provider_reachable"] is False
        assert readiness["error"] == "TimeoutError"

    # FUNCTION: test_successful_probe_reports_reachable
    # SUMMARY: Verify the healthy live branch records the provider as reachable with a latency.
    @pytest.mark.unit
    async def test_successful_probe_reports_reachable(self) -> None:
        service = self._live_service("probe")

        async def _ok() -> None:
            return None

        with patch.object(service, "_run_readiness_probe", _ok):
            readiness = await service.check_readiness()

        assert readiness["status"] == "healthy"
        assert readiness["provider_reachable"] is True
        assert readiness["response_time_ms"] >= 0


# CLASS: tests.application.test_llm_service._FakeMonotonicClock
# SUMMARY: Controllable stand-in for time.monotonic(), so the TTL-expiry test advances the cache's
# notion of time explicitly instead of sleeping for real and paying the TTL in wall-clock seconds.
class _FakeMonotonicClock:
    # FUNCTION: __init__
    # SUMMARY: Start the fake clock at a fixed reading.
    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    # FUNCTION: read
    # SUMMARY: Return the current fake reading. Matches time.monotonic()'s zero-argument signature
    # so it can replace it directly via monkeypatch.setattr.
    def read(self) -> float:
        return self._now

    # FUNCTION: advance
    # SUMMARY: Move the fake clock forward by the given number of seconds.
    def advance(self, seconds: float) -> None:
        self._now += seconds


# CLASS: tests.application.test_llm_service.TestReadinessProbeCaching
# SUMMARY: Verify the TTL cache added to probe mode: one provider call per TTL window, a fresh call
# once the TTL elapses, and that caching never turns a real failure into a reported success.
class TestReadinessProbeCaching:
    # FUNCTION: _live_service
    # SUMMARY: Build a service in live mode with the requested readiness check mode.
    # OUTPUT: (LLMService): Service whose settings declare live mode.
    def _live_service(self, mode: Literal["probe", "init"]) -> LLMService:
        # **LOGIC_STEP**: A fresh settings object per service, matching TestLiveReadinessBranches —
        # each test in this class needs its own cold _probe_cache, and a fresh LLMService() is what
        # gives it one (the cache lives on the instance, not the shared session-scoped fixture).
        settings = FixtureSettings()
        settings.agent.llm_mode = "live"
        settings.agent.llm_readiness_check_mode = mode
        with patch(
            "project.infrastructure.agents.llm_service.get_settings",
            return_value=settings,
        ):
            return LLMService()

    # FUNCTION: test_two_consecutive_probes_inside_ttl_call_the_provider_once
    # SUMMARY: Verify the second of two back-to-back check_readiness() calls is served from cache.
    @pytest.mark.unit
    async def test_two_consecutive_probes_inside_ttl_call_the_provider_once(self) -> None:
        service = self._live_service("probe")
        probe = AsyncMock(return_value=None)

        with patch.object(service, "_run_readiness_probe", probe):
            first = await service.check_readiness()
            second = await service.check_readiness()

        # **LOGIC_STEP**: Call count, not just "was it called" — two check_readiness() calls
        # inside the TTL must still mean exactly one round-trip to the provider.
        assert probe.await_count == 1
        assert first["status"] == "healthy"
        assert first["cached"] is False
        assert second["status"] == "healthy"
        assert second["cached"] is True
        assert second["cache_age_seconds"] >= 0.0

    # FUNCTION: test_probe_after_ttl_elapsed_calls_the_provider_again
    # SUMMARY: Verify a call made after _PROBE_CACHE_TTL_SECONDS has passed re-probes the provider,
    # using a controlled clock instead of a real sleep so the test stays fast.
    @pytest.mark.unit
    async def test_probe_after_ttl_elapsed_calls_the_provider_again(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = self._live_service("probe")
        probe = AsyncMock(return_value=None)
        clock = _FakeMonotonicClock(start=0.0)
        monkeypatch.setattr(readiness_module.time, "monotonic", clock.read)

        with patch.object(service, "_run_readiness_probe", probe):
            first = await service.check_readiness()
            clock.advance(readiness_module._PROBE_CACHE_TTL_SECONDS + 1.0)
            second = await service.check_readiness()

        assert probe.await_count == 2
        assert first["cached"] is False
        assert second["cached"] is False

    # FUNCTION: test_a_failing_probe_stays_unhealthy_when_served_from_cache
    # SUMMARY: Regression guard: the cache stores failures too, and must not turn a cached failure
    # into a reported success on the second call.
    @pytest.mark.unit
    async def test_a_failing_probe_stays_unhealthy_when_served_from_cache(self) -> None:
        service = self._live_service("probe")
        probe = AsyncMock(side_effect=TimeoutError("provider did not answer"))

        with patch.object(service, "_run_readiness_probe", probe):
            first = await service.check_readiness()
            second = await service.check_readiness()

        assert probe.await_count == 1
        assert first["status"] == "unhealthy"
        assert first["error"] == "TimeoutError"
        assert second["status"] == "unhealthy"
        assert second["error"] == "TimeoutError"
        assert second["cached"] is True

    # FUNCTION: test_mock_mode_makes_zero_provider_calls_and_stays_healthy
    # SUMMARY: Verify the cache change left the mock-mode early return untouched: still zero calls
    # to the provider probe, still healthy.
    @pytest.mark.unit
    async def test_mock_mode_makes_zero_provider_calls_and_stays_healthy(
        self,
        test_settings: FixtureSettings,
    ) -> None:
        with patch(
            "project.infrastructure.agents.llm_service.get_settings",
            return_value=test_settings,
        ):
            service = LLMService()
        probe = AsyncMock(return_value=None)

        with patch.object(service, "_run_readiness_probe", probe):
            readiness = await service.check_readiness()

        probe.assert_not_called()
        assert readiness["status"] == "healthy"
        assert readiness["backend_mode"] == "mock"


# CLASS: tests.application.test_llm_service.TestMockModeSurvivesTheOperatorsEnvironment
# SUMMARY: Verify a service built from process-wide settings lands in mock mode, not the live path.
# NOTE: This is the trap for tests/conftest.py::pin_llm_mode_toggle, and it is the only test here
# that reads real settings instead of the fixture's. Every other test in this file patches
# get_settings, so all of them stay green while an operator's own .env carries AGENT_LLM_MODE=live
# and any test constructing LLMService() directly builds a real provider client. Delete the
# fixture and this goes red on that machine; it is green everywhere the environment is silent,
# which is every fresh checkout and CI.
class TestMockModeSurvivesTheOperatorsEnvironment:
    # FUNCTION: test_settings_read_from_the_environment_select_mock_mode
    # SUMMARY: Verify unpatched settings put the service in mock mode.
    # OUTPUT: (None): None.
    @pytest.mark.unit
    def test_settings_read_from_the_environment_select_mock_mode(self) -> None:
        # **LOGIC_STEP**: Settings() is built here rather than taken from get_settings() because
        # that one is cached process-wide — a cached instance would answer for whatever the
        # environment said the first time anything asked, which is not what this claims.
        from_environment = Settings()

        with patch(
            "project.infrastructure.agents.llm_service.get_settings",
            return_value=from_environment,
        ):
            service = LLMService()

        assert from_environment.agent.llm_mode == "mock"
        assert service.is_mock_mode() is True
