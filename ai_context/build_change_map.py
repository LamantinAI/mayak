from __future__ import annotations

from ai_context.constants import SCHEMA_VERSION


def _representative_source_file(path_pattern: str) -> str | None:
    mapping = {
        "project/application/": "project/application/dtos.py",
        "project/infrastructure/api/endpoints/": ("project/infrastructure/api/endpoints/health.py"),
        "project/domain/": "project/domain/exceptions.py",
        "project/infrastructure/": "project/infrastructure/api/dependencies.py",
        "project/core/config*.py": "project/core/config.py",
        "project/infrastructure/persistence/": ("project/infrastructure/persistence/orm_models.py"),
        "alembic/versions/": "alembic/versions/",
        "project/core/logging/": "project/core/logging/logger.py",
        "project/": "project/core/composition_root.py",
        "README.md": "README.md",
        "docs/architecture_rules.json": "docs/architecture_rules.json",
        "project/core/composition_root.py": "project/core/composition_root.py",
        "project/core/service_registration.py": "project/core/service_registration.py",
        "project/infrastructure/api/dependencies.py": (
            "project/infrastructure/api/dependencies.py"
        ),
        "project/infrastructure/api/router_registration.py": (
            "project/infrastructure/api/router_registration.py"
        ),
    }
    return mapping.get(path_pattern)


def _build_minimal_context(task_name: str, task_metadata: dict[str, object]) -> dict[str, object]:
    overrides = {
        "add_endpoint": {
            "focus_queries": [
                "before-edit file project/infrastructure/api/dependencies.py",
                "failure rule endpoint.no_depends_without_alias",
            ],
            "source_files": [
                "project/application/dtos.py",
                "project/infrastructure/api/endpoints/health.py",
                "project/infrastructure/api/dependencies.py",
                "project/infrastructure/api/router_registration.py",
            ],
        },
        "replace_dependency": {
            "focus_queries": [
                "before-edit file project/core/composition_root.py",
                "failure rule endpoint.alias_chain_invalid",
            ],
            "source_files": [
                "project/core/composition_root.py",
                "project/core/service_registration.py",
                "project/infrastructure/api/dependencies.py",
                "project/infrastructure/api/router_registration.py",
            ],
        },
    }
    override = overrides.get(task_name, {})
    source_files = list(override.get("source_files", []))
    if not source_files:
        for path_pattern in task_metadata.get("files", []):
            representative = _representative_source_file(path_pattern)
            if representative is None or representative in source_files:
                continue
            source_files.append(representative)
            if len(source_files) == 4:
                break

    focus_queries = list(
        override.get(
            "focus_queries",
            [
                "workset diff",
            ],
        )
    )
    return {
        "read_first": [
            "CLAUDE.md",
            "uv run python scripts/query_ai_context.py overview",
        ],
        "focus_queries": focus_queries,
        "source_files": source_files[:4],
    }


