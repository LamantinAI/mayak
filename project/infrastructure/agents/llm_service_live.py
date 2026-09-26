# FILE: project/infrastructure/agents/llm_service_live.py
# SUMMARY: Live-provider mixin for LLMService covering client initialization and retryable provider calls.

import email.utils
import json
import time
from contextlib import suppress
from typing import Any, Protocol, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from openai import AuthenticationError as OpenAIAuthenticationError
from openai import ConflictError as OpenAIConflictError
from openai import NotFoundError as OpenAINotFoundError
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from project.core.config import Settings
from project.core.logging import SemanticLogger
from project.core.logging.redaction import redact_secrets
from project.domain.exceptions import ExternalServiceError, UpstreamAuthenticationError


# So the hot-path cost stays zero when full trace is disabled.
# Returns extra kwargs ready to merge into log_llm_call **extra.
def _build_full_trace_extras(
    full_trace: bool,
    messages: list[BaseMessage],
    completion_text: str | None,
) -> dict[str, Any]:
    if not full_trace:
        return {}

    system_prompt = "\n\n".join(str(m.content) for m in messages if isinstance(m, SystemMessage))
    user_message = "\n\n".join(str(m.content) for m in messages if not isinstance(m, SystemMessage))
    # Scrub credential shapes in full-trace prompts; personal data still needs operator care.
    extras: dict[str, Any] = {
        "system_prompt": redact_secrets(system_prompt),
        "user_message": redact_secrets(user_message),
    }
    if completion_text is not None:
        extras["completion_text"] = redact_secrets(completion_text)
    return extras


# Mirrors openai._base_client._parse_retry_after_header (ms/seconds/HTTP-date), capped at 120s.
def _retry_after_seconds(headers: Any) -> float | None:
    for raw, scale in ((headers.get("retry-after-ms"), 0.001), (headers.get("retry-after"), 1.0)):
        with suppress(TypeError, ValueError):
            return float(raw) * scale
    with suppress(TypeError, ValueError, OverflowError, OSError):
        parsed = email.utils.parsedate_tz(headers.get("retry-after"))
        return None if parsed is None else email.utils.mktime_tz(parsed) - time.time()
    return None


def _wait_after_retry_header(retry_state: RetryCallState) -> float:
    exception = retry_state.outcome.exception() if retry_state.outcome else None
    headers = getattr(getattr(exception, "response", None), "headers", None)
    wait_seconds = _retry_after_seconds(headers) if headers is not None else None
    if wait_seconds is not None and 0 < wait_seconds <= 120:
        return wait_seconds
    return wait_exponential(multiplier=1, min=2, max=10)(retry_state)


class _LLMServiceLiveContract(Protocol):
    # Validated application settings used to configure provider clients and retries.
    _settings: Settings

    _logger: SemanticLogger

    # Underlying live provider client or None when mock mode is active.
    _llm: BaseChatModel | None

    # Provider runnable optionally enhanced with bound tools.
    _bound_llm: Any


