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
shows the tool name and, for that `agent.tool.` prefix only, the span's `input_params` inline — so a
tool call is legible without a wrapper of the project's own.

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
