# Mayak

<div align="center">
<pre>
   ░░▒▒▓▓██████████████████████████▓▓▒▒░░   
███╗   ███╗ █████╗ ██╗   ██╗ █████╗ ██╗  ██╗
████╗ ████║██╔══██╗╚██╗ ██╔╝██╔══██╗██║ ██╔╝
██╔████╔██║███████║ ╚████╔╝ ███████║█████╔╝ 
██║╚██╔╝██║██╔══██║  ╚██╔╝  ██╔══██║██╔═██╗ 
██║ ╚═╝ ██║██║  ██║   ██║   ██║  ██║██║  ██╗
╚═╝     ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝
    ▂▃▄▅▆▇███████████████████████▇▆▅▄▃▂     
</pre>
</div>

A FastAPI backend template for services that are built and maintained by coding agents.

Most templates optimise for the first hour: they hand you a running app. Mayak optimises for the
hundredth change, made by someone — human or model — who was not there when the first hour happened.
It ships one worked vertical to copy, a validation suite that fails on the mistakes this kind of
codebase actually makes, and logs that can be read without grep gymnastics.

**Stack:** Python 3.13+ · FastAPI · PostgreSQL (psycopg at runtime, SQLAlchemy only as Alembic's
metadata source, optional) · OpenAI-compatible LLM client via langchain-openai · semantic NDJSON
logging.

---

## Contents