def build_change_map() -> dict[str, object]:
    tasks = {
        "add_shared_service": {
            "files": [
                "project/core/composition_root.py",
                "project/infrastructure/api/dependencies.py",
                "project/infrastructure/api/router_registration.py",
            ],
            "expected_artifacts": [
                "typed dependency getter",
                "app.state.services registration",
            ],
            "minimal_checks": [
                "uv run python scripts/validate_architecture.py",
                "uv run python scripts/validate_endpoint_wiring.py",
                "uv run python scripts/validate_runtime_ownership.py",
                "uv run python scripts/generate_ai_context.py --check",
            ],
            "tests_to_run": [
                "tests/application/test_generate_ai_context.py",
            ],
            "common_mistakes": [
                "adding a new shared service without aligning typed dependency getters",
                "changing service wiring without refreshing generated AI context artifacts",
            ],
            "forbidden_shortcuts": [
                "Do not instantiate shared services directly inside endpoints.",
            ],
            "verify": ["make quality-gates"],
        },
        "add_endpoint": {
            "files": [
                "project/application/",
                "project/infrastructure/api/endpoints/",
                "project/infrastructure/api/dependencies.py",
                "tests/application/",
            ],
            "expected_artifacts": [
                "DTO updates",
                "application orchestration",
                "typed dependency usage",
                "endpoint tests",
            ],
            "minimal_checks": [
                "uv run python scripts/validate_endpoint_wiring.py",
                "uv run python scripts/validate_runtime_ownership.py",
                "uv run python scripts/run_all_tests.py --skip-functional",
            ],
            "tests_to_run": ["tests/application/test_<feature>_endpoints.py"],
            "common_mistakes": [
                "service instantiated directly in endpoint",
                "Depends used without typed alias",
                "endpoint importing application service directly",
            ],
            "forbidden_shortcuts": [
                "Do not bypass project/infrastructure/api/dependencies.py aliases.",
            ],
            "verify": ["make quality-gates"],
        },
        "modify_existing_service": {
            "files": [
                "project/application/",
                "project/domain/",
                "tests/application/",
            ],
            "expected_artifacts": [
                "service behavior update",
                "preserved dependency wiring",
                "updated unit tests",
            ],
            "minimal_checks": [
                "uv run python scripts/validate_architecture.py",
                "uv run python scripts/validate_runtime_ownership.py",
                "uv run python scripts/run_all_tests.py --skip-functional",
            ],
            "tests_to_run": ["tests/application/test_<service>.py"],
            "verify": ["make quality-gates"],
        },
        "replace_dependency": {
            "files": [
                "project/core/composition_root.py",
                "project/core/service_registration.py",
                "project/infrastructure/api/dependencies.py",
            ],
            "expected_artifacts": [
                "updated dependency construction",
                "typed dependency getter or alias alignment",
                "runtime wiring verification",
            ],
            "minimal_checks": [
                "uv run python scripts/validate_architecture.py",
                "uv run python scripts/validate_endpoint_wiring.py",
                "uv run python scripts/validate_runtime_ownership.py",
                "uv run python scripts/generate_ai_context.py --check",
            ],
            "tests_to_run": [
                "tests/application/test_generate_ai_context.py",
            ],
            "common_mistakes": [
                "changing composition-root wiring without matching dependency alias updates",
                "breaking generated context integrity while services still build locally",
            ],
            "verify": ["make quality-gates"],
        },
        "add_config_setting": {
            "files": [
                "project/core/config*.py",
                "tests/application/test_config.py",
                "README.md",
            ],
            "expected_artifacts": [
                "typed settings field",
                "documented env behavior",
                "configuration tests",
            ],
            "minimal_checks": [
                "uv run python scripts/validate_runtime_ownership.py",
                "uv run python scripts/run_all_tests.py --skip-functional",
            ],
            "tests_to_run": ["tests/application/test_config.py"],
            "verify": ["make quality-gates"],
        },
        "add_persistence_model": {
            "files": [
                "project/domain/",
                "project/infrastructure/persistence/",
                "tests/application/",
            ],
            "expected_artifacts": [
                "domain contract update",
                "persistence adapter update",
                "repository or service tests",
            ],
            "minimal_checks": [
                "uv run python scripts/validate_architecture.py",
                "uv run python scripts/validate_runtime_ownership.py",
                "uv run python scripts/run_all_tests.py --skip-functional",
            ],
            "tests_to_run": [
                "tests/application/test_validate_migrations.py",
            ],
            "verify": ["make quality-gates"],
        },
        "add_migration": {
            "entrypoint": 'make autogenerate-migration MSG="description"',
            "files": [
                "alembic/versions/",
                "project/infrastructure/persistence/",
            ],
            "expected_artifacts": [
                "new Alembic revision",
                "schema-aware adapter update",
                "migration validation coverage",
            ],
            "steps": [
                'make autogenerate-migration MSG="description"',
                "update schema-aware persistence code if needed",
                "add or update migration-focused tests",
                "make quality-gates",
            ],
            "minimal_checks": ["uv run python scripts/validate_migrations.py"],
            "tests_to_run": ["tests/application/test_validate_migrations.py"],
            "verify": ["make quality-gates"],
        },
        "change_api_contract": {
            "files": [
                "project/application/",
                "project/infrastructure/api/endpoints/",
                "tests/application/",
            ],
            "expected_artifacts": [
                "DTO update",
                "endpoint behavior update",
                "API contract tests",
            ],
            "minimal_checks": [
                "uv run python scripts/validate_endpoint_wiring.py",
                "uv run python scripts/validate_runtime_ownership.py",
                "uv run python scripts/run_all_tests.py --skip-functional",
            ],
            "tests_to_run": [
                "tests/application/test_<feature>_endpoints.py",
                "tests/application/test_<feature>_dtos.py",
            ],
            "verify": ["make quality-gates"],
        },
        "refactor_large_module": {
            "files": [
                "project/",
                "tests/application/",
                "docs/architecture_rules.json",
            ],
            "expected_artifacts": [
                "smaller module boundaries",
                "preserved public imports",
                "module-size compliant tests",
            ],
            "minimal_checks": [
                "uv run python scripts/validate_module_sizes.py",
                "uv run python scripts/validate_architecture.py",
                "uv run python scripts/validate_runtime_ownership.py",
            ],
            "verify": ["make quality-gates"],
        },
        "change_logging_behavior": {
            "files": [
                "project/core/logging/",
                "project/application/",
                "tests/application/test_logging_*.py",
            ],
            "expected_artifacts": [
                "sanitized logging update",
                "span output preserved",
                "logging tests updated",
            ],
            "minimal_checks": [
                "uv run python scripts/validate_runtime_ownership.py",
                "uv run python scripts/run_all_tests.py --skip-functional",
            ],
            "tests_to_run": [
                "tests/application/test_logging_api.py",
                "tests/application/test_logging_redaction.py",
            ],
            "verify": ["make quality-gates"],
        },
        "introduce_new_external_client": {
            "files": [
                "project/domain/ports.py",
                "project/infrastructure/",
                "project/core/composition_root.py",
            ],
            "expected_artifacts": [
                "new port or adapter contract",
                "composition-root wiring",
                "integration-safe tests",
            ],
            "minimal_checks": [
                "uv run python scripts/validate_architecture.py",
                "uv run python scripts/validate_runtime_ownership.py",
                "uv run python scripts/run_all_tests.py --skip-functional",
            ],
            "verify": ["make quality-gates"],
        },
        "update_project_context": {
            "files": [
                "PROJECT.md",
                "docs/project_context.json",
            ],
            "expected_artifacts": [
                "updated domain context",
                "schema-valid project_context.json",
            ],
            "minimal_checks": [
                "uv run python scripts/validate_project_context.py",
            ],
            "tests_to_run": [
                "tests/application/test_validate_project_context.py",
            ],
            "verify": ["make quality-gates"],
        },
        "debug_runtime": {
            "files": [
                "docs/ai_context_map.json",
                "project/core/composition_root.py",
                "project/infrastructure/api/",
            ],
            "expected_artifacts": [
                "reproduced failure path",
                "validated fix",
                "relevant log inspection",
            ],
            "minimal_checks": [
                "uv run python scripts/validate_runtime_ownership.py",
                "uv run python scripts/run_all_tests.py --skip-functional",
                "uv run python scripts/generate_ai_context.py --check",
            ],
            "tests_to_run": [
                "tests/application/test_generate_ai_context.py",
            ],
            "common_mistakes": [
                "debugging broad runtime behavior before checking generated wiring context",
                "skipping semantic log inspection after a reproducible failure",
            ],
            "verify": ["make quality-gates"],
            "steps": [
                "uv run python scripts/run_all_tests.py --skip-functional",
                "inspect semantic logs",
                "read docs/ai_context_map.json",
                "make quality-gates",
            ],
        },
    }
    for task_name, task_metadata in tasks.items():
        task_metadata["minimal_context"] = _build_minimal_context(task_name, task_metadata)
    return {
        "schema_version": SCHEMA_VERSION,
        "golden_path": "make quality-gates",
        "tasks": tasks,
    }
