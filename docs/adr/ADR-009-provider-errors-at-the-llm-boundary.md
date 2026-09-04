# ADR-009: A Provider Error Stops at the Adapter

## Status

Accepted (2026-09-04)

## Decision

`LLMService` raises the domain's own error types, never the provider client's. The adapter in
`project/infrastructure/agents/llm_service_live.py` translates on the way out:

| What happened | What leaves the adapter | What the caller is answered |
| --- | --- | --- |
| The provider rejected our API key (`AuthenticationError`, `PermissionDeniedError`) | `UpstreamAuthenticationError` | 502 |
| Anything else the provider failed with, including a retryable failure that used up its attempts | `ExternalServiceError` | 502 |
| The provider refused the request itself (`BadRequestError`) | the provider's own error, untranslated | 500 |

`UpstreamAuthenticationError` is a subclass of `ExternalServiceError`, declared in
`project/domain/exceptions.py`. Every translation uses `raise ... from error`, so the provider's
exception stays reachable on `__cause__` for the log.

## Rationale

Before this, every one of those cases left the adapter as the provider client's own exception. None
of them is a `ProjectError`, so the generic handler answered all of them with one 500 and the fixed
message "An internal server error occurred". A dead API key, a rate limit that outlived its
retries, and a genuine bug in this service were the same line in the log and the same body on the
wire. Nothing in the kernel translated them and no test asked: the one test covering a
non-retryable failure raised `ValueError`, which is not a provider error at all.

Three details of the mapping are decisions rather than mechanics.

**A provider rejecting our key is 502, not 401.** It is tempting to reuse the domain's
`AuthenticationError`, which the handler answers with 401 — the status the provider itself used.
But 401 is about the credentials of whoever is calling *us*, and theirs are fine. Answering 401
tells them to re-authenticate against a problem they cannot reach, and this repository's
`error_utils.is_client_safe_project_error` lets an `AuthenticationError` return its own message
verbatim, so the wording would also describe our provider configuration to them. The distinct type
exists for our side of the boundary: a log query, an alert, a vertical that wants to page someone
about a dead key rather than watch a provider status page. What crosses the wire is the same 502
and the same generic message any other upstream failure gets.

**A refused request stays a 500.** A `BadRequestError` is a tool schema the provider will not
accept, or a prompt past the context window: this service's own defect, and the identical retry
fails identically forever. Calling it 502 would say "the provider is unwell" and send whoever is on
call to a status page that is green.

**The translation lives outside the retry loop, not in its `except` clauses.** Tenacity re-reads
the type raised inside the loop to decide whether to retry, so raising a domain error there would
end retrying after one attempt — and would skip the existing log event that records the provider's
own message. Both inner handlers keep their bare `raise`; a single `try` around the whole loop
catches whatever finally escapes it, retryable or not.

## Consequences

- A vertical that calls the model catches `ExternalServiceError` and gets every provider failure,
  or catches `UpstreamAuthenticationError` first when it wants to treat a dead key differently.
- Swapping `langchain-openai` for another client changes this adapter and nothing downstream.
- `/health/ready` is unaffected: the readiness probe has always caught every exception itself and
  reported a status rather than raising. Whether the LLM check can fail the verdict is ADR-008.
- The matrix in `tests/application/test_llm_service_retry.py` is where the mapping is pinned, one
  case per provider error class, each asserting both the domain type and `__cause__`.
