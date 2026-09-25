# Reading a Trace

The service logs semantic NDJSON with trace trees. This is the reference for reading one — what a
span name means, which log level a given outcome gets, and how to tell a routine rejection from a
real crash. `AGENTS.md` points here and states only the one command an agent reaches for at the
keyboard: `make format-trace ARGS="<logfile>"` (a local file) or `make logs` (a running container).

## Rendering a trace

- `make format-trace ARGS="<logfile>"` renders a local NDJSON file as a compact LLM-friendly text
  tree. Accepts `--trace <ID>` to filter to one request. Trace summaries are prepended to the log
  file on application shutdown when deep trace is enabled.
- `make logs` renders the running container's semantic log as a trace tree; `make logs-raw` dumps
  the raw NDJSON under `logs/`, for grepping instead of reading. Both pass `-p $(WORKTREE_PROJECT)`,
  the Compose project `make db-up-worktree` creates, so they read this checkout's own containers and
  not a sibling worktree's. `LINES=N` limits how far back to read; `ARGS="--trace <ID>"` narrows to
  one request.

## Filtering to real failures

Filter with `"outcome":"server_error"` in `request.summary`. `client_error` is 4xx and routine;
`ok` is 2xx/3xx; `cancelled` is a span cut short by something that is not an `Exception` — for an
HTTP request, uvicorn's graceful-shutdown timeout cancelling its task; `KeyboardInterrupt` and
`SystemExit` take the same path, logged at WARNING. Narrow to HTTP traffic with
`"span_name":"http_request"` — those summaries carry `status_code`. The summary emitted for
`application_lifecycle` at shutdown has no HTTP result and so reports `ok` whatever happened during
the run; read its `error_count` instead.

## Span names

Span names are read by the renderer, not only by people: `db.<vertical>.<operation>` for one
repository call, `agent.tool.<tool_name>` for one tool call inside an agent loop. The compact view
shows the tool name and, for that `agent.tool.` prefix only, the span's `input_params` inline.

An agent loop calls its tools through `run_tool(tool, arguments, shown=(...))` from
`project/infrastructure/agents/tool_runner.py`, which opens that span at INFO and records how many
characters came back. An argument named in `shown` — an id, an enum — is recorded as it is; every
other one as its type and size, since a tool argument is routinely what a user typed. Arguments that
miss the tool's schema raise `ToolArgumentsError`, naming each field and the problem without
pydantic's quoted input values — the text a loop hands back to the model.
`tests/application/test_mock_agent_multi_tool_loop.py` is a loop to copy.

## A database call is a span an operator can see

Every repository method opens `db.<vertical>.<operation>` with `level=logging.INFO` and puts its
outcome in `span.output` — a row count, a found/not-found flag, rows written. Both halves were
measured on a live container:

- Without the spans, a request whose query returned the wrong rows rendered as `OK 200, 0 spans`,
  the same as a correct one. A span that records only its duration hides the same thing: a query
  that matched nothing and one that found the row read alike.
- A child span defaults to DEBUG and production runs at INFO, so a span left at its default exists
  and shows nothing — the same `0 spans` again.

The price, measured with a handler that writes nothing: 3 639 ns per span filtered against 21 682 ns
written — three spans per request at 1 000 rps is 1.1% of one core against 6.5%.

## A routine rejection is not a crash

A `ProjectError` caught inside a `logger.span(...)` is judged the way `exception_handlers.py` judges
it a moment later: `project.domain.exceptions.is_client_rejection` separates ERROR with a traceback
from WARNING without one. A routine 409 raised inside a `db.*` span used to read exactly like a
crash, and in both projects of a duel the real errors drowned in those tracebacks. The compact
renderer marks it `⚠`, distinct from `⊘` (cancelled) and `✗` (a genuine error).

## A truncated LLM reply still reports success

`llm.call` carries the provider's `finish_reason` beside `success`. `length` means the reply was cut
off by an output-token or tool-schema limit, so it is logged at WARNING and marked `⚠` even though
`success` stayed `true` — a truncated tool argument otherwise reads as an ordinary successful call,
which cost one duel agent its whole live-run stage. Check `finish_reason` before trusting a green
`llm.call`, not just `success`.

## Operationalization

- `project/core/logging/trace_formatter.py` — the compact renderer: outcome coloring, the `⚠`/`⊘`/`✗`
  markers, and the `agent.tool.` `input_params` inline rendering.
- `project/domain/exceptions.py` — `is_client_rejection`.
- `Makefile` — `format-trace`, `logs`, `logs-raw`.
