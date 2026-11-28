# Upgrading a service made from an earlier Mayak

For the owner of a service made from this template before its latest changes: what changed in the
kernel the service inherited, what breaks without it, and how to check whether your copy has it.
Nothing here updates a service by itself — a service has edited the shared files, so each change is
ported by hand, one at a time. A file the service never changed is taken whole from the target
version; **Port by tree diff** below shows how to establish which files those are.

## Find your version

Current descendants can run `make upgrade-check` for their recorded source tree and applicable
checks. To check an older project from a current Mayak checkout, run
`uv run --no-project python -m scripts.check_template_updates --root /path/to/service`.

The first row of **Versions** in *your* copy of this file is the version your service was made
from. No `docs/upgrades.md` in your copy means it was made before the file arrived (#39): start at #13
and let each row's check tell you whether your copy already has the change. Port in the order of
**What to port first** below, not in PR order.

| since | pull requests | what the batch was |
|---|---|---|
| 1.3.4 | early read-only Alembic graph and PostgreSQL preflight in `make verify`, env-only and `CI=true` reachability without `.env`, non-loopback `.env` and bad-config diagnostics | **Porting the 1.3.4 changes** below; `scripts/validate_migrations.py` and `scripts/verify_change.py` are replaced whole when the service never changed them. |
| 1.3.3 | env-only `APP_STARTUP_MODE` (`migrate-and-serve`, `migrate`, `serve`), honest readiness message on unknown schema revisions, two-step rollout rule before schema expansion | **Porting the 1.3.3 changes** below; ship the `entrypoint.sh` foundation in `N-1` before adding expand migrations in `N` (`v1.3.2` ignores `APP_STARTUP_MODE`). |
| 1.3.2 | test discovery from `tests/`, bootstrap migrates its own database, extraction overwrites and deletes nothing of the project's | **Porting the 1.3.2 changes** below; `scripts/run_all_tests.py` and `scripts/bootstrap_project.py` are replaced whole when the service never changed them. |
| 1.3.1 | shared readiness probe, single-parse trace view, migration-lock test checks its own database, extraction keeps local work | **Porting the 1.3.1 changes** below; `health.py`, `trace_formatter.py`, `trace_tree.py` and `test_migration_lock.py` are replaced whole when the service never changed them. |
| 1.3.0 | `ForbiddenError` answered 403, line classifier fix, three recipe rules | **Porting the 1.3.0 changes** below; `validation_support/line_metrics.py` is replaced whole when the service never changed it. |
| 1.2.1 | strict migration advice, verify scope, handoff rule, tree-diff porting | Port by tree diff below; `scripts/` files a service never changed are replaced whole. |
| 1.2.0 | onboarding, worktree database, verification and traces | Use the checklist below to port what the service needs. |
| 2026-11-20 | certifi lock refresh | Replace the affected certifi release before rebuilding images. |
| 2026-10-01 | #41 | preserve PostgreSQL connection components through libpq/SQLAlchemy; strengthen the password test and correct project context |
| 2026-10-01 | #36 (lock refresh), #40 | urllib3 security update; formatter-stable test opt-outs and complete tool-call batches in the copied LLM loop |
| 2026-09-28 | #36, #37, #38 | fixes the bench3 measurement asked for: agent texts, nine kernel defects, tools |
| 2026-09-27 | #35 | texts an agent and an operator read, before bench3 |
| 2026-09-24…26 | #13–#34 | after bench2: a db test tier, a thinner sample, the review rounds' fixes |

A pull request to the template that changes what a service inherits adds its row here, and its
lines below, in the same pull request.

## Porting the 1.3.4 changes

The reference is `git -C <mayak> diff v1.3.3 v1.3.4`. No table, runtime setting, HTTP route,
`/health/ready` contract, `entrypoint.sh` mode, bootstrap flow or `make` target changes.

| Change | What to port | How to check |
|---|---|---|
| Full `make verify` now runs a read-only offline Alembic graph and PostgreSQL reachability preflight immediately after printing `verify: scope:` and before `make quality-gates`, failing fast with the exact next action (`make init-project` + `make db-up-worktree`, `make db-up-worktree`, or explicit/CI target guidance) without running DDL or a second migration pass | `scripts/verify_change.py` and `scripts/validate_migrations.py`, whole when the service never changed them, plus the verify notes in `README.md` and `docs/agent_rules.md` (then `make refresh-agent-docs`) | On a PostgreSQL checkout without a running database (even on a docs-only diff), `make verify DIFF_COMPARE_BRANCH=HEAD` exits `1` right after `verify: scope:` with `verify: preflight: FAIL (migrations.database_required)` and skips `quality-gates`; `POSTGRES_ENABLED=false` and `--coverage-only` open no database connections. |
| Pure env-only PostgreSQL targets without `.env` (including partial `POSTGRES_*` overrides without `POSTGRES_USER`) and `CI=true` without `.env` against an already-listening database now probe the effective `PostgresSettings` target instead of falsely reporting unreachable; non-loopback `POSTGRES_HOST` in `.env` and invalid configuration fail cleanly without leaking secrets or advising `make db-up-worktree` | Included in `scripts/validate_migrations.py` above | `uv run python scripts/validate_migrations.py --require-database` succeeds with valid `POSTGRES_*` env vars and no `.env`, and refuses an explicit/CI unreachable target without suggesting `make db-up-worktree`. |

## Porting the 1.3.3 changes

The reference is `git -C <mayak> diff v1.3.2 v1.3.3`. No table, HTTP status code, JSON key or
default startup behaviour changes: omitting `APP_STARTUP_MODE` keeps `migrate-and-serve`.

| Change | What to port | How to check |
|---|---|---|
| Separating migration DDL from replica startup (`APP_STARTUP_MODE=migrate` and `APP_STARTUP_MODE=serve`, env only), and rejecting positional arguments, unknown/empty modes, and invalid runtime settings before touching PostgreSQL | `entrypoint.sh`, `docker-compose.yml`, `.env.sample` and `tests/application/test_entrypoint_checks_the_configuration_first.py` (plus `tests/functional/docker-compose.yml` if kept) | `grep -n 'APP_STARTUP_MODE' entrypoint.sh docker-compose.yml .env.sample`; `uv run pytest -q tests/application/test_entrypoint_checks_the_configuration_first.py`. |
| `/health/ready` claimed `"its schema is newer than this code"` whenever `alembic_version` held any revision outside the image's `alembic/` tree, including a misrouted or unrelated database | `project/infrastructure/api/endpoints/health.py`, `tests/application/test_health_endpoints.py`, `tests/db/test_readiness_schema.py`, and the rolling-deploy / readiness notes in `README.md` and `docs/agent_rules.md` (then `make refresh-agent-docs`) | `grep -n '_DATABASE_UNKNOWN_REVISION_MESSAGE' project/infrastructure/api/endpoints/health.py`; `/health/ready` still answers `200` on an unknown revision for rolling deploys and states that compatibility is not verified. |

**Two-step adoption is required before relying on `APP_STARTUP_MODE=serve` for rollback:**

1. **Images built on Mayak `v1.3.2` (and earlier) ignore `APP_STARTUP_MODE`** and unconditionally
   run `alembic upgrade head` when `POSTGRES_ENABLED=true`. If release `N` applies a new migration
   while the previous image `N-1` still has the `v1.3.2` entrypoint, rolling back to `N-1` — even
   with `APP_STARTUP_MODE=serve` set — runs `alembic upgrade head` against revision `N`, fails with
   `Can't locate revision identified by '<rev_N>'`, and exits `1` before the server starts.
2. **Step 1 — Foundation release (`N-1`):** Port and deploy the `1.3.3` `entrypoint.sh` *before*
   shipping any new Alembic migration, so the active rollback image already honours
   `APP_STARTUP_MODE=serve`.
3. **Step 2 — Expand release (`N`) and split startup:** Ship expand-compatible (additive) schema
   changes in `N`. Run `APP_STARTUP_MODE=migrate` once under migration credentials, then run `N`
   replicas (and `N-1` on rollback) with `APP_STARTUP_MODE=serve`, optionally under a database role
   without DDL privileges. Defer contract/destructive DDL to a subsequent release after `N-1` is
   retired.

**Readiness boundary and traffic gate:** `/health/ready` returns `200` (`healthy`) when
`alembic_version` contains a revision unknown to the running image so `N-1` replicas stay in
rotation during Step 2. Readiness therefore **does not prove** that the connected database belongs
to or is compatible with this application. Gate traffic cutover on an **independent domain probe**
against a real product endpoint before shifting traffic, or enforce **strict product-level schema
verification** when your deployment pipeline gates rollouts solely on `/health/ready`.

## Porting the 1.3.2 changes

The reference is `git -C <mayak> diff v1.3.1 v1.3.2`. No table, setting, endpoint, response
shape or `make` target changes, and nothing has to be configured.

| Change | What to port | How to check |
|---|---|---|
| `make test`, `make gate-tests` and the host part of `make verify` ran a fixed list of `tests/` directories, so a failing test in a directory the service added (`tests/domain/`) passed verify | `scripts/run_all_tests.py`, whole when the service never changed it; otherwise drop the directory arguments of the local step so pytest collects from `pytest.ini`'s `testpaths = tests`. **Every existing service needs this one.** | Put a failing test in a new directory, e.g. `tests/domain/test_canary.py` with `assert False`: `make verify DIFF_COMPARE_BRANCH=HEAD` fails and names it; fix the assertion and it passes. |
| `make bootstrap ... DB=postgres` migrated the database `.env` named, not the one it started, whenever `POSTGRES_HOST` there was not a host the settings loader knows as loopback (`localhost.` was enough) | `scripts/bootstrap_project.py`, whole when the service never changed it. Matters only for a service that still has it and may run it again. | `grep -n "_start_worktree_database" scripts/bootstrap_project.py`; bootstrap prints `bootstrap: using this worktree's database at 127.0.0.1:<port>`. |

The extraction's new guards — it overwrites no file in the scaffold and deletes only files the
template shipped unchanged, as listed in `TEMPLATE_ONLY_SHA256` — belong to the template's own
tooling (`scripts/extract_reference_vertical.py`), which a service made from it no longer has.

## Porting the 1.3.1 changes

The reference is `git -C <mayak> diff v1.3.0 v1.3.1`; **Port by tree diff** below sorts its paths.
No table, setting, endpoint, response shape or command changes, and nothing has to be configured.
Three of the four fixes are in files a service inherited; the fourth is template tooling.

| Change | What to port | How to check |
|---|---|---|
| Concurrent `/health/ready` calls got an instant 503 "Database check timed out" while the first probe of the same pool ran, and a cancelled request cancelled the probe | `project/infrastructure/api/endpoints/health.py`, whole when the service never changed it; otherwise `_active_db_probes` and the probe block of `_check_database`. Port the new readiness tests of `tests/application/test_health_endpoints.py`. | `grep -n "call_at(deadline" project/infrastructure/api/endpoints/health.py`; two `/health/ready` requests at once both answer 200 and borrow one connection. |
| `make format-trace ARGS="<file> --all"`, `make logs ARGS=--all` and the shutdown summary (`ENABLE_FULL_TRACE=true`) re-parsed the whole log once per trace | `project/core/logging/trace_formatter.py` and `project/core/logging/trace_tree.py`, whole when the service never changed them, and the new tests at the end of `tests/application/test_trace_formatter_failure_visibility.py` | `grep -n "def _trace_events" project/core/logging/trace_tree.py`; the output of both views is byte-identical to 1.3.0. |
| The functional migration-lock test checked the suite's database instead of the probe it migrated | `tests/functional/src/test_migration_lock.py`, whole when the service never changed it | `grep -n "_recorded_revisions" tests/functional/src/test_migration_lock.py`; `make test-e2e` on `DB=postgres` runs both of its tests. |

The extraction's refusal on local work in the template's paths (`scripts/extract_reference_vertical.py`)
belongs to the template's own tooling, which a service made from it no longer has.

## Porting the 1.3.0 changes

The reference is `git -C <mayak> diff v1.2.1 v1.3.0`; **Port by tree diff** below sorts its paths.
No table, setting or existing endpoint changes, and nothing has to be configured.

| Change | What to port | How to check |
|---|---|---|
| The application layer refuses an operation with 403 | Add `ForbiddenError` to `project/domain/exceptions.py`, its `HTTP_403_FORBIDDEN` branch to `_project_error_status` in `project/infrastructure/api/exception_handlers.py`, and the class to `is_client_safe_project_error` in `project/core/error_utils.py`. A service that wrote its own 403 class or handler keeps one of them: raise the kernel's class and delete the copy, or make its own a subclass of `ForbiddenError` and drop its handler. | `grep -n "class ForbiddenError" project/domain/exceptions.py`. Port the 403 cases of `tests/application/test_client_errors_are_not_service_errors.py` and `test_user_values_stay_out_of_logs.py` and run them: 403 in the `ProjectError` envelope, a WARNING under `client_error.*`, the message absent from the log. An `HTTPException(403)` still answers in its own envelope. |
| The module-size check mislabels every line after a form feed, U+0085 or U+2028 | `validation_support/line_metrics.py`, whole when the service never changed it | `grep -n split_physical_lines validation_support/line_metrics.py`; `make gate-fast` |
| Three rules for tests and locking | Steps 4, 5 and 7 of `.agents/skills/add-vertical/SKILL.md`; the paragraph on locking before a `JOIN` in `docs/recipes/postgres-concurrency.md` | `grep -n "Lock with one statement" docs/recipes/postgres-concurrency.md` |

The new unit of `make check-product` — lines of code instead of non-empty lines — belongs to the
template's own tooling, which a service does not have.

## Agent onboarding and verification

For an existing service, keep its identity, settings and migrations. Port the Makefile targets with
their runner, tests and Compose changes together. Update the shared agent rules, then regenerate
the wrappers. Start the isolated local database if the project uses one and run `make verify`.
The [specification](specs/agent-onboarding.md) describes the behavior to preserve.

## Porting the 1.2.0 changes to an existing service

Use [the 1.1.0 to 1.2.0 diff](https://github.com/LamantinAI/mayak/compare/v1.1.0...v1.2.0)
as the reference; locally, `git -C <mayak> diff --name-status v1.1.0 v1.2.0` lists the same
paths. Port changes into your service's own files and keep its identity, application code,
environment and migration history.

| Change | What to port | How to check |
|---|---|---|
| Project-wide verification | In `scripts/verify_change.py`, check all HTTP handlers using host coverage and report the integration base, HEAD and the count of uncommitted paths. Port the corresponding `Makefile`, CI diff base and agent rules. Bring the existing module-size check into `gate-fast`. | Start from a clean commit and run `make verify DIFF_COMPARE_BRANCH=<base>`. The summary reports zero uncommitted paths. A handler already present in the base still fails if no host HTTP test calls it. E2E reports the product test files it ran with. |
| PostgreSQL worktree settings | Port the common reader in `project/core/config_settings_core.py` and its callers in `alembic/env.py`, `scripts/validate_migrations.py`, `Makefile` and `project/core/composition_root.py`. Preserve explicit connection settings and the service's `.env`. | After `make db-up-worktree`, run `uv run alembic current` directly on the worktree. Confirm its reported port matches the assigned port, and repeat with an explicitly selected disposable DB. |
| SQL literals and reversible foreign keys | Copy the literal-query pattern from the PostgreSQL scaffold repository into relevant queries and port the foreign-key naming convention from `orm_models.py`. Leave old migration revisions intact. | Run `make security-scan` and `uv run alembic check`. For a newly generated foreign key, check that the downgrade names the key and test it on disposable PostgreSQL. |
| Short trace IDs and hidden spans | Port `project/core/logging/trace_tree.py` and `trace_formatter.py`. Use the span in `docs/recipes/stateless-http.md` as a safe example for important product decisions. | Run `make format-trace ARGS="<logfile> --trace <short-id>"`. A unique prefix selects one trace. A collision asks for more characters. Hidden spans are counted when the request summary reports them. |
| Live-only provider imports | In `project/infrastructure/agents/llm_service*.py`, keep provider imports on the live path. Update any tests that patched the former module-level SDK symbol. | Run the LLM tests. In a fresh mock-mode Python process, import and construct the service and check that `openai` and `langchain_openai` are absent from `sys.modules`. Live client construction still imports the SDK during startup. |

After porting, run `make verify DIFF_COMPARE_BRANCH=<your starting commit>`. For a database service,
also exercise at least one upgrade from an already populated disposable database.

## Port by tree diff

Needs a local Mayak clone with history and its tags; no network.

1. **Find the source tree.** `template.source_tree` in your `docs/project_context.json`;
   `make upgrade-check` prints the same value.
2. **List the paths.** `git diff` accepts a tree object, so no commit has to be found:
   `git -C <mayak> diff --name-status <source_tree> <target tag>`.
3. **Sort each path.**
   - Absent from the service and part of the template's tooling or the reference vertical: skip.
   - Unchanged by the service — `git hash-object <path>` equals
     `git -C <mayak> rev-parse <source_tree>:<path>`: take the target tag's file whole.
   - Changed by the service: port by hand against
     `git -C <mayak> diff <source_tree> <target tag> -- <path>`, keeping the service's own lines.
   - `pyproject.toml` and `uv.lock`, when the template's diff is only its own version number: skip.
4. **Finish.** `make refresh-agent-docs`; record the new `version` and `source_tree`
   (`git -C <mayak> rev-parse '<target tag>^{tree}'`) in `template`; run
   `make verify DIFF_COMPARE_BRANCH=<commit before the port>`; for a database service, upgrade one
   already populated disposable database.
5. **Fallback.** No `template` block, or `git -C <mayak> cat-file -e '<source_tree>^{tree}'` fails:
   fetch Mayak's full history first (`git fetch --unshallow --tags`). If the tree is still missing,
   follow **How to port one change** and **What to port first**: each row's check says whether
   your copy has the change.

## How to port one change

1. Check your copy with the command in the table. If the check finds the change, skip it.
2. Read the pull request's diff for the files named (`gh pr diff <N> --repo LamantinAI/mayak`),
   and apply it to your files by hand — shared files such as `composition_root.py` differ in your
   service, so copying the template's file over yours drops your own lines.
3. Run `make quality-gates`; add `make test-e2e` when the change touches an endpoint, the wiring,
   `entrypoint.sh` or a migration.

## What to port first

### 1. Security

For PYSEC-2026-4320, run `uv lock --upgrade-package certifi`, then `make audit-deps` and rebuild the application image. The template lock carries certifi 2026.10.23.

| PR | what goes wrong without it | check your copy | files |
|---|---|---|---|
| #36 (lock refresh) | locked urllib3 2.7.0 has three known security advisories and fails the dependency audit | `uv tree --locked --package urllib3` shows 2.8.0 or a newer fixed version; run `make audit-deps` | `uv.lock`: update urllib3 alone with `uv lock --upgrade-package urllib3` |
| #32 | a PostgreSQL password with `@ % : /` crashes migrations and prints the encoded DSN in the traceback; the config log shows the provider URL whole | `grep -n '%%' alembic/env.py` finds the escape | `alembic/env.py`, `project/core/composition_root.py`, `scripts/validate_secrets.py` |
| #26 | a rejected request's text — what the client typed — lands in the log of its 409 or 422 | `grep -n describe_rejection project/core/logging/logger.py` | `project/domain/exceptions.py`, `project/core/logging/`, `project/core/pydantic_errors.py`, `project/core/error_utils.py`, `project/infrastructure/api/exception_handlers.py`, `project/infrastructure/agents/tool_runner.py`; ADR-013 |
| #25 | one 200 MB POST took the process from 138 to 1 655 MB; error responses dropped `WWW-Authenticate`, `Retry-After`, `Allow`; a 500 had no `X-Request-ID`; a failure inside dependency injection answered 400 instead of 500 | `grep -n max_body_bytes project/core/config_settings_core.py` | `project/infrastructure/api/middleware.py`, `exception_handlers.py`, `dependencies.py`, `project/core/config_settings_core.py`, `composition_root.py` |
| #30 | a 500 carries no CORS headers, so a browser reports a CORS error instead of the failure | `grep -n build_middleware_stack project/core/composition_root.py` | `project/core/composition_root.py` |

### 2. Data and lifecycle

| PR | what goes wrong without it | check your copy | files |
|---|---|---|---|
| #41 | spaces in user/password become `+`, database punctuation or dot names change the selected database, raw IPv6 is rejected | port and run `uv run pytest -q tests/application/test_postgres_dsn_round_trip.py`; both native consumers must recover the original parameters | `project/core/config_builders.py`, `tests/application/test_config.py`, `tests/application/test_postgres_dsn_round_trip.py`; DSNs now carry the database in the decoded `dbname` query option, not the URL path |
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
| #40 | a loop copied from the sample executes only the first call in a multi-call reply and sends incomplete results to the model | `grep -n 'for call in response.tool_calls' tests/application/test_mock_agent_multi_tool_loop.py`; check loops already copied into your vertical too | `tests/application/test_mock_agent_multi_tool_loop.py`: execute all calls, each with its own result and call ID |
| #31 | one logical call can make up to nine paid provider requests (the SDK's retries inside Tenacity's); `Retry-After` is ignored; a malformed reply reaches the caller raw | `grep -n max_retries project/infrastructure/agents/llm_service_live.py` shows 0 | `project/infrastructure/agents/llm_service_live.py`, `tool_runner.py` |
| #37 | a 200 whose message content is an object answers 500 instead of 502 | `grep -nE '^[[:space:]]+ValidationError,' project/infrastructure/agents/llm_service_live.py` finds it in the catch around `ainvoke`, not a comment | `project/infrastructure/agents/llm_service_live.py` |
| #23 | the trace shows the model's calls but not which tools it ran or with what | `ls project/infrastructure/agents/tool_runner.py` | `project/infrastructure/agents/tool_runner.py` — before #26 and #31 |

### 4. Operations

| PR | what goes wrong without it | check your copy | files |
|---|---|---|---|
| #40 | Ruff wraps a signature and the test gate loses its allowed opt-out; a marker inside an f-string can waive verification | `grep -n FSTRING_START scripts/validate_test_quality.py` finds the depth guard; a permitted signature comment survives formatting | `scripts/validate_test_quality.py`: read actual comments in context, excluding strings and the body |
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
| #41 | PROJECT no longer refers to removed JSON business-rule copies; API context notes include the existing PATCH route | compare the Business Rules paragraph with `docs/project_context.json`; `api_overview.notes` includes the sample's PATCH endpoint |
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
