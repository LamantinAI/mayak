# FILE: tests/application/test_llm_service_retry.py
# SUMMARY: Tests for the retry policy and token accounting of live LLM calls.

import email.utils
import json
import time
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
    ConflictError as OpenAIConflictError,
    InternalServerError,
    NotFoundError as OpenAINotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from tenacity import RetryCallState, wait_none

from project.domain.exceptions import ExternalServiceError, UpstreamAuthenticationError
from project.infrastructure.agents import llm_service_live as live_module
from project.infrastructure.agents.llm_service_live import LLMServiceLiveMixin
from tests.conftest import _FixtureSettings as FixtureSettings


# Build the provider timeout the retry policy is supposed to treat as retryable.
def _timeout_error() -> APITimeoutError:
    return APITimeoutError(request=httpx.Request("POST", "https://provider.invalid/v1"))


# Build a provider rate-limit error, the second retryable shape; optionally with the
# Retry-After headers a wait-strategy test wants to check.
def _rate_limit_error(headers: dict[str, str] | None = None) -> RateLimitError:
    request = httpx.Request("POST", "https://provider.invalid/v1")
    response = httpx.Response(status_code=429, request=request, headers=headers)
    return RateLimitError("slow down", response=response, body=None)


# Build any openai status error, so one helper covers the whole translation matrix.
def _status_error(error_class: type[APIStatusError], status_code: int) -> APIStatusError:
    request = httpx.Request("POST", "https://provider.invalid/v1")
    response = httpx.Response(status_code=status_code, request=request)
    return error_class("provider said no", response=response, body=None)


# A RetryCallState whose last attempt failed with `error` — enough for a wait strategy to read.
def _retry_state_for(error: BaseException) -> RetryCallState:
    state = RetryCallState(retry_object=MagicMock(), fn=None, args=(), kwargs={})
    state.set_exception((type(error), error, None))
    return state


# Live mixin plus the two contract attributes LLMService.__init__ normally supplies.
class _RetryHarness(LLMServiceLiveMixin):
    # Any: both are MagicMock stubs here, not the real Settings/SemanticLogger.
    _settings: Any

    _logger: Any


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


# Strip the exponential wait so the retry tests measure behaviour, not wall-clock sleep.
@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    # wait_exponential is resolved from the module namespace at call time, so
    # replacing the name is enough — the real policy keeps its min=2s backoff in production.
    monkeypatch.setattr(live_module, "wait_exponential", lambda **_: wait_none())


# What the retry loop does with retryable failures, permanent failures, and success.
class TestRetryPolicy:
    # max_retries=0 is what stops the SDK's own retries from multiplying Tenacity's;
    # max_tokens and timeout were never checked reaching the client either.
    @pytest.mark.unit
    def test_llm_client_kwargs_reach_chat_openai(
        self, test_settings: FixtureSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import patch

        # test_settings is session-scoped: a plain assignment would leak "live" mode onward.
        monkeypatch.setattr(test_settings.agent, "llm_mode", "live")
        instance = _RetryHarness.__new__(_RetryHarness)
        instance._settings = test_settings
        instance._logger = MagicMock()
        with patch.object(live_module, "ChatOpenAI") as client:
            instance._initialize_llm()
        kwargs = client.call_args.kwargs
        assert kwargs["max_retries"] == 0
        assert kwargs["max_tokens"] == test_settings.agent.max_tokens
        assert kwargs["timeout"] == test_settings.llm.request_timeout

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
        # Each failed attempt logs its own success=False before the retry; only the last is True.
        calls = instance._logger.log_llm_call.call_args_list
        assert [call.kwargs["success"] for call in calls] == [False, False, True]
        assert calls[0].kwargs["error"] == "Request timed out."
        assert calls[1].kwargs["error"] == "slow down"
        assert "error" not in calls[2].kwargs
        retry_errors = [
            call.kwargs["error_type"] for call in instance._logger.log_error.call_args_list
        ]
        assert retry_errors == ["llm_call_retryable_error", "llm_call_retryable_error"]

    @pytest.mark.unit
    async def test_attempts_stop_at_the_configured_limit(
        self, test_settings: FixtureSettings
    ) -> None:
        limit = test_settings.agent.max_llm_call_retries
        provider_error = _timeout_error()
        instance = _build_instance(test_settings, side_effect=provider_error)

        # Exhausted retries leave as a domain error, like every other provider failure.
        with pytest.raises(ExternalServiceError) as raised:
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

        assert instance._bound_llm.ainvoke.await_count == limit
        assert raised.value.__cause__ is provider_error

    # Only the provider's transient shapes are retryable; a bug must surface immediately.
    @pytest.mark.unit
    async def test_unexpected_error_is_not_retried(self, test_settings: FixtureSettings) -> None:
        instance = _build_instance(test_settings, side_effect=ValueError("bad argument"))

        with pytest.raises(ValueError):
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

        assert instance._bound_llm.ainvoke.await_count == 1

    @pytest.mark.unit
    async def test_uninitialised_provider_is_named(self, test_settings: FixtureSettings) -> None:
        instance = _build_instance(test_settings, return_value=AIMessage(content="ok"))
        instance._bound_llm = None

        with pytest.raises(Exception, match="not initialized"):
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

    # A malformed 200 raises one of these; a raw parsing exception otherwise reached the caller.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "error",
        [
            KeyError("choices"),
            IndexError(),
            TypeError(),
            AttributeError(),
            json.JSONDecodeError("Expecting value", "not json", 0),
        ],
    )
    async def test_malformed_provider_reply_maps_to_domain_error(
        self, test_settings: FixtureSettings, error: Exception
    ) -> None:
        instance = _build_instance(test_settings, side_effect=error)

        with pytest.raises(ExternalServiceError):
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

        assert instance._bound_llm.ainvoke.await_count == 1


