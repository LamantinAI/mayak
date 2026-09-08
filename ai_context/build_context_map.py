from __future__ import annotations

from ai_context.constants import (
    CANONICAL_EXAMPLES,
    COLD_PATHS,
    COMPOSITION_ROOT_PATH,
    DEPENDENCIES_PATH,
    QUERY_SHORTCUTS,
    QUERY_SUPPORTED_COMMANDS,
    READ_LAST_PATHS,
    REFERENCE_IMPLEMENTATION_PATHS,
    ROOT_DIR,
    ROUTER_REGISTRATION_PATH,
    SCHEMA_VERSION,
    SERVICE_REGISTRATION_PATH,
    TEMPLATE_KERNEL_PATHS,
)
from ai_context.extraction import (
    extract_dependency_registry,
    extract_env_prefixes,
    extract_route_inventory,
    extract_router_modules,
    extract_service_registry_entries,
)
from ai_context.file_policy import build_file_policy_index
from ai_context.integrity import build_integrity_report


def _quality_gates_by_concern() -> dict[str, list[str]]:
    return {
        "architecture": ["uv run python scripts/validate_architecture.py"],
        "autofix": ["make ai-autofix"],
        "cbm": ["uv run python scripts/validate_cbm.py"],
        "drift": [
            "uv run python scripts/generate_ai_context.py --check",
            "uv run python scripts/sync_agent_docs.py --check",
        ],
        "generated_refresh": [
            "make refresh-ai-context",
            "make refresh-agent-docs",
            "make refresh-project-map",
            "make refresh-generated-docs",
        ],
        "endpoint_wiring": ["uv run python scripts/validate_endpoint_wiring.py"],
        "runtime_ownership": ["uv run python scripts/validate_runtime_ownership.py"],
        "full": ["make quality-gates"],
        # **LOGIC_STEP**: lint and types name their make target instead of respelling the command.
        # A hand-copied command reaches an agent through docs/ai_context_map.json, so a stale one
        # is a command someone runs and believes — the entries below keep their direct spelling
        # instead: a script with no source list cannot drift.
        "lint": ["make gate-lint"],
        "migrations": ["uv run python scripts/validate_migrations.py"],
        "module_size": ["uv run python scripts/validate_module_sizes.py"],
        "tests": ["uv run python scripts/run_all_tests.py --skip-functional"],
        "types": ["make gate-types"],
    }


def _build_service_registry() -> dict[str, dict[str, object]]:
    service_registry = extract_service_registry_entries(
        COMPOSITION_ROOT_PATH,
        ROOT_DIR,
        "shared",
    )
    service_registry.update(
        extract_service_registry_entries(
            SERVICE_REGISTRATION_PATH,
            ROOT_DIR,
            "vertical",
        )
    )
    return dict(sorted(service_registry.items()))


def build_integrity_payload(
    service_registry: dict[str, dict[str, object]],
    dependency_registry: dict[str, dict[str, dict[str, object]]],
    router_modules: list[str],
    route_inventory: dict[str, dict[str, object]],
) -> dict[str, object]:
    return build_integrity_report(
        service_registry,
        dependency_registry,
        router_modules,
        route_inventory,
    )


def build_context_map() -> dict[str, object]:
    service_registry = _build_service_registry()
    dependency_registry = extract_dependency_registry(DEPENDENCIES_PATH)
    router_modules = extract_router_modules(ROUTER_REGISTRATION_PATH)
    route_inventory = extract_route_inventory(
        ROOT_DIR,
        ROUTER_REGISTRATION_PATH,
        dependency_registry,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "service_keys": {
            "shared": [
                service_key
                for service_key, metadata in service_registry.items()
                if metadata["category"] == "shared"
            ],
            "vertical": [
                service_key
                for service_key, metadata in service_registry.items()
                if metadata["category"] == "vertical"
            ],
        },
        "service_registry": service_registry,
        "dependency_registry": dependency_registry,
        "router_modules": router_modules,
        "route_inventory": route_inventory,
        "resolution_model": {
            "confidence_levels": ["high", "medium", "low"],
            "sources": ["declared", "ast_exact", "ast_heuristic", "inferred"],
            "guidance": (
                "Low-confidence or heuristic entries are navigation aids only and "
                "must be confirmed in the real wiring modules before editing."
            ),
        },
        "core_entrypoints": [
            "CLAUDE.md",
            "docs/architecture_rules.json",
            "scripts/query_ai_context.py",
            "project/core/composition_root.py",
            "project/core/service_registration.py",
            "project/infrastructure/api/router_registration.py",
            "project/infrastructure/api/dependencies.py",
        ],
        "template_kernel_paths": list(TEMPLATE_KERNEL_PATHS),
        "reference_implementation_paths": list(REFERENCE_IMPLEMENTATION_PATHS),
        "canonical_examples": dict(CANONICAL_EXAMPLES),
        "cold_paths": list(COLD_PATHS),
        "read_last_paths": list(READ_LAST_PATHS),
        "env_prefixes": extract_env_prefixes(ROOT_DIR),
        "integrity": build_integrity_payload(
            service_registry=service_registry,
            dependency_registry=dependency_registry,
            router_modules=router_modules,
            route_inventory=route_inventory,
        ),
        "quality_gates": ["make quality-gates"],
        "quality_gates_by_concern": _quality_gates_by_concern(),
        "file_policy_index": build_file_policy_index(),
        "generated_maps": [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        ],
        "query_cli": {
            "path": "scripts/query_ai_context.py",
            "shortcuts": list(QUERY_SHORTCUTS),
            "supported_queries": list(QUERY_SUPPORTED_COMMANDS),
        },
    }
