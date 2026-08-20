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

**A good fit** when you are starting a small-to-medium HTTP service in Python, you expect an agent to
write most of it, and you would rather spend the first day copying a working example than assembling
a stack. Five services have been built on it so far, each of them by copying that one vertical and
deleting the original once its own worked.

**A poor fit** when you need a framework rather than a starting point. Mayak has no plugin system, no
generators beyond one skill, and no upgrade path: you clone it, and from then on the copy is yours.
It also assumes hexagonal layering and will fail the build if you import across the layers the wrong
way, which is the point but is not for everyone.

**Not included, by design:** authentication, background jobs, a message queue, caching, a vector
store, metrics, tracing backends, or a business pipeline. Those are decisions your service should
make, not inherit.

---

## Quick start

You need Python `3.13`, Docker with Compose, and a Unix shell. The runtime minimum and the dev
toolchain are the same version — see `docs/adr/ADR-002-python-version-policy.md`.

Press **Use this template** at the top of the repository page. GitHub hands you a repository of
your own: these files, one first commit that is yours, and no remote pointing back here. That is
the setup a clone would leave you to do by hand — detach the history, create the repository,
repoint the remote — and it is the reason the button exists.

Clone it instead if you only mean to read it. Either way it has to be a git checkout rather than a
downloaded ZIP: several gates ask git which files are tracked, so without `.git` they fail on a
repository that is otherwise perfectly healthy.

```bash
make init-project
```

That installs dependencies and creates `.env` from `.env.sample`, generating a local database
password as it copies — the sample ships a placeholder that a startup guard refuses, on purpose, so
copying it verbatim would hand you a container that exits before serving anything. It never
overwrites an existing `.env`, and it does not start Docker.

Then pick how you want the database. With one:

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build
```

Without one — for a service that needs no relational store — set `POSTGRES_ENABLED=false` in `.env`:

```bash
POSTGRES_ENABLED=false docker compose up -d --build
```

The database lives in the `docker-compose.postgres.yml` overlay, so it is attached with a second
`-f` rather than commented out. A bare `docker compose up` while `POSTGRES_ENABLED=true` starts the
app without a database, and `entrypoint.sh` exits 1 rather than serving a half-working service.

Once it is up: `http://localhost:8000/docs` for Swagger, `GET /health/` for liveness, and
`GET /health/ready` for readiness — which checks services, the LLM client, and the database when the
project uses one.

`make smoke` does the whole round trip for you: picks the compose files, waits for a healthy
container, calls both probes, and tears the stack down afterwards.

To run without Docker, start the database first (both files, because `db` lives in the overlay) and
then the app:

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d db
make run-local
```

`make run-local` migrates before it serves, the same order the Docker image uses.

---

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
order of the eleven files, the constraints each validator enforces, and the full sweep for removing
the example afterwards.

---

## Checking your work

Three commands, in order of cost:

```bash
make quality-gates    # ~15 s — lint, format, types, thirteen validators, unit and integration tests
make test-e2e         # ~25 s — the functional suite against a real Postgres in Docker
make ci-local         # ~75 s — everything the pipeline runs, both database modes included
```

`make quality-gates` is the loop to run while working. It regenerates the derived maps before
checking them, so a stale artifact prints a notice instead of a red gate, and it runs `make doctor`
itself when something fails — the doctor names the blocking layer and prints the shape of the fix
rather than a wall of output.

Two things it cannot do, worth knowing before you trust a green run:

- **It runs none of your SQL.** Reversing an `ORDER BY` in the shipped repository leaves every gate
  green and fails `make test-e2e`. A change under `project/infrastructure/persistence/`,
  `project/infrastructure/api/endpoints/`, or the wiring files is finished by the functional suite.
- **The migration check downgrades itself** to an informational skip when no database is reachable,
  so run it against one before trusting a green migration gate. In CI the skip is a hard failure.

`make ai-autofix` fixes formatting, lint and comment markup in one pass. Never hand-edit generated
files — `CLAUDE.md`, `AGENTS.md`, `docs/project_map.md` and `docs/ai_*.json` are rewritten from
their sources, and a pre-edit hook refuses the write.

The GitHub Actions workflow in `.github/workflows/ci.yml` runs the same ground as `make ci-local`;
a test keeps the two lists from drifting apart.

---

## Reading a running service

Every log line is one JSON object with a semantic event id, a trace id and a span id, so a request
can be followed without parsing prose:

```json
{"seq":7,"ts":"2026-03-20T14:22:02.340","level":"INFO","trace_id":"a1b2c3d4","span_id":"ff03cc","event_id":"llm.call","data":{"model":"gpt-4o","duration_ms":1322,"input_tokens":1847,"output_tokens":203,"success":true}}
```

Each root span closes with a `request.summary` carrying the aggregate: duration, outcome, status
code, child span count, LLM calls and token totals. `outcome` is `ok` (2xx/3xx), `client_error`
(4xx, logged at INFO) or `server_error` (5xx, logged at ERROR), so real failures are one filter away.

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
| `AGENT_LLM_MODE` | `mock` for dev, CI and tests; `live` to call a provider | `mock` |
| `AGENT_DEFAULT_LLM_TEMPERATURE` | LLM temperature (0.0-2.0) | `0.2` |
| `AGENT_MAX_TOKENS` | maximum output tokens | `4096` |
| `AGENT_LLM_READINESS_CHECK_MODE` | `probe` (network call) or `init` (client construction) | `probe` |
| `SERVER_CORS_ORIGINS` | allowed CORS origins; a wildcard is rejected at startup | `["*"]` |
| `APP_DEBUG` | `DEBUG` log level, single worker, Starlette's own error page | `false` |

Notes worth knowing:

- `POSTGRES_HOST=localhost` is for host-side tooling; the compose overlay overrides it to `db`
  inside the network. Without the second `-f`, the container looks for the database inside itself.
- `POSTGRES_ENABLED=false` removes the connection pool, the migrations and the database's vote in
  `/health/ready`. See `docs/adr/ADR-006-optional-postgres.md`.
- A wildcard CORS origin is refused at startup when `APP_DEBUG=false`, together with the default
  database password — the guard exists so a placeholder cannot reach a deployment.
- `.env.sample` is the documentation for every variable; a test compares its values against the
  defaults declared in code, so the two cannot drift.

---

## Where to read more

| Document | What it answers |
|---|---|
| `docs/agent_rules.md` | the operating contract: where a fact goes, how the kernel is shaped, what each gate enforces |
| `CLAUDE.md`, `AGENTS.md` | the same contract, generated for Claude Code and for Codex — do not edit either directly |
| `docs/adr/` | why the load-bearing decisions were made, one dated document each |
| `docs/project_map.md` | generated file tree with a one-line summary per module |
| `PROJECT.md` | what a given project does — the file you rewrite first |

Comments in the code carry the reasoning: where a decision was hard, the file says what was tried and
why the obvious alternative was rejected. Those comments are the channel that reaches a reader who
arrives months later, and they are kept honest on purpose — if a number appears, the command that
measured it appears beside it.