class LLMServiceLiveMixin:
    # Underlying live provider client or None when mock mode is active.
    _llm: BaseChatModel | None

    # Provider runnable optionally enhanced with bound tools.
    _bound_llm: Any

    # A second provider belongs to a vertical; the kernel has one shared client.
    def _initialize_llm(self: _LLMServiceLiveContract) -> None:
        effective_mode = self._settings.agent.llm_mode
        effective_model = self._settings.llm.model
        effective_temperature = self._settings.agent.default_llm_temperature
        effective_max_tokens = self._settings.agent.max_tokens
        effective_api_key = self._settings.llm.api_key
        effective_base_url: str | None = (
            str(self._settings.llm.base_url) if self._settings.llm.base_url else None
        )

        # Skip provider client construction in deterministic mock mode.
        if effective_mode == "mock":
            self._llm = None
            self._bound_llm = None
            self._logger.log_system_event(
                event_name="llm_service_initialized",
                category="initialization",
                new_value={
                    "model": effective_model,
                    "mode": "mock",
                    "temperature": effective_temperature,
                },
            )
            return

        # Create ChatOpenAI instance with resolved settings.
        llm_kwargs: dict[str, Any] = {
            "model": effective_model,
            "api_key": effective_api_key,
            "base_url": effective_base_url,
            "temperature": effective_temperature,
            "max_tokens": effective_max_tokens,
            "tiktoken_model_name": self._settings.llm.tiktoken_model_name,
            "timeout": self._settings.llm.request_timeout,
            # Tenacity owns the attempt budget; SDK retries would multiply it (see below).
            "max_retries": 0,
        }
        llm = ChatOpenAI(**llm_kwargs)
        self._llm = llm
        self._bound_llm = llm

        # Log initialization event for the live provider client.
        self._logger.log_system_event(
            event_name="llm_service_initialized",
            category="initialization",
            new_value={
                "model": effective_model,
                "mode": "live",
                "temperature": effective_temperature,
            },
        )

    # Provider auth failures and other upstream failures have distinct domain errors.
    async def _call_llm_with_retry(
        self: _LLMServiceLiveContract,
        messages: list[BaseMessage],
    ) -> BaseMessage:
        # Check if the live provider is initialized.
        runnable = self._bound_llm
        if runnable is None:
            raise ExternalServiceError("LLM provider is not initialized")

        effective_retries = self._settings.agent.max_llm_call_retries

        # Tenacity is the only retry loop; nested retries multiply the attempt budget.
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(effective_retries),
                wait=_wait_after_retry_header,
                retry=retry_if_exception_type(
                    (
                        RateLimitError,
                        APITimeoutError,
                        APIConnectionError,
                        InternalServerError,
                    )
                ),
                reraise=True,
            ):
                with attempt:
                    start_ns = time.perf_counter_ns()
                    model = self._settings.llm.model

                    try:
                        try:
                            response = cast(BaseMessage, await runnable.ainvoke(messages))
                        except (
                            IndexError,
                            KeyError,
                            TypeError,
                            AttributeError,
                            json.JSONDecodeError,
                        ) as error:
                            # What langchain-openai raises for a 200 with empty, missing or null
                            # choices/message, or a non-JSON body. Not retried: the same body
                            # comes back. Not bare ValueError: pydantic's ValidationError must
                            # still surface.
                            raise ExternalServiceError(
                                "LLM provider returned an invalid reply"
                            ) from error
                        duration_ms = (time.perf_counter_ns() - start_ns) / 1e6
                        input_tokens = None
                        output_tokens = None
                        total_tokens = None
                        usage = getattr(response, "usage_metadata", None)
                        if usage:
                            if isinstance(usage, dict):
                                input_tokens = usage.get("input_tokens")
                                output_tokens = usage.get("output_tokens")
                                total_tokens = usage.get("total_tokens")
                            else:
                                input_tokens = getattr(usage, "input_tokens", None)
                                output_tokens = getattr(usage, "output_tokens", None)
                                total_tokens = getattr(usage, "total_tokens", None)

                        # Structural-only meta (no content): prompt/response sizes.
                        system_prompt_chars = sum(
                            len(str(m.content)) for m in messages if isinstance(m, SystemMessage)
                        )
                        prompt_chars_total = sum(
                            len(str(m.content))
                            for m in messages
                            if not isinstance(m, SystemMessage)
                        )
                        response_content = getattr(response, "content", "")
                        response_chars = len(str(response_content))

                        # Preserve finish_reason: a truncated answer otherwise looks successful.
                        response_metadata = getattr(response, "response_metadata", None)
                        finish_reason = (
                            response_metadata.get("finish_reason")
                            if isinstance(response_metadata, dict)
                            else None
                        )

                        full_trace = self._settings.observability.full_trace_enabled
                        extras = _build_full_trace_extras(
                            full_trace, messages, str(response_content)
                        )
                        self._logger.log_llm_call(
                            model,
                            duration_ms=duration_ms,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            total_tokens=total_tokens,
                            success=True,
                            prompt_chars_total=prompt_chars_total,
                            system_prompt_chars=system_prompt_chars,
                            messages_count=len(messages),
                            response_chars=response_chars,
                            finish_reason=finish_reason,
                            **extras,
                        )
                        return response
                    except (
                        RateLimitError,
                        APITimeoutError,
                        APIConnectionError,
                        InternalServerError,
                    ) as error:
                        duration_ms = (time.perf_counter_ns() - start_ns) / 1e6
                        system_prompt_chars = sum(
                            len(str(m.content)) for m in messages if isinstance(m, SystemMessage)
                        )
                        prompt_chars_total = sum(
                            len(str(m.content))
                            for m in messages
                            if not isinstance(m, SystemMessage)
                        )
                        full_trace = self._settings.observability.full_trace_enabled
                        extras = _build_full_trace_extras(full_trace, messages, None)
                        self._logger.log_llm_call(
                            model,
                            duration_ms=duration_ms,
                            success=False,
                            error=str(error),
                            prompt_chars_total=prompt_chars_total,
                            system_prompt_chars=system_prompt_chars,
                            messages_count=len(messages),
                            **extras,
                        )
                        self._logger.log_error(
                            error_type="llm_call_retryable_error",
                            message="LLM call failed, retrying",
                            exception=error,
                        )
                        raise
                    except OpenAIError as error:
                        duration_ms = (time.perf_counter_ns() - start_ns) / 1e6
                        system_prompt_chars = sum(
                            len(str(m.content)) for m in messages if isinstance(m, SystemMessage)
                        )
                        prompt_chars_total = sum(
                            len(str(m.content))
                            for m in messages
                            if not isinstance(m, SystemMessage)
                        )
                        full_trace = self._settings.observability.full_trace_enabled
                        extras = _build_full_trace_extras(full_trace, messages, None)
                        self._logger.log_llm_call(
                            model,
                            duration_ms=duration_ms,
                            success=False,
                            error=str(error),
                            prompt_chars_total=prompt_chars_total,
                            system_prompt_chars=system_prompt_chars,
                            messages_count=len(messages),
                            **extras,
                        )
                        self._logger.log_error(
                            error_type="llm_call_failed",
                            message="LLM call failed permanently",
                            exception=error,
                        )
                        raise
        except (OpenAIAuthenticationError, PermissionDeniedError) as error:
            # Provider credentials are owned by this service, not the HTTP caller.
            raise UpstreamAuthenticationError(
                "LLM provider rejected this service's credentials"
            ) from error
        except (
            BadRequestError,
            OpenAINotFoundError,
            OpenAIConflictError,
            UnprocessableEntityError,
        ):
            # Bad requests from our own code must surface rather than masquerade as outages.
            raise
        except OpenAIError as error:
            # Other provider failures become domain errors at this boundary.
            raise ExternalServiceError("LLM provider call failed") from error

        raise ExternalServiceError("LLM retry loop exited unexpectedly")
