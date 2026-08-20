# FILE: tests/application/test_middleware.py
# SUMMARY: Tests for the logging middleware — query-parameter masking and inbound request-id resolution.

from __future__ import annotations

import uuid
from typing import Any, Iterator
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from project.infrastructure.api.middleware import (
    _SENSITIVE_PARAM_PATTERN,
    _extract_or_generate_request_id,
    _sanitize_params,
)

# ATTRIBUTE: MASK (str)
# SUMMARY: What a masked value is replaced with.
MASK = "***"

# ATTRIBUTE: SENSITIVE_WORDS (tuple[str, ...])
# SUMMARY: The words the middleware itself treats as sensitive, read off its own pattern.
# **LOGIC_STEP**: Read from the pattern rather than restated here. A hand-copied list is a second
# source of truth that goes stale the first time someone adds a word to the regex and no test
# notices; deriving it means a new word arrives already covered.
SENSITIVE_WORDS = tuple(_SENSITIVE_PARAM_PATTERN.pattern.strip("()").split("|"))

# ATTRIBUTE: TRACEPARENT (str)
# SUMMARY: A spec-shaped W3C traceparent whose middle segment is the trace id.
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"

# ATTRIBUTE: TRACEPARENT_TRACE_ID (str)
# SUMMARY: The 32-hex segment the middleware must lift out of TRACEPARENT.
TRACEPARENT_TRACE_ID = "0af7651916cd43dd8448eb211c80319c"


# FUNCTION: _request_carrying
# SUMMARY: Build the smallest object `_extract_or_generate_request_id` accepts — one that answers
#          case-insensitive `.headers.get`.
# INPUT: headers (dict[str, str]): Inbound header names and values.
# OUTPUT: (Any): Stand-in for fastapi.Request exposing only the attribute under test.
def _request_carrying(headers: dict[str, str]) -> Any:
    folded = {name.lower(): value for name, value in headers.items()}
    return type(
        "_StubRequest",
        (),
        {"headers": type("_StubHeaders", (), {"get": staticmethod(folded.get)})()},
    )()


# CLASS: tests.application.test_middleware.TestQueryParameterMasking
# SUMMARY: Verify every word the middleware calls sensitive is masked, and nothing else is touched.
class TestQueryParameterMasking:
    # FUNCTION: test_every_declared_sensitive_word_is_masked
    # SUMMARY: Verify each word in the module's own pattern masks a parameter named after it.
    @pytest.mark.unit
    @pytest.mark.parametrize("word", SENSITIVE_WORDS)
    def test_every_declared_sensitive_word_is_masked(self, word: str) -> None:
        masked = _sanitize_params({word: "value-that-must-not-survive"})

        assert masked == {word: MASK}

    # FUNCTION: test_the_word_is_matched_anywhere_in_the_name
    # SUMMARY: Verify a compound name containing a sensitive word is masked, not only an exact match.
    @pytest.mark.unit
    @pytest.mark.parametrize("name", ["api_key", "access_token", "auth_code", "x-secret-header"])
    def test_the_word_is_matched_anywhere_in_the_name(self, name: str) -> None:
        assert _sanitize_params({name: "value"})[name] == MASK

    # FUNCTION: test_matching_ignores_case
    # SUMMARY: Verify an upper- or mixed-case name is masked exactly like its lowercase form.
    @pytest.mark.unit
    @pytest.mark.parametrize("name", ["API_TOKEN", "Secret_Key", "PassWord"])
    def test_matching_ignores_case(self, name: str) -> None:
        assert _sanitize_params({name: "value"})[name] == MASK

    # FUNCTION: test_ordinary_parameters_pass_through_untouched
    # SUMMARY: Verify a mapping with no sensitive name is returned equal to its input.
    @pytest.mark.unit
    def test_ordinary_parameters_pass_through_untouched(self) -> None:
        ordinary = {"page": "1", "limit": "10", "query": "hello", "sort": "asc"}

        assert _sanitize_params(ordinary) == ordinary

    # FUNCTION: test_masking_is_per_key_not_all_or_nothing
    # SUMMARY: Verify one sensitive name in a mapping does not mask its neighbours.
    @pytest.mark.unit
    def test_masking_is_per_key_not_all_or_nothing(self) -> None:
        assert _sanitize_params({"api_key": "s3cr3t", "page": "1"}) == {
            "api_key": MASK,
            "page": "1",
        }

    # FUNCTION: test_empty_mapping_stays_empty
    # SUMMARY: Verify the degenerate input returns an empty mapping rather than raising.
    @pytest.mark.unit
    def test_empty_mapping_stays_empty(self) -> None:
        assert _sanitize_params({}) == {}


