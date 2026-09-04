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
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError as OpenAIAuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
from tenacity import wait_none

from project.domain.exceptions import ExternalServiceError, UpstreamAuthenticationError
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


# FUNCTION: _status_error
# SUMMARY: Build any openai status error, so one helper covers the whole translation matrix.
# INPUT: error_class (type[APIStatusError]): The provider exception class to construct.
# INPUT: status_code (int): HTTP status the provider would have answered with.
def _status_error(error_class: type[APIStatusError], status_code: int) -> APIStatusError:
    request = httpx.Request("POST", "https://provider.invalid/v1")
    response = httpx.Response(status_code=status_code, request=request)
    return error_class("provider said no", response=response, body=None)


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
        provider_error = _timeout_error()
        instance = _build_instance(test_settings, side_effect=provider_error)

        # **LOGIC_STEP**: A retryable failure that used up its attempts leaves as a domain error,
        # like every other provider failure — what the caller sees must not depend on which shape
        # of provider trouble it was. The provider exception survives on __cause__ for the log.
        with pytest.raises(ExternalServiceError) as raised:
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

        assert instance._bound_llm.ainvoke.await_count == limit
        assert raised.value.__cause__ is provider_error

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


# CLASS: tests.application.test_llm_service_retry.TestProviderErrorsBecomeDomainErrors
# SUMMARY: Every shape of provider failure, and the domain error the caller is given instead.
# NOTE: Until 2026-09-04 the adapter re-raised the provider's own exception, so a dead API key, a
# rate limit that outlived its retries and a provider outage were one indistinguishable 500 —
# nothing in the kernel translated them, and no test asked. The point of the matrix is that the
# caller's contract is the domain type, not the vendor's: a project that swaps langchain-openai
# for another client changes this file and nothing downstream of it.
class TestProviderErrorsBecomeDomainErrors:
    # FUNCTION: test_rejected_credentials_are_named_as_the_upstream_side
    # SUMMARY: A provider that refuses our key raises the upstream-credential error, not the
    # caller-facing one.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("error_class", "status_code"),
        [(OpenAIAuthenticationError, 401), (PermissionDeniedError, 403)],
    )
    async def test_rejected_credentials_are_named_as_the_upstream_side(
        self,
        test_settings: FixtureSettings,
        error_class: type[APIStatusError],
        status_code: int,
    ) -> None:
        provider_error = _status_error(error_class, status_code)
        instance = _build_instance(test_settings, side_effect=provider_error)

        with pytest.raises(UpstreamAuthenticationError) as raised:
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

        assert raised.value.__cause__ is provider_error
        # **LOGIC_STEP**: A subclass of ExternalServiceError, so it answers 502 and carries the
        # generic upstream message. The caller's own credentials were never in question, and
        # answering 401 would send them to re-authenticate against a key they cannot reach.
        assert isinstance(raised.value, ExternalServiceError)
        assert instance._bound_llm.ainvoke.await_count == 1

    # FUNCTION: test_other_provider_failures_become_the_upstream_error
    # SUMMARY: Everything else the provider can fail with reaches the caller as 502's domain type.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("error_class", "status_code"),
        [(RateLimitError, 429), (InternalServerError, 503), (APIStatusError, 418)],
    )
    async def test_other_provider_failures_become_the_upstream_error(
        self,
        test_settings: FixtureSettings,
        error_class: type[APIStatusError],
        status_code: int,
    ) -> None:
        provider_error = _status_error(error_class, status_code)
        instance = _build_instance(test_settings, side_effect=provider_error)

        with pytest.raises(ExternalServiceError) as raised:
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

        assert not isinstance(raised.value, UpstreamAuthenticationError)
        assert raised.value.__cause__ is provider_error

    # FUNCTION: test_a_dropped_connection_becomes_the_upstream_error
    # SUMMARY: The non-status provider failures translate too, once their retries are spent.
    @pytest.mark.unit
    async def test_a_dropped_connection_becomes_the_upstream_error(
        self, test_settings: FixtureSettings
    ) -> None:
        provider_error = APIConnectionError(
            request=httpx.Request("POST", "https://provider.invalid/v1")
        )
        instance = _build_instance(test_settings, side_effect=provider_error)

        with pytest.raises(ExternalServiceError) as raised:
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

        assert raised.value.__cause__ is provider_error

    # FUNCTION: test_a_request_the_provider_refuses_is_left_untranslated
    # SUMMARY: A malformed request stays the provider's own error, and so reports as a 500.
    # NOTE: The one deliberate hole in the translation. A rejected request is this service's
    # defect — a tool schema the provider will not take, a prompt past the context window — and
    # the identical retry fails identically forever. Calling it 502 would say "the provider is
    # unwell" and send whoever is on call to a status page that is green.
    @pytest.mark.unit
    async def test_a_request_the_provider_refuses_is_left_untranslated(
        self, test_settings: FixtureSettings
    ) -> None:
        provider_error = _status_error(BadRequestError, 400)
        instance = _build_instance(test_settings, side_effect=provider_error)

        with pytest.raises(BadRequestError) as raised:
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

        assert raised.value is provider_error

    # FUNCTION: test_the_permanent_failure_is_still_logged_before_it_is_translated
    # SUMMARY: Translation happens outside the retry loop, so the existing log events survive it.
    @pytest.mark.unit
    async def test_the_permanent_failure_is_still_logged_before_it_is_translated(
        self, test_settings: FixtureSettings
    ) -> None:
        instance = _build_instance(
            test_settings, side_effect=_status_error(OpenAIAuthenticationError, 401)
        )

        with pytest.raises(UpstreamAuthenticationError):
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

        # **LOGIC_STEP**: The translation could have been written inside the loop's own except
        # clauses, and that would have been wrong twice over: tenacity re-reads the raised type to
        # decide whether to retry, so a domain error there would end retrying after one attempt —
        # and the log event that names the provider's own message would have been skipped.
        assert instance._logger.log_llm_call.call_args.kwargs["success"] is False
        assert instance._logger.log_error.call_args.kwargs["error_type"] == "llm_call_failed"


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
