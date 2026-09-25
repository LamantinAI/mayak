# Project Map

<!-- PROJECT_MAP_START -->
This file is the generated reference tree for repository navigation.
Read `docs/agent_rules.md` for the operational contract and change workflow.

```
Project Map: Mayak
============================================================
├── .agents/
│   ├── hooks/
│   │   └── pre-edit-guard.sh
│   └── skills/
│       ├── add-vertical/
│       │   └── SKILL.md
│       └── initialize-project/
│           └── SKILL.md
├── .claude/
│   ├── skills/
│   │   ├── add-vertical
│   │   └── initialize-project
│   └── settings.json
├── .codex/
│   └── hooks.json
├── .githooks/
│   └── pre-commit
├── .github/
│   └── workflows/
│       └── ci.yml
├── ai_context/
│   ├── __init__.py  ---  Helpers for AI context extraction, builders, integrity validation, and artifact rendering.
│   ├── build_architecture_rules.py
│   ├── build_change_map.py
│   ├── build_context_map.py
│   ├── constants.py
│   ├── dynamic_imports.py  ---  Recognise dynamic import calls so the import-boundary validators are not blind to them.
│   ├── errors.py
│   ├── extraction.py  ---  AST-based extraction helpers for AI-facing repository context artifacts.
│   ├── file_policy.py
│   ├── integrity.py  ---  Integrity checks for generated AI context artifacts.
│   ├── line_metrics.py  ---  Classify every physical line of a Python module as code, comment, docstring, or blank, so size budgets can charge for logic instead of for documentation.
│   ├── rendering.py  ---  Stable rendering helpers for generated AI context artifacts.
│   └── validator_contract.py  ---  Canonical error-payload contract shared by every scripts/validate_*.py JSON converter, so `stop_widening_condition` (and the rest of the remediation guidance already present in each validator's internal rule playbook) reaches the actual `--json` output instead of being dropped by ad-hoc per-validator dict literals.
├── ai_query/
│   ├── handlers/
│   │   ├── __init__.py  ---  Command handlers for the Mayak AI query CLI.
│   │   ├── before_edit.py
│   │   ├── bootstrap.py  ---  Session cold-start payload: what to read first, in what order of authority, and where the wiring lives.
│   │   ├── failure.py
│   │   ├── overview.py
│   │   ├── symbol.py
│   │   └── workset.py
│   ├── __init__.py  ---  Query helpers for the Mayak AI context CLI.
│   ├── common.py
│   ├── models.py
│   ├── rendering.py
│   └── router.py
├── alembic/
│   ├── versions/
│   │   ├── 001_initial_reference_tasks_schema.py
│   │   ├── 7300d4656a8d_add_updated_at_to_reference_tasks.py
│   │   └── b5e2c1a9d4f0_one_open_reference_task_per_title.py
│   ├── env.py  ---  Alembic migration environment configuration for PostgreSQL schema management.
│   ├── README
│   └── script.py.mako
├── docs/
│   ├── adr/
│   │   ├── ADR-001-pragmatic-cbm.md
│   │   ├── ADR-002-python-version-policy.md
│   │   ├── ADR-003-mock-first-llm-mode.md
│   │   ├── ADR-004-module-size-limit.md
│   │   ├── ADR-005-agent-workflow-lifecycle.md
│   │   ├── ADR-006-optional-postgres.md
│   │   ├── ADR-007-autocommit-and-explicit-transactions.md
│   │   ├── ADR-008-readiness-criticality.md
│   │   ├── ADR-009-provider-errors-at-the-llm-boundary.md
│   │   ├── ADR-010-what-the-gates-do-not-see.md
│   │   ├── ADR-011-one-rendered-wrapper.md
│   │   └── README.md
│   ├── mutations/
│   │   └── reference_task.json
│   ├── agent_rules.md
│   ├── ai_change_map.json
│   ├── ai_context_map.json
│   ├── architecture_rules.json
│   ├── llm_export_profile.json
│   ├── project_context.json
│   ├── project_map.md
│   └── tracing.md
├── project/
│   ├── application/
│   │   ├── __init__.py  ---  Application layer containing orchestration services and DTOs.
│   │   ├── core_model.py  ---  Shared Pydantic base model for application DTOs with strict default validation settings.
│   │   ├── dtos.py  ---  Contains Pydantic models for API input/output data transfer objects.
│   │   ├── reference_task_dtos.py  ---  Wire contract for the reference vertical — types only; the bounds are the domain's checks.
│   │   └── reference_task_service.py  ---  Reference application service: create, read, list and a read-modify-write update, over a port.
│   ├── common/
│   │   ├── __init__.py  ---  Shared utilities and helpers used across all layers.
│   │   └── sampling.py  ---  Decide whether one liveness-probe request should be logged, so an orchestrator polling
│   ├── core/
│   │   ├── logging/
│   │   │   ├── __init__.py  ---  AI-optimized semantic logging system with structured NDJSON output, event classification and context propagation.
│   │   │   ├── config.py  ---  Logging configuration compatible with Uvicorn and standard logging dictConfig.
│   │   │   ├── context.py  ---  Context management for semantic logging using ContextVars.
│   │   │   ├── enums.py  ---  Semantic event type definitions for AI classification of log entries.
│   │   │   ├── file_manager.py  ---  File-based log management for local development: per-run log paths and retention rotation.
│   │   │   ├── formatters.py  ---  NDJSON formatter for structured logging with semantic event classification and LLM-friendly output.
│   │   │   ├── logger.py  ---  Core semantic logging adapter implementing caller resolution, context propagation, and span lifecycle management.
│   │   │   ├── logger_events.py  ---  Semantic logging facade mixin that composes the smaller event-helper families.
│   │   │   ├── logger_events_base.py  ---  Structural contract every semantic-logging mixin depends on: caller resolution and log_event.
│   │   │   ├── logger_events_errors.py  ---  Semantic logging mixin implementing error and critical failure helper methods.
│   │   │   ├── logger_events_operational.py  ---  Semantic logging mixin implementing external-call, user-input, system, metric, and warning helpers.
│   │   │   ├── logger_events_state.py  ---  Semantic logging mixin implementing state, data, decision, and validation helper methods.
│   │   │   ├── logger_types.py  ---  Shared structured payload aliases and span context types used across semantic logging modules.
│   │   │   ├── redaction.py  ---  Safe logging summaries for user-provided text payloads and request-like structures.
│   │   │   ├── trace_formatter.py  ---  NDJSON-to-text tree transformer for LLM-friendly trace visualization.
│   │   │   └── trace_tree.py  ---  Parses NDJSON trace events into the SpanNode/LeafEvent tree trace_formatter.py renders.
│   │   ├── __init__.py  ---  Core infrastructure containing configuration, dependency injection, and logging.
│   │   ├── composition_root.py  ---  Composition Root that creates and links all dependencies, creating adapters and injecting them into Core services for FastAPI application.
│   │   ├── config.py  ---  Stable facade that re-exports configuration builders, settings models, and runtime helpers.
│   │   ├── config_builders.py  ---  Shared DSN builder helpers reused by runtime settings and database tooling.
│   │   ├── config_runtime.py  ---  Runtime settings facade, validation logic, and override helpers built on top of smaller settings modules.
│   │   ├── config_settings_agent.py  ---  Settings for the agent runtime — LLM behaviour, prompt location, readiness strategy.
│   │   ├── config_settings_core.py  ---  Core Pydantic settings models for project, LLM, server, and PostgreSQL configuration.
│   │   ├── config_settings_observability.py  ---  The one switch that turns the kernel's terse logging into a full on-disk trace.
│   │   ├── error_utils.py  ---  Safe exception helpers for client-facing messages and sanitized error logging.
│   │   ├── lifecycle.py  ---  Lifecycle manager for async resources initialization and shutdown.
│   │   ├── serialization.py  ---  High-performance serialization utilities using orjson for structured data handling.
│   │   └── service_registration.py  ---  Vertical-specific service wiring helpers. CompositionRoot delegates per-vertical service construction here so that build_dependencies() stays focused on orchestration.
│   ├── domain/
│   │   ├── __init__.py  ---  Domain layer with pure business logic, exceptions, and port interfaces.
│   │   ├── exceptions.py  ---  Defines the hierarchy of custom exceptions for the entire application.
│   │   ├── ports.py  ---  Canonical example of a domain port (dependency inversion boundary). Verticals add their own ports alongside this one.
│   │   └── reference_task.py  ---  The reference task: its fields, its statuses, and the checks every writer meets, over HTTP or not.
│   ├── infrastructure/
│   │   ├── agents/
│   │   │   ├── __init__.py  ---  Infrastructure implementations for agents.
│   │   │   ├── llm_service.py  ---  Public LLM service facade composing live-provider, mock-mode, and readiness subsystems.
│   │   │   ├── llm_service_live.py  ---  Live-provider mixin for LLMService covering client initialization and retryable provider calls.
│   │   │   ├── llm_service_mock.py  ---  Deterministic mock-response mixin for LLMService used in local development and tests.
│   │   │   ├── llm_service_readiness.py  ---  Readiness mixin for LLMService covering provider probes and safe health responses.
│   │   │   └── prompt_llm_adapter.py  ---  Adapter implementing the domain's LLMPort on top of the langchain-flavoured LLMService.
│   │   ├── api/
│   │   │   ├── endpoints/
│   │   │   │   ├── __init__.py  ---  Package initialization for API endpoints.
│   │   │   │   ├── health.py  ---  Health check endpoint for monitoring application status.
│   │   │   │   └── reference_tasks.py  ---  Reference HTTP surface: each handler parses, delegates to the service, converts the result.
│   │   │   ├── __init__.py  ---  FastAPI infrastructure including endpoints, middleware, dependencies, and exception handlers.
│   │   │   ├── dependencies.py  ---  FastAPI dependency injection helpers for accessing application services from app.state.
│   │   │   ├── exception_handlers.py  ---  FastAPI exception handlers for translating domain exceptions to HTTP responses with semantic logging.
│   │   │   ├── middleware.py  ---  FastAPI middleware for AI-optimized semantic logging of HTTP requests.
│   │   │   └── router_registration.py  ---  Helpers that register API routers while keeping CompositionRoot focused on application orchestration.
│   │   ├── persistence/
│   │   │   ├── __init__.py  ---  Database persistence layer: SQLAlchemy metadata for migrations, psycopg repositories for runtime.
│   │   │   ├── orm_models.py  ---  Defines SQLAlchemy ORM models for database interaction.
│   │   │   └── reference_task_repository.py  ---  Reference psycopg repository showing how rows from the real driver become domain objects.
│   │   └── __init__.py  ---  Infrastructure layer with adapters for API, agents, persistence, and external clients.
│   ├── launcher/
│   │   ├── __init__.py  ---  Application entry point and FastAPI app factory.
│   │   └── main.py  ---  The main executable file for launching FastAPI application.
│   ├── prompts/
│   │   └── example_assistant_prompt.txt
│   └── __init__.py  ---  Root package for the application.
├── scripts/
│   ├── __init__.py
│   ├── check_product_from_template.py  ---  Build a throwaway project from this checkout the way a new service is made, and check it.
│   ├── create_env_file.py
│   ├── doctor_ai_context.py
│   ├── extract_reference_vertical.py  ---  Take the reference vertical out of a project made from the template — once, all or nothing.
│   ├── generate_ai_context.py
│   ├── query_ai_context.py
│   ├── run_all_tests.py
│   ├── run_mutations.py
│   ├── structure_builder.py  ---  A utility script to generate a project tree map with file summaries read from each file's header, using the git index (tracked + untracked-not-ignored files) so the map is hermetic across worktrees and checkouts.
│   ├── sync_agent_docs.py
│   ├── validate_architecture.py  ---  Repository utility that enforces the hexagonal architecture import boundaries for production modules.
│   ├── validate_cbm.py  ---  Check that every Python file under project/ opens with its header — `# FILE:` and `# SUMMARY:`.
│   ├── validate_dependencies.py
│   ├── validate_endpoint_wiring.py
│   ├── validate_file_policy.py
│   ├── validate_migrations.py
│   ├── validate_module_sizes.py
│   ├── validate_repository_metadata.py
│   ├── validate_runtime_ownership.py
│   ├── validate_secrets.py
│   └── validate_test_quality.py
├── tests/
│   ├── application/
│   │   ├── test_client_errors_are_not_service_errors.py  ---  A 4xx is the application working. This pins that it is recorded that way — at WARNING,
│   │   ├── test_config.py  ---  Unit tests for configuration management and settings validation.
│   │   ├── test_critical_event_trace_id.py  ---  Regression guard: the unhandled-exception record must carry the request's trace_id.
│   │   ├── test_db_tier_contract.py  ---  Verify the db tier fails loudly without its database, and stands aside only when the project declares none.
│   │   ├── test_domain_ports.py  ---  Smoke tests for canonical domain Protocol declarations to keep them in coverage.
│   │   ├── test_env_sample_matches_code.py  ---  Guard against .env.sample drifting from the defaults declared in the settings models.
│   │   ├── test_error_utils.py  ---  Unit tests for safe client-facing exception messages and sanitized exception summaries.
│   │   ├── test_exceptions.py  ---  Unit tests for the domain exception hierarchy.
│   │   ├── test_full_trace_is_announced.py  ---  Verify a service that records prompt and completion text says so while it boots, and
│   │   ├── test_functional_request_helpers.py  ---  Pin that the five request helpers in tests/functional/conftest.py read an empty 204
│   │   ├── test_health_endpoints.py  ---  Endpoint tests for liveness and readiness health contracts.
│   │   ├── test_import_graph_is_acyclic.py  ---  Guard the one defect the whole suite is structurally blind to — an import cycle inside
│   │   ├── test_launcher_shutdown.py  ---  Verify SIGTERM leaves the launcher's post-uvicorn shutdown work enough time to finish.
│   │   ├── test_launcher_workers.py  ---  Verify SERVER_WORKERS above one starts several worker processes, each able to build the application.
│   │   ├── test_lifecycle_and_log_rotation.py  ---  Cover the two kernel modules nothing exercised — the lifespan body and log rotation.
│   │   ├── test_llm_service.py  ---  Unit tests for mock-mode LLM service behavior and readiness semantics.
│   │   ├── test_llm_service_retry.py  ---  Tests for the retry policy and token accounting of live LLM calls.
│   │   ├── test_logging_api.py  ---  Unit tests for the semantic logging helper API surface.
│   │   ├── test_logging_redaction.py  ---  Unit tests for safe logging summary helpers.
│   │   ├── test_middleware.py  ---  Tests for the logging middleware — query-parameter masking and inbound request-id resolution.
│   │   ├── test_mock_agent_multi_tool_loop.py  ---  The sample ADR-003 promises and the kernel did not ship: a real agent loop over three
│   │   ├── test_optional_postgres.py  ---  Guards for POSTGRES_ENABLED — the kernel must assemble, report ready and accept its
│   │   ├── test_prompt_llm_adapter.py  ---  Tests that PromptLLMAdapter really satisfies the domain's LLMPort contract.
│   │   ├── test_reference_task_vertical.py  ---  The reference vertical's rules decided before anything is written, and its wiring — no database.
│   │   ├── test_request_summary_outcome.py  ---  Regression tests proving request.summary reports the real result of a request.
│   │   ├── test_sampling.py  ---  Cover the health-check log sampler, which had no test of its own before.
│   │   ├── test_scripted_llm.py  ---  The scripted model double itself, and the one thing it exists for — proving a vertical
│   │   ├── test_secret_leak_guards.py  ---  Regression guards for the two paths that leaked secrets to clients and to logs.
│   │   ├── test_serialization.py  ---  Unit tests for low-level structured serialization helpers used by semantic logging.
│   │   ├── test_tool_argument_schema.py  ---  Verify a language-model tool's parameters reach the provider's schema under their own names.
│   │   ├── test_trace_file_gate.py  ---  Verify the NDJSON trace file is switched on by its own setting and never by debug mode.
│   │   ├── test_trace_formatter_against_real_output.py  ---  The renderer must work on the event shape THIS application emits, and on the shape it
│   │   ├── test_trace_formatter_failure_visibility.py  ---  Regression guards proving a failed request is visible in the rendered trace tree.
│   │   ├── test_trace_formatter_span_semantics.py  ---  Regression guards for three trace_formatter.py changes made alongside logger.py and
│   │   └── test_trace_formatter_summary.py  ---  The trace renderer must tell the truth about logs written before the outcome field existed.
│   ├── db/
│   │   ├── __init__.py
│   │   ├── conftest.py  ---  The db tier: tests that run real SQL against a real PostgreSQL, inside `make test`.
│   │   ├── docker-compose.yml
│   │   ├── stack.py  ---  Start, locate and stop the db tier's PostgreSQL for this checkout.
│   │   ├── test_migrations_match_models.py  ---  Verify the migrations build a schema that matches the ORM metadata, inside `make test`.
│   │   ├── test_reference_task_repository.py  ---  The reference repository's SQL, run against a real PostgreSQL and judged by what it returns.
│   │   └── test_reference_tasks_api.py  ---  The reference vertical over HTTP, in process, on the real repository: status codes, filters, conflicts.
│   ├── functional/
│   │   ├── src/
│   │   │   ├── __init__.py
│   │   │   ├── test_health_api.py  ---  The kernel over HTTP in the built image: it serves, and it finds its migrated database ready.
│   │   │   ├── test_migration_lock.py  ---  Prove that two processes migrating the same fresh database at once both succeed.
│   │   │   └── test_reference_tasks_api.py  ---  Smoke test of the reference vertical in the built image: HTTP into the container, rows in its database.
│   │   ├── utils/
│   │   │   ├── __init__.py
│   │   │   ├── helpers.py  ---  Helper classes and settings for functional tests.
│   │   │   ├── wait_for_api.py  ---  Utility to wait for the API to become ready.
│   │   │   └── wait_for_postgres.py  ---  Utility to wait for PostgreSQL to become ready.
│   │   ├── .env.sample
│   │   ├── __init__.py
│   │   ├── conftest.py  ---  Pytest fixtures for functional E2E tests providing database connections, HTTP clients, and request helpers.
│   │   ├── docker-compose.yml
│   │   ├── pytest.ini
│   │   ├── requirements.txt
│   │   └── settings.py
│   ├── infrastructure/
│   │   ├── __init__.py  ---  Infrastructure layer test package.
│   │   └── test_persistence_models.py  ---  Every foreign key in the shipped metadata declares its deletion policy.
│   ├── integration/
│   │   ├── __init__.py  ---  Integration test package. Runtime integration coverage without Docker-heavy functional flows.
│   │   ├── test_composition_root_lifecycle.py  ---  Smoke test that the assembled FastAPI app responds to /health/ inside a real lifespan.
│   │   ├── test_exception_to_http_status.py  ---  End-to-end mapping of ProjectError subclasses to HTTP status codes via the FastAPI exception handler pipeline.
│   │   └── test_trace_propagation.py  ---  Verify the middleware extracts request id from inbound trace headers and echoes it.
│   ├── support/
│   │   ├── __init__.py  ---  Test doubles shared by the unit and integration suites.
│   │   └── scripted_llm.py  ---  A language model that answers from a script, so a vertical's own handling of a bad
│   ├── template/
│   │   ├── test_ai_query_router.py  ---  Unit tests for the ai_query router dispatcher.
│   │   ├── test_ai_query_zone_lookup.py  ---  Unit tests for zone_for_path FILE_POLICY-first resolution and EDIT_ZONES fallback.
│   │   ├── test_create_env_file.py  ---  Tests for the .env creation step — what it generates, what it copies, what it refuses to touch.
│   │   ├── test_doctor_ai_context.py  ---  Unit tests for the AI-context doctor entrypoint.
│   │   ├── test_extract_reference_vertical.py  ---  Taking the reference vertical out of a project: nothing in the template, everything once in
│   │   ├── test_gate_recipes.py  ---  Guard the Makefile recipes whose defect is what they do NOT do — a gate that measures
│   │   ├── test_generate_ai_context.py  ---  Unit tests for the AI context map generator script.
│   │   ├── test_optional_postgres_tooling.py  ---  The template's tools with POSTGRES_ENABLED=false: the migration gate's own rule, the query
│   │   ├── test_query_ai_context.py  ---  Unit tests for the AI context query CLI helpers.
│   │   ├── test_run_all_tests.py  ---  Unit tests for the canonical all-tests runner script used by AI agents and developers.
│   │   ├── test_run_mutations.py  ---  The mutation runner measures the vertical's tests, not the template's checks of its files.
│   │   ├── test_skill_texts_match_reality.py  ---  Guard the factual claims the skills make about this repository, so prose cannot drift
│   │   ├── test_structure_builder.py  ---  Unit tests for project-map generation targeting the dedicated reference document.
│   │   ├── test_sync_agent_docs.py  ---  Unit tests for the generated agent wrapper sync script.
│   │   ├── test_template_neutrality.py  ---  Guard every way a stale project name creeps back into a template that must stay neutral.
│   │   ├── test_validate_architecture.py  ---  Unit tests for the repository architecture boundary validator.
│   │   ├── test_validate_cbm.py  ---  The file-header check: what it refuses, what it lets through, and that it reaches project/.
│   │   ├── test_validate_dependencies.py  ---  Unit tests for the validator that ties runtime imports to declared dependencies.
│   │   ├── test_validate_endpoint_wiring.py  ---  Unit tests for the endpoint-facing wiring contract validator.
│   │   ├── test_validate_file_policy.py  ---  Unit tests for the FILE_POLICY_INDEX schema validator.
│   │   ├── test_validate_migrations.py  ---  Unit tests for the Alembic migration validation quality gate.
│   │   ├── test_validate_module_sizes.py  ---  Unit tests for the production module budget validator, including the guarantee that documentation is not charged against the budget.
│   │   ├── test_validate_repository_metadata.py  ---  Unit tests for the merged repository-metadata validator (skills/commands frontmatter,
│   │   ├── test_validate_runtime_ownership.py  ---  Unit tests for the runtime ownership validator.
│   │   ├── test_validate_secrets.py  ---  Tests for the credential scanner: what it must catch, and what it must not shout about.
│   │   ├── test_validate_test_quality.py  ---  Unit tests for the validator that rejects tests which cannot fail.
│   │   └── test_validator_error_contract.py  ---  Contract test proving every scripts/validate_*.py JSON converter — plus the generate_ai_context.py drift-issue producer — surfaces rule_id, suggested_fix, read_first, next_commands, and stop_widening_condition on every emitted issue. Measured: stop_widening_condition was 0/10 in actual CLI JSON output despite living in every validator's internal rule-playbook dict.
│   └── conftest.py  ---  Global test configuration and fixtures for the FastAPI backend test suite.
├── .dockerignore
├── .env.sample
├── .gitignore
├── .python-version
├── AGENTS.md
├── alembic.ini
├── CLAUDE.md
├── CODEOWNERS
├── dev_setup.sh
├── docker-compose.postgres.yml
├── docker-compose.yml
├── Dockerfile
├── entrypoint.sh
├── LICENSE
├── Makefile
├── PROJECT.md
├── pyproject.toml
├── pytest.ini
├── README.md
└── uv.lock
```
<!-- PROJECT_MAP_END -->