# CLASS: tests.application.test_middleware.TestInboundRequestIdResolution
# SUMMARY: Verify which inbound header wins, and that an unusable one is discarded rather than echoed.
class TestInboundRequestIdResolution:
    # FUNCTION: test_a_well_formed_inbound_id_is_kept
    # SUMMARY: Verify a caller's own id survives so a trace spans both services.
    @pytest.mark.unit
    def test_a_well_formed_inbound_id_is_kept(self) -> None:
        caller_id = "abcdef12-3456-7890-abcd-ef1234567890"

        assert _extract_or_generate_request_id(_request_carrying({"X-Request-ID": caller_id})) == (
            caller_id
        )

    # FUNCTION: test_an_unusable_inbound_id_is_replaced_not_echoed
    # SUMMARY: Verify a hostile or malformed header never reaches the log or the response.
    # **LOGIC_STEP**: The header is attacker-controlled. Asserting only "a fresh id came back"
    # would pass even if the payload were concatenated into it, so the assertion is that the
    # supplied text appears nowhere in the result.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "hostile",
        ["<script>alert(1)</script>", "short", "with spaces inside", "a" * 65],
    )
    def test_an_unusable_inbound_id_is_replaced_not_echoed(self, hostile: str) -> None:
        resolved = _extract_or_generate_request_id(_request_carrying({"X-Request-ID": hostile}))

        assert hostile not in resolved
        assert uuid.UUID(resolved).version == 4

    # FUNCTION: test_an_empty_header_is_treated_as_absent
    # SUMMARY: Verify a present-but-blank header falls through instead of being validated.
    # **LOGIC_STEP**: Kept out of the parametrised case above: `"" in anything` is always true, so
    # the "not echoed" assertion cannot fail for it and would have been a passing no-op.
    @pytest.mark.unit
    def test_an_empty_header_is_treated_as_absent(self) -> None:
        assert (
            uuid.UUID(
                _extract_or_generate_request_id(_request_carrying({"X-Request-ID": ""}))
            ).version
            == 4
        )

    # FUNCTION: test_traceparent_supplies_the_id_when_no_request_id_header
    # SUMMARY: Verify the W3C trace id is lifted out of the middle segment of the header.
    @pytest.mark.unit
    def test_traceparent_supplies_the_id_when_no_request_id_header(self) -> None:
        resolved = _extract_or_generate_request_id(_request_carrying({"traceparent": TRACEPARENT}))

        assert resolved == TRACEPARENT_TRACE_ID

    # FUNCTION: test_a_malformed_traceparent_falls_through
    # SUMMARY: Verify a header that does not match the spec yields a fresh id, not a partial parse.
    @pytest.mark.unit
    def test_a_malformed_traceparent_falls_through(self) -> None:
        resolved = _extract_or_generate_request_id(_request_carrying({"traceparent": "00-zz-01"}))

        assert uuid.UUID(resolved).version == 4

    # FUNCTION: test_an_explicit_request_id_outranks_traceparent
    # SUMMARY: Verify the caller's deliberate id wins over the ambient trace context.
    @pytest.mark.unit
    def test_an_explicit_request_id_outranks_traceparent(self) -> None:
        caller_id = "abcdef12-3456-7890-abcd-ef1234567890"

        resolved = _extract_or_generate_request_id(
            _request_carrying({"X-Request-ID": caller_id, "traceparent": TRACEPARENT})
        )

        assert resolved == caller_id

    # FUNCTION: test_no_headers_at_all_yields_a_fresh_uuid
    # SUMMARY: Verify the fallback produces something parseable as a UUID.
    @pytest.mark.unit
    def test_no_headers_at_all_yields_a_fresh_uuid(self) -> None:
        assert uuid.UUID(_extract_or_generate_request_id(_request_carrying({}))).version == 4


# CLASS: tests.application.test_middleware.TestRequestIdReachesTheResponse
# SUMMARY: Verify the resolved id is published on every response, whatever the outcome.
class TestRequestIdReachesTheResponse:
    # FUNCTION: _never_sample_out_health
    # SUMMARY: Pin health-check sampling on so the middleware runs its full path in these tests.
    @pytest.fixture(autouse=True)
    def _never_sample_out_health(self) -> Iterator[None]:
        with patch(
            "project.infrastructure.api.middleware.should_sample_health_check",
            return_value=True,
        ):
            yield

    # FUNCTION: test_a_successful_response_carries_a_parseable_id
    # SUMMARY: Verify the published header is a real UUID rather than an arbitrary string.
    @pytest.mark.unit
    async def test_a_successful_response_carries_a_parseable_id(
        self, async_client: AsyncClient
    ) -> None:
        response = await async_client.get("/health/")

        assert uuid.UUID(response.headers["X-Request-ID"]).version == 4

    # FUNCTION: test_a_failing_response_carries_one_too
    # SUMMARY: Verify a 404 is still traceable — the path most worth correlating is the broken one.
    @pytest.mark.unit
    async def test_a_failing_response_carries_one_too(self, async_client: AsyncClient) -> None:
        response = await async_client.get("/nonexistent")

        assert response.status_code == 404
        assert uuid.UUID(response.headers["X-Request-ID"]).version == 4

    # FUNCTION: test_two_requests_are_told_apart
    # SUMMARY: Verify the id is minted per request, not once per process.
    @pytest.mark.unit
    async def test_two_requests_are_told_apart(self, async_client: AsyncClient) -> None:
        first = await async_client.get("/health/")
        second = await async_client.get("/health/")

        assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