# Mirrors the SDK's own 120s cap and its three header shapes (ms, seconds, HTTP-date).
# no_backoff (fixture) patches wait_exponential to wait_none(), so a fallback reads as 0.
class TestRetryAfterWait:
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("headers", "expected"),
        [
            ({"retry-after": "7"}, 7.0),
            ({"retry-after-ms": "250"}, 0.25),
            ({}, 0),
            ({"retry-after": "90"}, 90.0),
            ({"retry-after": "150"}, 0),  # over the 120s cap: falls back
            ({"retry-after": "not-a-date"}, 0),  # unparsable: falls back
        ],
    )
    def test_retry_after_header_or_fallback(self, headers: dict[str, str], expected: Any) -> None:
        state = _retry_state_for(_rate_limit_error(headers=headers))

        assert live_module._wait_after_retry_header(state) == expected

    # Built at call time: a value baked into the parametrize table drifts past `abs=2`.
    @pytest.mark.unit
    def test_http_date_retry_after_is_honoured(self) -> None:
        soon = email.utils.formatdate(time.time() + 30, usegmt=True)
        state = _retry_state_for(_rate_limit_error(headers={"retry-after": soon}))

        assert live_module._wait_after_retry_header(state) == pytest.approx(30, abs=2)


# Every shape of provider failure, and the domain error the caller is given instead.
class TestProviderErrorsBecomeDomainErrors:
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
        # A subclass of ExternalServiceError: the caller's own credentials were never in question.
        assert isinstance(raised.value, ExternalServiceError)
        assert instance._bound_llm.ainvoke.await_count == 1

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

    # The non-status provider failures translate too, once their retries are spent.
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

    # The deliberate hole in the translation: the provider understood and refused the
    # request, so the identical retry fails identically forever — reported as 500, not 502.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("error_class", "status_code"),
        [
            (BadRequestError, 400),
            (OpenAINotFoundError, 404),
            (OpenAIConflictError, 409),
            (UnprocessableEntityError, 422),
        ],
    )
    async def test_a_request_the_provider_refuses_is_left_untranslated(
        self,
        test_settings: FixtureSettings,
        error_class: type[APIStatusError],
        status_code: int,
    ) -> None:
        provider_error = _status_error(error_class, status_code)
        instance = _build_instance(test_settings, side_effect=provider_error)

        with pytest.raises(error_class) as raised:
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

        assert raised.value is provider_error
        assert not isinstance(raised.value, ExternalServiceError)

    @pytest.mark.unit
    async def test_the_permanent_failure_is_still_logged_before_it_is_translated(
        self, test_settings: FixtureSettings
    ) -> None:
        instance = _build_instance(
            test_settings, side_effect=_status_error(OpenAIAuthenticationError, 401)
        )

        with pytest.raises(UpstreamAuthenticationError):
            await instance._call_llm_with_retry([HumanMessage(content="hi")])

        # Translating inside the loop's own except clauses would end retrying after one
        # attempt (tenacity re-reads the raised type) and skip the provider's own log message.
        assert instance._logger.log_llm_call.call_args.kwargs["success"] is False
        assert instance._logger.log_error.call_args.kwargs["error_type"] == "llm_call_failed"


class TestTokenAccounting:
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

    @pytest.mark.unit
    async def test_missing_usage_metadata_logs_none_not_zero(
        self, test_settings: FixtureSettings
    ) -> None:
        instance = _build_instance(test_settings, return_value=AIMessage(content="ok"))

        await instance._call_llm_with_retry([HumanMessage(content="hi")])

        kwargs = instance._logger.log_llm_call.call_args.kwargs
        assert kwargs["input_tokens"] is None
        assert kwargs["total_tokens"] is None


# finish_reason must survive from the raw provider reply to the log_llm_call call site — a
# truncated reply (finish_reason="length") otherwise looks identical to a complete one.
class TestFinishReasonReachesTheLog:
    @pytest.mark.unit
    async def test_finish_reason_is_read_from_response_metadata(
        self, test_settings: FixtureSettings
    ) -> None:
        reply = AIMessage(content="ok", response_metadata={"finish_reason": "length"})
        instance = _build_instance(test_settings, return_value=reply)

        await instance._call_llm_with_retry([HumanMessage(content="hi")])

        kwargs = instance._logger.log_llm_call.call_args.kwargs
        assert kwargs["finish_reason"] == "length"

    # The field is not special-cased to only the truncated value — every reply carries it.
    @pytest.mark.unit
    async def test_a_normal_stop_reason_reaches_the_log_too(
        self, test_settings: FixtureSettings
    ) -> None:
        reply = AIMessage(content="ok", response_metadata={"finish_reason": "stop"})
        instance = _build_instance(test_settings, return_value=reply)

        await instance._call_llm_with_retry([HumanMessage(content="hi")])

        kwargs = instance._logger.log_llm_call.call_args.kwargs
        assert kwargs["finish_reason"] == "stop"

    # A mock runnable or a future provider that omits response_metadata must not crash.
    @pytest.mark.unit
    async def test_a_reply_with_no_response_metadata_logs_finish_reason_none(
        self, test_settings: FixtureSettings
    ) -> None:
        instance = _build_instance(test_settings, return_value=AIMessage(content="ok"))

        await instance._call_llm_with_retry([HumanMessage(content="hi")])

        kwargs = instance._logger.log_llm_call.call_args.kwargs
        assert kwargs["finish_reason"] is None
