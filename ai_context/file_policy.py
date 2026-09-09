from __future__ import annotations

from ai_context.constants import ZONE_RISK


FILE_POLICY_INDEX: dict[str, dict[str, object]] = {
    "project/core/composition_root.py": {
        "role": "Shared composition root for service construction and FastAPI app assembly.",
        "why_it_exists": (
            "Keeps shared runtime wiring in one canonical place so services are created once "
            "and exposed through app.state.services."
        ),
        "layer": "core",
        "common_tasks": [
            "add_shared_service",
            "replace_dependency",
            "debug_runtime",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Composition-root wiring is a reusable template surface and the canonical entrypoint "
            "for shared dependency assembly."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "expert",
        "regenerate_if_changed": ["make refresh-ai-context"],
        "validators_if_changed": [
            "uv run python scripts/validate_architecture.py",
            "uv run python scripts/validate_endpoint_wiring.py",
            "uv run python scripts/validate_runtime_ownership.py",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "generated_artifacts": [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        ],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "drift.integrity.error",
            "runtime_ownership.app_state_services_write_restricted",
            "runtime_ownership.shared_resource_creation_restricted",
        ],
    },
    "project/core/service_registration.py": {
        "role": "Vertical-service wiring registry layered on top of the shared composition root.",
        "why_it_exists": (
            "Keeps feature-specific service construction out of the shared composition root "
            "while preserving one canonical registration flow."
        ),
        "layer": "core",
        "common_tasks": [
            "replace_dependency",
            "add_shared_service",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Vertical registration is reusable template infrastructure that every derived "
            "Mayak project follows."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "caution",
        "regenerate_if_changed": ["make refresh-ai-context"],
        "validators_if_changed": [
            "uv run python scripts/validate_architecture.py",
            "uv run python scripts/validate_endpoint_wiring.py",
            "uv run python scripts/validate_runtime_ownership.py",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "generated_artifacts": [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        ],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "drift.integrity.error",
            "endpoint.alias_chain_invalid",
        ],
    },
    "project/infrastructure/api/router_registration.py": {
        "role": "Canonical router inclusion surface for FastAPI endpoint modules.",
        "why_it_exists": (
            "Centralizes router ownership so new routes are registered predictably and can be "
            "discovered by validators and AI context generation."
        ),
        "layer": "infrastructure",
        "common_tasks": [
            "add_endpoint",
            "change_api_contract",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Router registration is part of the reusable wiring kernel rather than sample "
            "business behavior."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "caution",
        "regenerate_if_changed": ["make refresh-ai-context"],
        "validators_if_changed": [
            "uv run python scripts/validate_endpoint_wiring.py",
            "uv run python scripts/validate_runtime_ownership.py",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "generated_artifacts": [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        ],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "drift.integrity.error",
            "endpoint.alias_chain_invalid",
        ],
    },
    "project/infrastructure/api/dependencies.py": {
        "role": "Typed FastAPI dependency alias registry for endpoint-facing service access.",
        "why_it_exists": (
            "Provides the single canonical alias -> getter -> service_key surface that endpoints "
            "must use instead of importing or constructing services directly."
        ),
        "layer": "infrastructure",
        "common_tasks": [
            "add_endpoint",
            "replace_dependency",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Endpoint dependency aliases are reusable wiring infrastructure and are enforced by "
            "runtime validators."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "caution",
        "regenerate_if_changed": ["make refresh-ai-context"],
        "validators_if_changed": [
            "uv run python scripts/validate_endpoint_wiring.py",
            "uv run python scripts/validate_runtime_ownership.py",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "generated_artifacts": [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        ],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "endpoint.no_depends_without_alias",
            "endpoint.alias_chain_invalid",
            "drift.integrity.error",
        ],
    },
    "project/infrastructure/api/endpoints/health.py": {
        "role": "Sample readiness/liveness endpoint and canonical thin-endpoint reference.",
        "why_it_exists": (
            "Demonstrates the readiness contract (services + LLM + db connectivity) "
            "and ships as the kernel's canonical reference for the "
            "add_endpoint task."
        ),
        "layer": "infrastructure",
        "common_tasks": [
            "add_endpoint",
            "change_api_contract",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Canonical thin-endpoint reference shipped in the kernel; consumed by the "
            "add_endpoint task and surfaced via CANONICAL_EXAMPLES.thin_endpoint."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "safe",
        "regenerate_if_changed": ["make refresh-ai-context"],
        "validators_if_changed": [
            "uv run python scripts/validate_endpoint_wiring.py",
            "uv run python scripts/validate_runtime_ownership.py",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "generated_artifacts": [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        ],
        "do_not_edit_directly": False,
        "recommended_diff_style": "content-local-diff",
        "likely_failure_rules": [
            "endpoint.no_depends_without_alias",
            "endpoint.alias_chain_invalid",
            "drift.integrity.error",
        ],
    },
    "project/core/lifecycle.py": {
        "role": "FastAPI lifespan controller that opens shared async resources at startup and closes them at shutdown.",
        "why_it_exists": (
            "Centralizes ordering of pool and LLM startup and teardown so heavy resources "
            "are created once and released cleanly when the application stops."
        ),
        "layer": "core",
        "common_tasks": [
            "add_shared_service",
            "debug_runtime",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Lifecycle wiring is reusable kernel infrastructure — verticals consume it without "
            "modifying it."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "expert",
        "regenerate_if_changed": ["make refresh-ai-context"],
        "validators_if_changed": [
            "uv run python scripts/validate_runtime_ownership.py",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "generated_artifacts": [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        ],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "runtime_ownership.shared_resource_creation_restricted",
            "drift.integrity.error",
        ],
    },
    "project/infrastructure/api/exception_handlers.py": {
        "role": "Centralized FastAPI exception handlers mapping domain errors to typed HTTP responses.",
        "why_it_exists": (
            "Keeps error→status mapping in one place so endpoints stay thin and the failure "
            "surface stays uniform across verticals."
        ),
        "layer": "infrastructure",
        "common_tasks": [
            "modify_existing_service",
            "add_endpoint",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Shared exception mapping is reusable kernel infrastructure consumed by every endpoint."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "caution",
        "regenerate_if_changed": ["make refresh-ai-context"],
        "validators_if_changed": [
            "uv run python scripts/validate_architecture.py",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "generated_artifacts": [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        ],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "drift.integrity.error",
        ],
    },
    "project/infrastructure/api/middleware.py": {
        "role": "AI-optimized HTTP middleware that opens semantic-logging spans and propagates trace context.",
        "why_it_exists": (
            "Hooks every request into the semantic logger so spans, request IDs, and "
            "business-context headers are captured uniformly across verticals."
        ),
        "layer": "infrastructure",
        "common_tasks": [
            "debug_runtime",
            "modify_existing_service",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Shared HTTP middleware is reusable kernel infrastructure that runs for every "
            "request — wiring touches affect every endpoint."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "caution",
        "regenerate_if_changed": ["make refresh-ai-context"],
        "validators_if_changed": [
            "uv run python scripts/validate_architecture.py",
            "uv run python scripts/validate_runtime_ownership.py",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "generated_artifacts": [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        ],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "drift.integrity.error",
        ],
    },
    "project/infrastructure/agents/llm_service.py": {
        "role": "LLM service facade composing live-provider, mock-mode, and readiness mixins.",
        "why_it_exists": (
            "Provides one canonical entry point for LLM calls so verticals depend on a stable "
            "facade and the live/mock split stays consistent."
        ),
        "layer": "infrastructure",
        "common_tasks": [
            "introduce_new_external_client",
            "modify_existing_service",
            "debug_runtime",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Shared LLM facade is reusable kernel infrastructure consumed by verticals through "
            "the composition root."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "caution",
        "regenerate_if_changed": ["make refresh-ai-context"],
        "validators_if_changed": [
            "uv run python scripts/validate_architecture.py",
            "uv run python scripts/validate_runtime_ownership.py",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "generated_artifacts": [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        ],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "runtime_ownership.shared_resource_creation_restricted",
            "drift.integrity.error",
        ],
    },
    "scripts/query_ai_context.py": {
        "role": "Repo operating API for AI agents and narrow navigation workflows.",
        "why_it_exists": (
            "Exposes stable query commands so agents can inspect wiring, policy, failure "
            "remediation, and minimal context without broad repository scans."
        ),
        "layer": "tooling",
        "common_tasks": [
            "debug_runtime",
            "refactor_large_module",
            "replace_dependency",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "The query CLI defines the reusable operating interface for the repository itself."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "expert",
        "regenerate_if_changed": ["make refresh-ai-context"],
        "validators_if_changed": [
            "uv run pytest tests/application/test_query_ai_context.py -q",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "generated_artifacts": [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        ],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "drift.generated.outdated",
            "drift.agent_docs.outdated",
        ],
    },
    "scripts/generate_ai_context.py": {
        "role": "Generator for machine-readable repository context and architecture contracts.",
        "why_it_exists": (
            "Builds the JSON artifacts that back onboarding, query routing, and drift checks "
            "without introducing a separate documentation stack."
        ),
        "layer": "tooling",
        "common_tasks": [
            "debug_runtime",
            "replace_dependency",
            "refactor_large_module",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "This generator defines the reusable machine contract consumed by validators and "
            "query tooling."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "expert",
        "regenerate_if_changed": ["make refresh-ai-context"],
        "validators_if_changed": [
            "uv run pytest tests/application/test_generate_ai_context.py -q",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "generated_artifacts": [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        ],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "drift.integrity.error",
            "drift.generated.outdated",
        ],
    },
    "scripts/doctor_ai_context.py": {
        "role": "Diagnostic backbone that pinpoints the first blocking layer when quality-gates fails.",
        "why_it_exists": (
            "Provides actionable error narratives instead of raw validator output, "
            "extending coverage to all gates run by quality-gates and routing agents to the "
            "smallest fix recipe via failure_playbook."
        ),
        "layer": "tooling",
        # **LOGIC_STEP**: Empty on purpose. This field is matched by name against the task keys in
        # docs/ai_change_map.json, and no real key covers the validator layer today. An invented
        # name reads as guidance and delivers nothing — an empty list is honest.
        "common_tasks": [],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Doctor wires together every validator and is invoked by quality-gates on failure. "
            "Edits ripple into the entire failure-recovery loop."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "expert",
        "regenerate_if_changed": ["make refresh-ai-context"],
        "validators_if_changed": [
            "uv run pytest tests/application/test_doctor_ai_context.py",
            "uv run python scripts/doctor_ai_context.py",
            "make quality-gates",
        ],
        "generated_artifacts": [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        ],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "drift.integrity.error",
        ],
    },
    "scripts/validate_architecture.py": {
        "role": "Runtime validator for forbidden layer imports and architecture rule enforcement.",
        "why_it_exists": (
            "Turns the most important layer boundaries into executable checks rather than "
            "leaving them as narrative guidance only."
        ),
        "layer": "tooling",
        "common_tasks": [
            "refactor_large_module",
            "modify_existing_service",
            "introduce_new_external_client",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Architecture validation is part of the reusable enforcement kernel for derived repos."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "expert",
        "regenerate_if_changed": [],
        "validators_if_changed": [
            "uv run pytest tests/application/test_validate_architecture.py -q",
            "uv run python scripts/validate_architecture.py",
        ],
        "generated_artifacts": [],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "arch.application.no_forbidden_import",
        ],
    },
    "scripts/validate_endpoint_wiring.py": {
        "role": "Runtime validator for endpoint dependency-alias and wiring contracts.",
        "why_it_exists": (
            "Guards the thin-endpoint pattern so routes stay on typed aliases instead of "
            "importing or constructing services directly."
        ),
        "layer": "tooling",
        "common_tasks": [
            "add_endpoint",
            "change_api_contract",
            "replace_dependency",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Endpoint wiring validation is reusable enforcement infrastructure, not sample code."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "expert",
        "regenerate_if_changed": [],
        "validators_if_changed": [
            "uv run pytest tests/application/test_validate_endpoint_wiring.py -q",
            "uv run python scripts/validate_endpoint_wiring.py",
        ],
        "generated_artifacts": [],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "endpoint.no_depends_without_alias",
            "endpoint.alias_chain_invalid",
        ],
    },
    "scripts/validate_runtime_ownership.py": {
        "role": "Runtime validator for shared-resource, environment access, and app.state ownership.",
        "why_it_exists": (
            "Turns resource ownership rules into executable checks so LLM edits cannot drift "
            "shared runtime wiring into endpoints or business logic."
        ),
        "layer": "tooling",
        "common_tasks": [
            "add_endpoint",
            "replace_dependency",
            "add_config_setting",
            "debug_runtime",
        ],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Runtime ownership validation is reusable enforcement infrastructure for the template."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "expert",
        "regenerate_if_changed": [],
        "validators_if_changed": [
            "uv run pytest tests/application/test_validate_runtime_ownership.py -q",
            "uv run python scripts/validate_runtime_ownership.py",
        ],
        "generated_artifacts": [],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "runtime_ownership.env_access_restricted",
            "runtime_ownership.shared_resource_creation_restricted",
        ],
    },
    "pyproject.toml": {
        "role": "Project-wide Python packaging, dependency, and tool configuration.",
        "why_it_exists": (
            "Single source for runtime/dev dependencies, build backend, and tooling configs "
            "(ruff, mypy, coverage, pytest). Drives uv.lock and CI reproducibility."
        ),
        "layer": "tooling",
        # **LOGIC_STEP**: Empty for the same reason as scripts/doctor_ai_context.py above — the
        # four names here (`add_dependency`, `update_dependency`, `tune_lint_or_type_config`,
        # `tune_coverage_config`) match no key in docs/ai_change_map.json, and `replace_dependency`,
        # the one that sounds close, is about swapping an adapter in the composition root, not a
        # package. Verify with:
        #   uv run python scripts/query_ai_context.py before-edit file pyproject.toml
        "common_tasks": [],
        "kernel_or_reference": "template_kernel",
        "classification_reason": (
            "Defines reproducible build/dev environment for every Mayak project. "
            "Changes ripple to lockfile, mypy gate, ruff gate, and coverage tooling."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": "expert",
        "regenerate_if_changed": ["make update-deps"],
        "validators_if_changed": [
            "uv lock --check",
            "uv run python scripts/run_all_tests.py --skip-functional",
        ],
        "generated_artifacts": ["uv.lock"],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [
            "drift.integrity.error",
        ],
    },
}


def build_file_policy_index() -> dict[str, dict[str, object]]:
    return {
        path: {
            **metadata,
            "risk": ZONE_RISK[metadata["edit_zone"]],
            "regenerate_if_changed": list(metadata["regenerate_if_changed"]),
            "validators_if_changed": list(metadata["validators_if_changed"]),
            "generated_artifacts": list(metadata["generated_artifacts"]),
            "common_tasks": list(metadata["common_tasks"]),
            "likely_failure_rules": list(metadata.get("likely_failure_rules", [])),
        }
        for path, metadata in FILE_POLICY_INDEX.items()
    }
