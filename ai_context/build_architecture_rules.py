from __future__ import annotations

import re
from pathlib import Path

from ai_context.constants import (
    ARCHITECTURE_ANTI_PATTERNS,
    CANONICAL_EXAMPLES,
    COLD_PATHS,
    EDIT_ZONES,
    QUERY_SHORTCUTS,
    QUERY_SUPPORTED_COMMANDS,
    READ_LAST_PATHS,
    REFERENCE_IMPLEMENTATION_PATHS,
    SCHEMA_VERSION,
    TEMPLATE_KERNEL_PATHS,
)
from ai_context.file_policy import build_file_policy_index
from scripts.validate_architecture import get_layer_rules


# ATTRIBUTE: _REPO_ROOT (Path)
# SUMMARY: Repository root used to read the two files that own the Python version numbers.
_REPO_ROOT = Path(__file__).resolve().parent.parent


# FUNCTION: _python_runtime_policy
# SUMMARY: Derive the Python version policy from pyproject.toml and .python-version.
# OUTPUT: (dict[str, str]): Minimum, local toolchain, mypy target and CI versions.
def _python_runtime_policy() -> dict[str, str]:
    # **LOGIC_STEP**: These five values used to be typed out here by hand, in the file that
    # ARCHITECTURE.md points to as a source of truth. Nothing checked them against
    # pyproject.toml, so a version bump would leave the generated rules quietly wrong while
    # every gate stayed green.
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^requires-python\s*=\s*">=([0-9.]+)"', pyproject, re.MULTILINE)
    if match is None:
        raise RuntimeError('pyproject.toml has no `requires-python = ">=X.Y"` entry to read.')
    minimum = match.group(1)
    toolchain = (_REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    return {
        "minimum_supported": minimum,
        "default_local": toolchain,
        "mypy_target": minimum,
        "ci_primary": toolchain,
        "ci_compatibility": minimum,
    }


def _build_enforcement_model(
    layer_rules: dict[str, dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    strictly_validated_rules: list[dict[str, object]] = []
    guidance_only_rules: list[dict[str, object]] = []
    for layer, rules in layer_rules.items():
        # **LOGIC_STEP**: A layer is strictly validated by whichever mechanism it declares. The
        # domain moved from a blacklist to an allowlist on 2026-08-12, and a reader of this payload
        # has to see which one — the two answer different questions. Emitting only the blacklist
        # entry would have dropped the domain out of `strictly_validated_rules` entirely, making
        # the strictest rule in the repository look like guidance.
        allowed_imports = list(rules["runtime_enforced"]["allowed_imports"])
        if allowed_imports:
            strictly_validated_rules.append(
                {
                    "layer": layer,
                    "rule_id": f"arch.{layer}.import_not_allowed",
                    "strength": rules["runtime_enforced"]["rule_strength"],
                    "validator": rules["runtime_enforced"]["validator"],
                    "scope": "allowed_import_prefixes_plus_stdlib",
                }
            )
        forbidden_imports = list(rules["runtime_enforced"]["forbidden_imports"])
        if forbidden_imports:
            strictly_validated_rules.append(
                {
                    "layer": layer,
                    "rule_id": f"arch.{layer}.no_forbidden_import",
                    "strength": rules["runtime_enforced"]["rule_strength"],
                    "validator": rules["runtime_enforced"]["validator"],
                    "scope": "forbidden_import_prefixes",
                }
            )
        guidance_only_rules.append(
            {
                "layer": layer,
                "rule_id": f"arch.{layer}.declared_allowed_dependencies",
                "strength": rules["not_enforced_in_validator"]["rule_strength"],
                "declared_allowed_dependencies": list(
                    rules["guidance_only"]["declared_allowed_dependencies"]
                ),
                "not_whitelist_gate": rules["not_enforced_in_validator"][
                    "declared_allowed_dependencies_are_not_whitelist_gate"
                ],
                "summary": rules["enforcement_summary"],
            }
        )
    return {
        "strictly_validated_rules": strictly_validated_rules,
        "guidance_only_rules": guidance_only_rules,
    }


def build_architecture_rules() -> dict[str, object]:
    layer_rules = {
        layer: {
            "runtime_enforced": {
                "validator": rules["runtime_enforced"]["validator"],
                "forbidden_imports": list(rules["runtime_enforced"]["forbidden_imports"]),
                "allowed_imports": list(rules["runtime_enforced"]["allowed_imports"]),
                "enforcement_notes": list(rules["runtime_enforced"]["enforcement_notes"]),
                "rule_strength": rules["runtime_enforced"]["rule_strength"],
            },
            "guidance_only": {
                "declared_allowed_dependencies": list(
                    rules["guidance_only"]["declared_allowed_dependencies"]
                ),
                "narrative_notes": list(rules["guidance_only"]["narrative_notes"]),
                "rule_strength": rules["guidance_only"]["rule_strength"],
            },
            "not_enforced_in_validator": {
                "declared_allowed_dependencies_are_not_whitelist_gate": rules[
                    "not_enforced_in_validator"
                ]["declared_allowed_dependencies_are_not_whitelist_gate"],
                "explanation": rules["not_enforced_in_validator"]["explanation"],
                "rule_strength": rules["not_enforced_in_validator"]["rule_strength"],
            },
            "enforcement_summary": rules["enforcement_summary"],
        }
        for layer, rules in get_layer_rules().items()
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "python_runtime_policy": _python_runtime_policy(),
        "llm_export_profile": "docs/llm_export_profile.json",
        "quality_gates": ["make quality-gates"],
        "layers": layer_rules,
        "enforcement_model": _build_enforcement_model(layer_rules),
        "anti_patterns": list(ARCHITECTURE_ANTI_PATTERNS),
        "canonical_examples": dict(CANONICAL_EXAMPLES),
        "cold_paths": list(COLD_PATHS),
        "read_last_paths": list(READ_LAST_PATHS),
        "source_of_truth": {
            "checks_and_validators": [
                "scripts/validate_architecture.py",
                "scripts/validate_endpoint_wiring.py",
                "scripts/validate_runtime_ownership.py",
                "make quality-gates",
            ],
            "machine_contract": "docs/architecture_rules.json",
            "operational_query_interface": "scripts/query_ai_context.py",
            "narrative_architecture": "CLAUDE.md",
            "generated_navigation": [
                "docs/ai_context_map.json",
                "docs/ai_change_map.json",
            ],
            "fallback_docs": [
                "docs/agent_rules.md",
                "docs/project_map.md",
                "docs/adr/README.md",
                "CLAUDE.md",
            ],
        },
        "query_cli": {
            "path": "scripts/query_ai_context.py",
            "recommended_first_step": "uv run python scripts/query_ai_context.py bootstrap",
            "shortcuts": list(QUERY_SHORTCUTS),
            "focused_examples": [
                f"uv run python scripts/query_ai_context.py {query}"
                for query in QUERY_SUPPORTED_COMMANDS
            ],
        },
        "template_kernel_paths": list(TEMPLATE_KERNEL_PATHS),
        "reference_implementation_paths": list(REFERENCE_IMPLEMENTATION_PATHS),
        "file_policy_index": build_file_policy_index(),
        "wiring_files": {
            "composition_root": "project/core/composition_root.py",
            "vertical_services": "project/core/service_registration.py",
            "typed_dependencies": "project/infrastructure/api/dependencies.py",
            "router_registration": "project/infrastructure/api/router_registration.py",
        },
        "edit_zones": EDIT_ZONES,
        "endpoint_wiring_contract": {
            "validator": "scripts/validate_endpoint_wiring.py",
            "checks": [
                "endpoint modules must not import application services directly",
                "endpoint parameters must use typed dependency aliases for service injection",
                "endpoint alias -> getter -> service_key chains must resolve through dependencies.py",
                "endpoint modules must not instantiate services directly",
            ],
        },
        "runtime_ownership_contract": {
            "validator": "scripts/validate_runtime_ownership.py",
            "checks": [
                "app.state.services writes must stay in the canonical runtime wiring path",
                "shared async resources must be created only in canonical wiring or lifecycle paths",
                "direct os.getenv and os.environ access must stay in explicit allowlist zones",
            ],
        },
        "cbm_policy": {
            "strict_core": ["file headers", "classes", "public functions", "__init__"],
            "optional_detail": ["attributes", "private helpers"],
            "logic_step_when_to_use": [
                "branching logic",
                "side effects",
                "security nuances",
                "lifecycle nuances",
                "wiring nuances",
                "non-obvious invariants",
            ],
        },
    }
