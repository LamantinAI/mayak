# FILE: tests/application/test_llm_service_retry.py
# SUMMARY: Tests for the retry policy and token accounting of live LLM calls.
#
# The retry loop, the timeout, and the token counters shipped untested: no test simulated a provider
# failure, and none asserted that usage_metadata reached the log. A silent change to the retryable
# exception set, the attempt count, or the usage parsing would have gone unnoticed until production.

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from openai import APITimeoutError, RateLimitError
from tenacity import wait_none

from project.infrastructure.agents import llm_service_live as live_module
from project.infrastructure.agents.llm_service_live import LLMServiceLiveMixin
from tests.conftest import _FixtureSettings as FixtureSettings


# FUNCTION: _timeout_error
# SUMMARY: Build the provider timeout the retry policy is supposed to treat as retryable.
def _timeout_error() -> APITimeoutError:
    return APITimeoutError(request=httpx.Request("POST", "https://provider.invalid/v1"))


# FUNCTION: _rate_limit_error
# SUMMARY: Build a provider rate-limit error, the second retryable shape.
def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://provider.invalid/v1")
    response = httpx.Response(status_code=429, request=request)
    return RateLimitError("slow down", response=response, body=None)


# CLASS: tests.application.test_llm_service_retry._RetryHarness
# SUMMARY: Live mixin plus the two contract attributes LLMService.__init__ normally supplies.
class _RetryHarness(LLMServiceLiveMixin):
    # **LOGIC_STEP**: The mixin declares only what it assigns itself; `_settings` and `_logger`
    # come from the composed LLMService. These tests build the mixin alone, so the harness
    # declares them here instead — as Any, because both are stubs and a narrower type would make
    # mypy reject the MagicMock the assertions read `call_args` from.

    # ATTRIBUTE: _settings (Any)
    # SUMMARY: Stubbed application settings supplied by the test fixture.
    _settings: Any

    # ATTRIBUTE: _logger (Any)
    # SUMMARY: MagicMock standing in for the semantic logger so calls can be inspected.
    _logger: Any


# FUNCTION: _build_instance
# SUMMARY: Assemble the live mixin with the minimum contract attributes plus a scripted provider.
def _build_instance(
    test_settings: FixtureSettings,
    *,
    side_effect: Any = None,
    return_value: Any = None,
) -> _RetryHarness:
    instance = _RetryHarness.__new__(_RetryHarness)
    runnable = AsyncMock()
    runnable.ainvoke = AsyncMock(side_effect=side_effect, return_value=return_value)
    instance._bound_llm = runnable
    instance._settings = test_settings
    instance._logger = MagicMock()
    return instance


# FUNCTION: no_backoff
# SUMMARY: Strip the exponential wait so the retry tests measure behaviour, not wall-clock sleep.
@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    # **LOGIC_STEP**: wait_exponential is resolved from the module namespace at call time, so
    # replacing the name is enough — the real policy keeps its min=2s backoff in production.
    monkeypatch.setattr(live_module, "wait_exponential", lambda **_: wait_none())