- [Is this for you](#is-this-for-you)
- [Quick start](#quick-start)
- [Your first vertical](#your-first-vertical)
- [Checking your work](#checking-your-work)
- [Reading a running service](#reading-a-running-service)
- [What it deliberately does not ship](#what-it-deliberately-does-not-ship)
- [Environment configuration](#environment-configuration)
- [Where to read more](#where-to-read-more)

---

## Is this for you

**A good fit** when the service will be changed for months by people and agents who were not there
when it was written, and you would rather spend the first day copying a working example than
assembling a stack. Everything here is built for that case: the validators, the comment convention,
the one worked vertical. The payoff grows with the number of changes made by a stranger to the code,
and with the number of agents making them at once.

**A poor fit** for a thin CRUD service you will write once and rarely touch. The template charges its
tax before the first endpoint: hexagonal layering the build enforces, a comment header on every
file, ten validators to satisfy, and a pull request per change.
On a small MVP that tax is real and there is not enough change ahead of you to repay it. Write it by
hand.

**A poor fit** too when you need a framework rather than a starting point. Mayak has no plugin
system, no generators beyond one skill, and no upgrade path: you clone it, and from then on the copy
is yours.

**Not included, by design:** authentication, background jobs, a message queue, caching, a vector
store, metrics, tracing backends, or a business pipeline. Those are decisions your service should
make, not inherit.

---

## Quick start

Use Python 3.13+ (toolchain `3.13`), [uv](https://docs.astral.sh/uv/), Git and Make. PostgreSQL and Docker end-to-end
tests use Docker with the Compose plugin.

Create a repository with **Use this template** on GitHub, then:

```sh
make bootstrap NAME="Room Book" DOMAIN="Manage room bookings" DB=postgres
make run-local
```

For a stateless service pass `DB=none`. Bootstrap generates the product identity, records which
template tree it came from and moves the example into the scaffold. It creates missing local
configuration and checks a temporary loopback server. Use `make run-local` for Swagger at
`http://127.0.0.1:8000/docs` and `/health/ready` for readiness.

For the template itself or an existing service, run `make init-project`. Use `make db-up-worktree`
when it uses PostgreSQL. This checkout's database port is picked up by `make migrate` and
`make run-local`. Use `make db-down-worktree` when you want to remove this worktree's database.

To test the application image, use `make test-e2e`. For an interactive Docker stack, use
`docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build` when PostgreSQL
is enabled or `docker compose up --build` for the stateless profile. The default host bindings are
loopback. Git hooks are opt-in via `make install-hooks`.

## Your first vertical

The template ships exactly one feature, `reference_task`, and it exists to be copied: a domain model,
a port, a psycopg repository, an application service, DTOs, an endpoint, unit tests and functional
tests — plus three wiring edits. One example rather than a catalogue, because a second one would be
either a duplicate or a guess about your domain.

Four files carry every wiring change, and they are the ones to read first:

| File | What it holds |
|---|---|
| `project/core/composition_root.py` | shared services and `app.state.services` |
| `project/core/service_registration.py` | per-vertical services |
| `project/infrastructure/api/router_registration.py` | router inclusion |
| `project/infrastructure/api/dependencies.py` | typed dependency getters and aliases |

Two skills carry the procedures. They live in `.agents/skills/`, which is where Codex looks, and
`.claude/skills/` holds a symlink to each so Claude Code finds the same file. Either agent can
invoke them by name; both are plain markdown and read fine on their own.

| Skill | Purpose |
|---|---|
| `initialize-project` | turning a fresh clone into your project — naming, identity, first validation |
| `add-vertical` | adding a vertical end to end, and deleting the reference one when yours works |

`.agents/skills/add-vertical/SKILL.md` is worth reading even if you write the code yourself: it lists the
order of the twelve files, the constraints each validator enforces, and the full sweep for removing
the example afterwards.

---

## Checking your work

```sh
make gate-fast       # Format, lint, types and layer rules while editing
make verify          # Strict full gate, complete diff coverage and applicable Docker E2E
make ci-local        # Local equivalent of the CI jobs, including dependency audit
```

`make quality-gates` runs the full gate by itself. Locally it can format changed Python and
refresh the generated wrappers. The strict checks in `make verify` and CI report any needed
fixes. If a step fails, its output supplies the diagnosis. `make doctor` is available on its own.

The full gate includes the **db tier**. It tests queries and schema changes against disposable
PostgreSQL. You can set `TEST_DATABASE_URL` to a PostgreSQL test database you control. A stateless
project skips the db tier. A host HTTP test must execute the body of every handler. Docker E2E runs separately and lists any product test files in the image. A clean repository without an integration branch still checks routes and the image; pass `DIFF_COMPARE_BRANCH=<starting commit>` to measure committed changes.
Use `make verify E2E=1` to request it for a diff that would otherwise skip it.
For a committed change, pass `make verify DIFF_COMPARE_BRANCH=<baseline>`. The command labels
zero measured lines as skipped, and its `verify: scope:` line names what it measured:
`change since <ref> (explicit base)` for the branch you passed; `change since <ref> (auto base)`
for `origin/HEAD`, `origin/main` or `origin/master`; `project only (clean tree, no base)` for a
clean checkout with neither; `uncommitted paths only (no base)` when only the working tree was
measured, which ends with `project and uncommitted paths only; commits were not measured` and
prints the full handoff command, `make verify DIFF_COMPARE_BRANCH=<starting commit>`. Resolve
files that differ between the index and working tree before verification. Changes under
`project/` select Docker E2E even when the HTTP handler stayed the same.

For PostgreSQL scheduling rules, see the [concurrent write recipe](docs/recipes/postgres-concurrency.md).

Edit `docs/agent_rules.md` and run `make refresh-agent-docs` to update the shared agent contract.
The `initialize-project` and `add-vertical` skills cover setup and feature work.
The [acceptance spec](docs/specs/agent-onboarding.md) defines the template's onboarding checks.

---

## Reading a running service

Every log line is one JSON object with a semantic event id, a trace id and a span id, so a request
can be followed without parsing prose:

```json
{"seq":7,"ts":"2026-03-20T14:22:02.340","level":"INFO","trace_id":"a1b2c3d4","span_id":"ff03cc","event_id":"llm.call","data":{"model":"gpt-4o","duration_ms":1322,"input_tokens":1847,"output_tokens":203,"success":true}}
```

Each root span closes with a `request.summary` carrying the aggregate: duration, outcome, status
code, child span count, LLM calls and token totals. `outcome` is `ok` (2xx/3xx), `client_error`
(4xx, logged at INFO), `server_error` (5xx, logged at ERROR) or `cancelled` (the request's task was
cancelled before it answered, logged at WARNING), so real failures are one filter away.

```bash
make logs                          # the container's log, rendered as a trace tree
make logs ARGS="--trace a1b2c3d4"  # one request
make logs-raw                      # raw NDJSON under logs/, for grepping
make format-trace ARGS="<file>"    # render a local NDJSON file
```

The trace tree is the fastest way to answer "what did this request actually do" — it shows the span
hierarchy with durations and the LLM calls inline. `ENABLE_FULL_TRACE=true` adds prompts and
completions to a file log; it is off by default because the file grows with every model call.

---

## What it deliberately does not ship

No graph library, no streaming endpoint, no orchestration framework. LLM access is one service,
`project/infrastructure/agents/llm_service.py`, with retries, semantic events and a mock mode that
answers deterministically — including tool calls, so an agent loop runs in CI with no API key. A
vertical that needs a graph library adds one; see `docs/adr/ADR-003-mock-first-llm-mode.md`.

No external memory integration, no metrics exporter, no feature-flag system. Each of those was
either removed after measuring that it earned nothing, or never added for the same reason.

No SQL or request deadlines. The pool drops a dead TCP peer (`keepalives`, `tcp_user_timeout`)
and the database probe in `/health/ready` has a 2 s budget, but a query to a server that keeps the
connection open and stops answering waits with no limit, and so does the request that sent it
(measured: a GET against a frozen database was still waiting after 45 s). The right limits depend
on the product's queries, so the product sets them: `statement_timeout` and `lock_timeout` for the
application's connections — for example `"options": "-c statement_timeout=5s -c lock_timeout=2s"`
in the pool's `kwargs` in `project/core/composition_root.py`; an `ALTER ROLE … SET` would bind
migrations too when the container runs Alembic under the same role (the default
`APP_STARTUP_MODE=migrate-and-serve`; split migration and runtime roles by running a one-shot
`APP_STARTUP_MODE=migrate` step and starting replicas with `APP_STARTUP_MODE=serve`) — and a
deadline for the whole request, since a proxy timeout closes the client's connection but does not
stop the work behind it. Migrations wait for the advisory lock in `alembic/env.py` with no limit
either; bound that wait with the deadline of whatever runs them. In a rolling deploy old and new
code share one schema: add first (expand), and remove what old code still reads only after its
last replica is gone (contract). `/health/ready` stays `200` when `alembic_version` records a
revision ID unknown to the running image so `N-1` replicas stay in rotation after an expand
migration — which means `/health/ready` does not prove that a specific database is compatible with
the application (a misrouted or unrelated database also records an unknown revision and fails on
the first real query). Gate traffic cutover on an independent domain probe before shifting traffic,
or enforce strict product-level schema verification when rolling out solely on `/health/ready`.

---

## Environment configuration

### Required

| Variable | When it is required | Default |
|----------|---------------------|---------|
| `OPENAI_COMPATIBLE_API_KEY` | `AGENT_LLM_MODE=live` | `""` |
| `OPENAI_COMPATIBLE_BASE_URL` | a custom LLM provider | `None` |
| `OPENAI_COMPATIBLE_MODEL` | live mode | `gpt-3.5-turbo` |
| `POSTGRES_USER` / `PASSWORD` / `DB` | `POSTGRES_ENABLED=true` | `postgres` / `postgres` / `app` |

### Optional

| Variable | Purpose | Default |
|----------|---------|---------|
| `APP_STARTUP_MODE` | container entrypoint mode (env only; positional arguments are refused): `migrate-and-serve` validates settings, migrates when `POSTGRES_ENABLED=true`, and starts the server; `migrate` validates settings, migrates (or no-ops when `POSTGRES_ENABLED=false`), and exits `0`; `serve` validates settings and starts without running migrations | `migrate-and-serve` |
| `AGENT_LLM_MODE` | `mock` for dev, CI and tests; `live` to call a provider | `mock` |
| `AGENT_DEFAULT_LLM_TEMPERATURE` | LLM temperature (0.0-2.0) | `0.2` |
| `AGENT_MAX_TOKENS` | maximum output tokens | `4096` |
| `AGENT_LLM_READINESS_CHECK_MODE` | `init` checks only that the provider client was constructed at startup, at zero ongoing cost; `probe` calls the provider when no result from the last 30 s is cached — success and failure are both cached, per instance | `init` |
| `AGENT_LLM_READINESS_CRITICAL` | whether an unhealthy LLM check makes `/health/ready` report unready | `false` |
| `SERVER_CORS_ORIGINS` | allowed CORS origins; a wildcard is rejected at startup under the conditions below | `["*"]` |
| `SERVER_CORS_ALLOW_CREDENTIALS` | whether the app sends `Access-Control-Allow-Credentials`; `false` makes a wildcard origin list safe | `true` |
| `SERVER_MAX_BODY_BYTES` | largest request body the app reads; a larger one is answered 413. Headers are parsed by the server first — limit them at the proxy | `1048576` |
| `APP_DEBUG` | `DEBUG` log level, single worker, and three startup guards switched off: the wildcard check on `SERVER_CORS_ORIGINS`, the placeholder checks on `POSTGRES_PASSWORD` and `OPENAI_COMPATIBLE_API_KEY`. Not Starlette's traceback page — `FastAPI(debug=False)` is hard-wired | `false` |

Notes worth knowing:

- `POSTGRES_HOST=localhost` is for host-side tooling; the compose overlay overrides it to `db`
  inside the network. Without the second `-f`, the container looks for the database inside itself.
- `POSTGRES_ENABLED=false` removes the connection pool, the migrations and the database's vote in
  `/health/ready`. See `docs/adr/ADR-006-optional-postgres.md`.
- A wildcard CORS origin is refused at startup only when `APP_DEBUG=false` **and**
  `SERVER_CORS_ALLOW_CREDENTIALS=true` (the default) — that combination is what lets any site make
  authenticated cross-origin requests. Either list the exact origins this deployment serves, or set
  `SERVER_CORS_ALLOW_CREDENTIALS=false` if the API is public or token-authenticated and never relies
  on cookies.
- `.env.sample` is the documentation for every variable; a test compares each value against the
  default the code declares for it, except the placeholders and local conveniences it lists by name.

---

## Where to read more

| Document | What it answers |
|---|---|
| `docs/agent_rules.md` | the operating contract: where a fact goes, how the kernel is shaped, what each gate enforces |
| `AGENTS.md` | the operating contract as an agent loads it: Codex directly, Claude Code through the `CLAUDE.md` that imports it — do not edit either directly |
| `docs/adr/` | why the load-bearing decisions were made, one dated document each |
| `docs/upgrades.md` | for a service made from an earlier version: what changed in the kernel it inherited, what to port first, how to check |
| `git grep -n '^# SUMMARY:'` | a one-line summary per module: every Python file opens with one |
| `PROJECT.md` | the current description of the product — the file you rewrite first |
| `docs/project_context.json` | required metadata and template provenance; not a second description |

Comments in the code carry the reasoning: where a decision was hard, the file says what was tried and
why the obvious alternative was rejected. Those comments are the channel that reaches a reader who
arrives months later, and they are kept honest on purpose — if a number appears, the command that
measured it appears beside it.
