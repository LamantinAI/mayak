# Upgrading a service made from an earlier Mayak

For the owner of a service made from this template before its latest changes: what changed in the
kernel the service inherited, what breaks without it, and how to check whether your copy has it.
Nothing here updates a service by itself — a service has edited the shared files, so each change is
ported by hand, one at a time.

## Find your version

The first row of **Versions** in *your* copy of this file is the version your service was made
from. No `docs/upgrades.md` in your copy means it was made before the file arrived (#39): start at #13
and let each row's check tell you whether your copy already has the change. Port in the order of
**What to port first** below, not in PR order.

| since | pull requests | what the batch was |
|---|---|---|
| 2026-09-28 | #36, #37, #38 | fixes the bench3 measurement asked for: agent texts, nine kernel defects, tools |
| 2026-09-27 | #35 | texts an agent and an operator read, before bench3 |
| 2026-09-24…26 | #13–#34 | after bench2: a db test tier, a thinner sample, the review rounds' fixes |

A pull request to the template that changes what a service inherits adds its row here, and its
lines below, in the same pull request.

## How to port one change

1. Check your copy with the command in the table. If the check finds the change, skip it.
2. Read the pull request's diff for the files named (`gh pr diff <N> --repo LamantinAI/mayak`),
   and apply it to your files by hand — shared files such as `composition_root.py` differ in your
   service, so copying the template's file over yours drops your own lines.
3. Run `make quality-gates`; add `make test-e2e` when the change touches an endpoint, the wiring,
   `entrypoint.sh` or a migration.

## What to port first

### 1. Security

| PR | what goes wrong without it | check your copy | files |
|---|---|---|---|
| #32 | a PostgreSQL password with `@ % : /` crashes migrations and prints the encoded DSN in the traceback; the config log shows the provider URL whole | `grep -n '%%' alembic/env.py` finds the escape | `alembic/env.py`, `project/core/composition_root.py`, `scripts/validate_secrets.py` |
| #26 | a rejected request's text — what the client typed — lands in the log of its 409 or 422 | `grep -n describe_rejection project/core/logging/logger.py` | `project/domain/exceptions.py`, `project/core/logging/`, `project/core/pydantic_errors.py`, `project/core/error_utils.py`, `project/infrastructure/api/exception_handlers.py`, `project/infrastructure/agents/tool_runner.py`; ADR-013 |
| #25 | one 200 MB POST took the process from 138 to 1 655 MB; error responses dropped `WWW-Authenticate`, `Retry-After`, `Allow`; a 500 had no `X-Request-ID`; a failure inside dependency injection answered 400 instead of 500 | `grep -n max_body_bytes project/core/config_settings_core.py` | `project/infrastructure/api/middleware.py`, `exception_handlers.py`, `dependencies.py`, `project/core/config_settings_core.py`, `composition_root.py` |
| #30 | a 500 carries no CORS headers, so a browser reports a CORS error instead of the failure | `grep -n build_middleware_stack project/core/composition_root.py` | `project/core/composition_root.py` |

### 2. Data and lifecycle

| PR | what goes wrong without it | check your copy | files |
|---|---|---|---|
| #38 | a container refused for its settings (a wildcard CORS origin, a placeholder password) has already migrated the database it shares | in `entrypoint.sh`, `validate_runtime` comes before `alembic upgrade head` | `entrypoint.sh` |
| #33 | after a database restart every stale pooled connection answers 500; `POSTGRES_POOL_SIZE` of 1–3 crashes startup; a frozen connection hangs readiness; a startup cancelled mid-way leaks the pool | `grep -n 'check=AsyncConnectionPool.check_connection' project/core/composition_root.py` | `project/core/composition_root.py`, `project/core/lifecycle.py`, `project/infrastructure/api/endpoints/health.py` — after #24 |
| #24 | `/health/ready` answers 200 on a schema that is unmigrated or a migration behind | `grep -n 'not migrated' project/infrastructure/api/endpoints/health.py` | `project/infrastructure/api/endpoints/health.py` |
| #13 | `SERVER_WORKERS` above 1 exits at startup with code 3 | `grep -n 'factory=True' project/launcher/main.py` | `project/launcher/main.py` |
| #13 | a tool whose arguments are a `CoreModel` reaches the model with an empty schema, so the model sees no arguments — mock mode does not notice | `grep -n ToolArgs project/application/core_model.py` | `project/application/core_model.py`, `project/infrastructure/agents/llm_service.py` |
| #34 | `make init-project` reports success after a failed dependency install and leaves no `.env` | `head -3 dev_setup.sh` shows `set -e` | `dev_setup.sh` |
| #15 | `make test` runs no query against a real database, so a wrong SQL filter or order passes every fast test | `ls tests/db/stack.py` | `tests/db/`, `Makefile`, `scripts/run_all_tests.py`; ADR-010 |

### 3. The language model

| PR | what goes wrong without it | check your copy | files |
|---|---|---|---|
| #31 | one logical call can make up to nine paid provider requests (the SDK's retries inside Tenacity's); `Retry-After` is ignored; a malformed reply reaches the caller raw | `grep -n max_retries project/infrastructure/agents/llm_service_live.py` shows 0 | `project/infrastructure/agents/llm_service_live.py`, `tool_runner.py` |
| #37 | a 200 whose message content is an object answers 500 instead of 502 | `grep -n ValidationError project/infrastructure/agents/llm_service_live.py` | `project/infrastructure/agents/llm_service_live.py` |
| #23 | the trace shows the model's calls but not which tools it ran or with what | `ls project/infrastructure/agents/tool_runner.py` | `project/infrastructure/agents/tool_runner.py` — before #26 and #31 |

### 4. Operations

| PR | what goes wrong without it | check your copy | files |
|---|---|---|---|
| #37 | the `request.summary` of an unhandled 500 has no status code — the one line an operator filters for | `grep -n 'observer.status_code = 500' project/infrastructure/api/middleware.py` | `middleware.py`, `project/core/logging/logger.py` |
| #37 | the OpenAPI schema declares a 422 body the service never sends | `curl -s localhost:<port>/openapi.json \| grep -c ErrorEnvelope` | `project/infrastructure/api/exception_handlers.py` |
| #37 | the layer validator passes `from project import infrastructure` in the application layer | `grep -n _resolve_import_names scripts/validate_architecture.py` | `scripts/validate_architecture.py` |
| #28 | a domain class named like a guarded resource (`Client`) turns the gate red on correct code | `grep -n _RESOURCE_MODULES scripts/validate_runtime_ownership.py` | `scripts/validate_runtime_ownership.py` |
| #22 | no seconds-long gate while editing; format and lint errors a machine could fix fail the full gate | `grep -n '^gate-fast:' Makefile` | `Makefile`, `.githooks/pre-commit`; ADR-012 |
| #14 | two `make test-e2e` runs on one host build over each other's image | `grep -n 'test-app-image' tests/functional/docker-compose.yml` finds nothing | `tests/functional/docker-compose.yml` |
| #13 | an e2e helper dropped the query string written into a path, so a filter test checked the unfiltered list | `grep -n copy_merge_params tests/functional/conftest.py` | `tests/functional/conftest.py` |
| #38 | every push runs a `product-from-template` CI job that installs everything and checks nothing in a service | `grep -c product-from-template .github/workflows/ci.yml` is 0 | `.github/workflows/ci.yml`: delete the job |
| #38 | the pre-edit guard lets `./AGENTS.md` through; the suite reads your `AGENT_PROMPTS_DIR`; the doctor advises regenerating the lock when uv could not run | `grep -n 'pwd -P' .agents/hooks/pre-edit-guard.sh` | `.agents/hooks/pre-edit-guard.sh`, `tests/conftest.py`, `scripts/doctor_ai_context.py` |

### 5. Texts and removed layers — when convenient

What an agent reads in your service, and weight the template has shed. Nothing breaks without them;
an agent pays in turns for text that no longer matches the code.

| PR | what changed | check your copy |
|---|---|---|
| #36 | add-vertical: test data per query shape, a translator and a lock on every write path, 422 on an unparseable filter; ADR-003 on what mock mode cannot check; ADR-007 on every writer; escalating on the same cause; reading one request's trace | `grep -n 'twelve files' .agents/skills/add-vertical/SKILL.md` |
| #35 | the add-vertical rule on what an endpoint may import; readiness probe caching in README | `grep -n cached README.md` |
| #29 | `docs/project_context.json` without the unchecked `verticals` and `business_rules` | `python3 -c "import json; print('verticals' in json.load(open('docs/project_context.json')))"` prints False |
| #21 | the navigation maps and their query tool are gone | `ls ai_context ai_query` finds nothing |
| #18 | only the two-line file header of Code-Base Markup is enforced | `wc -l scripts/validate_cbm.py` is about 200, not 900 |
| #17, #19 | ADR-007 on rules between rows; add-vertical on declaring a bound once | `grep -n 'Where the single-row token does not reach' docs/adr/ADR-007*` |

The template's own tools — the mutation runner, the extraction script, `check-product`, their
tests — are not part of a service. #14, #16, #20, #27 and #34 changed mostly them, and #37 and #38
changed them beside the kernel lines in the tables above: port those lines, not the tools.

## Changes that touch the same file

- `project/core/composition_root.py` — #25, #30, #32, #33, each in a different place: expect to
  merge by hand.
- `project/infrastructure/api/endpoints/health.py` — #24 before #33.
- `project/infrastructure/agents/tool_runner.py` — created by #23; #26 and #31 edit it after.
- `project/infrastructure/api/exception_handlers.py` — #25, then #26, then #37.
- `project/infrastructure/api/middleware.py` — #25, then #37 (it edits a block #25 added).
- `project/infrastructure/agents/llm_service_live.py` — #31, then #37 (it extends #31's catch).

No change in this list alters a table: #32 escapes the DSN Alembic reads, #38 reorders the
entrypoint. The one migration among them, #17's, belongs to the sample a service does not have.