# CLASS: tests.application.test_llm_service_retry.TestRetryPolicy
# SUMMARY: What the retry loop does with retryable failures, permanent failures, and success.
class TestRetryPolicy:
    # FUNCTION: test_transient_failure_is_retried_until_success
    # SUMMARY: Two timeouts followed by a reply must produce a reply, not an error.
    @pytest.mark.unit
    async def test_transient_failure_is_retried_until_success(
        self, test_settings: FixtureSettings
    ) -> None:
        instance = _build_instance(
            test_settings,
            side_effect=[_timeout_error(), _rate_limit_error(), AIMessage(content="ok")],
        )

        result = await instance._call_llm_with_retry([HumanMessage(content="hi")])

        assert result.content == "ok"
        assert instance._bound_llm.ainvoke.await_count == 3
        # **LOGIC_STEP**: Each failed attempt is logged as its own llm.call with success=False
        # before the retry, and only the last one as success=True. Nothing read these calls until
        # 2026-09-02: flipping the retry branch to success=True left every gate green, so a log
        # in which every timeout looked like a completed call would have shipped. The two
        # assertions on `TestTokenAccounting` read `call_args` — the last call only — and never
        # execute the retry branch at all.
        calls = instance._logger.log_llm_call.call_args_list
        assert [call.kwargs["success"] for call in calls] == [False, False, True]
        assert calls[0].kwargs["error"] == "Request timed out."
        assert calls[1].kwargs["error"] == "slow down"
        assert "error" not in calls[2].kwargs
        retry_errors = [
            call.kwargs["error_type"] for call in instance._logger.log_error.call_args_list
        ]
        assert retry_errors == ["llm_call_retryable_error", "llm_call_retryable_error"]

    # FUNCTION: test_attempts_stop_at_the_configured_limit
    # SUMMARY: A provider that never recovers must raise after exactly max_llm_call_retries tries.
    @pytest.mark.unit
    async def test_attempts_stop_at_the_configured_limit(
        self, test_settings: FixtureSettings
    ) -> None:
        limit = test_settings.agent.max_llm_call_retries
        instance = _build_instance(test_settings, side_effect=_timeout_error())

        with pytest.raises(APITimeoutError):
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

        assert instance._bound_llm.ainvoke.await_count == limit

    # FUNCTION: test_unexpected_error_is_not_retried
    # SUMMARY: Only the provider's transient shapes are retryable; a bug must surface immediately.
    @pytest.mark.unit
    async def test_unexpected_error_is_not_retried(self, test_settings: FixtureSettings) -> None:
        instance = _build_instance(test_settings, side_effect=ValueError("bad argument"))

        with pytest.raises(ValueError):
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

        assert instance._bound_llm.ainvoke.await_count == 1

    # FUNCTION: test_uninitialised_provider_is_named
    # SUMMARY: Calling before the provider exists reports which component is missing.
    @pytest.mark.unit
    async def test_uninitialised_provider_is_named(self, test_settings: FixtureSettings) -> None:
        instance = _build_instance(test_settings, return_value=AIMessage(content="ok"))
        instance._bound_llm = None

        with pytest.raises(Exception, match="not initialized"):
            await instance._call_llm_with_retry([HumanMessage(content="hi")])


# CLASS: tests.application.test_llm_service_retry.TestTokenAccounting
# SUMMARY: Token counters reported by the provider must reach the log event.
class TestTokenAccounting:
    # FUNCTION: test_usage_metadata_reaches_the_log_event
    # SUMMARY: input/output/total tokens are read off the reply and passed to log_llm_call.
    @pytest.mark.unit
    async def test_usage_metadata_reaches_the_log_event(
        self, test_settings: FixtureSettings
    ) -> None:
        reply = AIMessage(
            content="ok",
            usage_metadata={"input_tokens": 11, "output_tokens": 5, "total_tokens": 16},
        )
        instance = _build_instance(test_settings, return_value=reply)

        await instance._call_llm_with_retry([HumanMessage(content="hi")])

        kwargs = instance._logger.log_llm_call.call_args.kwargs
        assert kwargs["input_tokens"] == 11
        assert kwargs["output_tokens"] == 5
        assert kwargs["total_tokens"] == 16
        assert kwargs["success"] is True

    # FUNCTION: test_missing_usage_metadata_logs_none_not_zero
    # SUMMARY: A provider that reports no usage must not be recorded as having spent zero tokens.
    @pytest.mark.unit
    async def test_missing_usage_metadata_logs_none_not_zero(
        self, test_settings: FixtureSettings
    ) -> None:
        instance = _build_instance(test_settings, return_value=AIMessage(content="ok"))

        await instance._call_llm_with_retry([HumanMessage(content="hi")])

        kwargs = instance._logger.log_llm_call.call_args.kwargs
        assert kwargs["input_tokens"] is None
        assert kwargs["total_tokens"] is None
