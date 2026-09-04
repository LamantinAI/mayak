# FILE: project/infrastructure/agents/llm_service_live.py
# SUMMARY: Live-provider mixin for LLMService covering client initialization and retryable provider calls.

import time
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
)
from openai import AuthenticationError as OpenAIAuthenticationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from project.core.config import Settings
from project.core.logging import SemanticLogger
from project.domain.exceptions import ExternalServiceError, UpstreamAuthenticationError


# FUNCTION: _build_full_trace_extras
# SUMMARY: Build body-inclusive extras for llm.call events when full-trace observability is on.
#          Returns empty dict when disabled so the hot-path cost stays zero.
# INPUT: full_trace (bool): Whether full-trace is enabled at call time.
# INPUT: completion_text (str | None): Raw completion text when available.
# OUTPUT: (dict[str, Any]): Extra kwargs ready to merge into log_llm_call **extra.
def _build_full_trace_extras(
    full_trace: bool,
    messages: list[BaseMessage],
    completion_text: str | None,
) -> dict[str, Any]:
    if not full_trace:
        return {}

    system_prompt = "\n\n".join(str(m.content) for m in messages if isinstance(m, SystemMessage))
    user_message = "\n\n".join(str(m.content) for m in messages if not isinstance(m, SystemMessage))
    extras: dict[str, Any] = {
        "system_prompt": system_prompt,
        "user_message": user_message,
    }
    if completion_text is not None:
        extras["completion_text"] = completion_text
    return extras


# CLASS: project.infrastructure.agents.llm_service_live._LLMServiceLiveContract
# SUMMARY: Structural contract describing the shared LLMService state used by the live-provider mixin.
class _LLMServiceLiveContract(Protocol):
    # ATTRIBUTE: _settings (Settings)
    # SUMMARY: Validated application settings used to configure provider clients and retries.
    _settings: Settings

    # ATTRIBUTE: _logger (SemanticLogger)
    # SUMMARY: Semantic logger used for lifecycle, call, and error events.
    _logger: SemanticLogger

    # ATTRIBUTE: _llm (BaseChatModel | None)
    # SUMMARY: Underlying live provider client or None when mock mode is active.
    _llm: BaseChatModel | None

    # ATTRIBUTE: _bound_llm (Any)
    # SUMMARY: Provider runnable optionally enhanced with bound tools.
    _bound_llm: Any


# CLASS: project.infrastructure.agents.llm_service_live.LLMServiceLiveMixin
# SUMMARY: Mixin implementing live-provider setup and retryable LLM calls.
class LLMServiceLiveMixin:
    # ATTRIBUTE: _llm (BaseChatModel | None)
    # SUMMARY: Underlying live provider client or None when mock mode is active.
    _llm: BaseChatModel | None

    # ATTRIBUTE: _bound_llm (Any)
    # SUMMARY: Provider runnable optionally enhanced with bound tools.
    _bound_llm: Any

    # FUNCTION: _initialize_llm
    # SUMMARY: Initialize the live provider client, or leave mock placeholders, from global settings.
    # NOTE: There used to be a three-tier resolution here — explicit overrides beat a per-agent
    # AgentConfig, which beat global settings — and nothing in the template ever constructed either
    # of the two upper tiers. A vertical that needs a second LLM endpoint builds its own service
    # rather than inheriting a mechanism the kernel cannot demonstrate.
    def _initialize_llm(self: _LLMServiceLiveContract) -> None:
        effective_mode = self._settings.agent.llm_mode
        effective_model = self._settings.llm.model
        effective_temperature = self._settings.agent.default_llm_temperature
        effective_max_tokens = self._settings.agent.max_tokens
        effective_api_key = self._settings.llm.api_key
        effective_base_url: str | None = (
            str(self._settings.llm.base_url) if self._settings.llm.base_url else None
        )

        # **LOGIC_STEP**: Skip provider client construction in deterministic mock mode.
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

        # **LOGIC_STEP**: Create ChatOpenAI instance with resolved settings.
        llm_kwargs: dict[str, Any] = {
            "model": effective_model,
            "api_key": effective_api_key,
            "base_url": effective_base_url,
            "temperature": effective_temperature,
            "max_tokens": effective_max_tokens,
            "tiktoken_model_name": self._settings.llm.tiktoken_model_name,
            "timeout": self._settings.llm.request_timeout,
        }
        llm = ChatOpenAI(**llm_kwargs)
        self._llm = llm
        self._bound_llm = llm

        # **LOGIC_STEP**: Log initialization event for the live provider client.
        self._logger.log_system_event(
            event_name="llm_service_initialized",
            category="initialization",
            new_value={
                "model": effective_model,
                "mode": "live",
                "temperature": effective_temperature,
            },
        )

    # FUNCTION: _call_llm_with_retry
    # SUMMARY: Call the LLM with automatic retry logic using configured max retries.
    # RAISES: UpstreamAuthenticationError: If the provider rejects this service's credentials.
    # RAISES: ExternalServiceError: If the provider is uninitialised, or fails for any other
    #         reason — including a retryable failure that used up every attempt.
    # RAISES: BadRequestError: Untranslated on purpose; see the handler at the end of this method.
    async def _call_llm_with_retry(
        self: _LLMServiceLiveContract,
        messages: list[BaseMessage],
    ) -> BaseMessage:
        # **LOGIC_STEP**: Check if the live provider is initialized.
        runnable = self._bound_llm
        if runnable is None:
            raise ExternalServiceError("LLM provider is not initialized")

        effective_retries = self._settings.agent.max_llm_call_retries

        # **LOGIC_STEP**: Invoke the provider with configurable retry logic.
        # NOTE: This AsyncRetrying is the **single source of retry truth** for LLM calls.
        # Callers must not wrap it in their own @retry: the two layers multiply rather than
        # combine, so three attempts around three attempts is a nine-attempt worst case that
        # outlives whatever asyncio.wait_for budget the caller set. Add attempts here, never
        # around this.
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(effective_retries),
                wait=wait_exponential(multiplier=1, min=2, max=10),
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
                        response = cast(BaseMessage, await runnable.ainvoke(messages))
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
            # **LOGIC_STEP**: Whose credentials failed decides who can fix it. The provider
            # rejecting OUR key is an operator's configuration problem — a dead key, a revoked
            # project — and it is answered 502 like any other upstream failure, because the
            # caller's own credentials are fine and a 401 would send them to re-authenticate
            # against something they cannot reach. The distinct type is what a log query, an
            # alert or a test can key on.
            raise UpstreamAuthenticationError(
                "LLM provider rejected this service's credentials"
            ) from error
        except BadRequestError:
            # **LOGIC_STEP**: Deliberately not translated. A rejected request — a malformed tool
            # schema, a prompt past the context window — is this service's own defect, and the
            # identical retry fails identically forever. Reported as 500, because 502 would say
            # "the provider is unwell" and send whoever is on call to a status page that is green.
            raise
        except OpenAIError as error:
            # **LOGIC_STEP**: Everything else the provider can fail with, including the retryable
            # kinds that exhausted their attempts above. The cause survives on __cause__ for the
            # log; the client is told only that an upstream service failed.
            raise ExternalServiceError("LLM provider call failed") from error

        raise ExternalServiceError("LLM retry loop exited unexpectedly")
