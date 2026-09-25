# ADR-013: What a client sent stays out of the log

## Status

Accepted, 2026-09-25 (the owner's choice between keeping the values with this ADR and keeping them
out).

## Decision

When the application rejects a request, the value the client sent goes back to that client in
the response, and does not go into a log record through the text of the error that rejected it.
The log carries what an operator acts on: the error's type, the field it names, pydantic's error
type, the input's type and size, the status, the request id.

This applies to every 4xx the kernel writes down:
- a domain rejection raised inside a span — `span.error` carries `describe_rejection(error)`, the
  type and the field (`project/domain/exceptions.py`), never `str(error)`;
- `log_client_error`, which every 4xx handler calls — it records the exception's type and that same
  description, never its text, so a 4xx added later is covered too;
- a request pydantic refused — each field's message is logged only when it names no value
  (`project/core/pydantic_errors.py`); the 422 response still carries the message whole;
- a tool call's arguments — `ToolArgumentsError` names each field with pydantic's message only when
  it names no value.

Separately, the NDJSON formatter removes credential shapes from the text of a WARNING or worse
record, whoever wrote it. That is about secrets, not about client values: it catches a DSN, not a
title.

## What this does not cover — by design

Each is recorded as it is, and a vertical should know it:
- **Identifiers and enumerations.** A repository span records the id it read and the status it
  filtered by; `run_tool(shown=...)` records the arguments named there verbatim. Ids and enum values
  are what a trace is read by.
- **Correlation metadata.** The `http_request` span records the path, the client's IP and
  User-Agent; `X-Request-ID`, `traceparent`, `X-Session-ID` and `X-User-ID` become the trace's ids
  and context. Do not put personal data in a path or in those headers.
- **Keys the client chose.** A field path is logged, and for an unknown key (`extra_forbidden`) or a
  dict entry that path is the client's own key.
- **A failure's own text.** An ERROR or CRITICAL record keeps its exception's text and traceback,
  with credential shapes removed: a crash needs its cause, and a crash can quote an input. So can a
  provider's error text in an LLM warning.
- **The opt-in full trace.** `ENABLE_FULL_TRACE=true` writes prompts and completions to a separate
  file; it is off in `.env.sample` and turning it on is a data-retention decision.

## Why

- **Measured.** On a live server with a vertical copied from the sample, 50 conflicting requests put
  a berth's name into 100 log lines: the 409's text reached both `span.error` and the handler's
  record. A 422 echoed the rejected status into a WARNING record.
- **The template already chose this for tool arguments.** Step 9 of the plan after bench2 stopped
  recording tool-argument values; a rejection's text leaked the same values by another door.
- **The complement is not a safe list.** Logging pydantic's message for every type except
  `value_error` and `assertion_error` looked safe; the independent check found `union_tag_invalid`
  repeating the caller's tag and a dozen parsing errors quoting the input. The list is explicit, and
  `tests/application/test_pydantic_errors.py` holds each entry to its message template.
- **INFO text is not redacted.** Redaction costs 8.8 µs a message against about 22 µs for a whole
  written span, and INFO is most of the volume; the semantic logger redacts its own `message` field
  at every level already.

## Reproduce

`tests/application/test_user_values_stay_out_of_logs.py` sends a synthetic value through a 409, a
domain 422, a field check's 422 and an `HTTPException` 404, and reads the NDJSON the project's
loggers write: the value is in each response and in no record.
