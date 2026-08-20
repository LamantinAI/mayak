# FILE: ai_query/handlers/bootstrap.py
# SUMMARY: Session cold-start payload: what to read first, in what order of authority, and where the wiring lives.

from __future__ import annotations

from typing import Any

from ai_query.common import context_bundle
from ai_query.models import QueryPayload

# ATTRIBUTE: _SOURCE_OF_TRUTH_KEYS (tuple[str, ...])
# SUMMARY: The precedence order of the source_of_truth sections in the generated contract.
_SOURCE_OF_TRUTH_KEYS = (
    "checks_and_validators",
    "machine_contract",
    "operational_query_interface",
    "narrative_architecture",
    "generated_navigation",
    "fallback_docs",
)


# FUNCTION: _source_of_truth_order
# SUMMARY: Flatten the generated source_of_truth mapping into one ordered, de-duplicated list.
def _source_of_truth_order(source_of_truth: dict[str, Any]) -> list[str]:
    ordered: list[str] = []
    for key in _SOURCE_OF_TRUTH_KEYS:
        value = source_of_truth.get(key)
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if isinstance(entry, str) and entry not in ordered:
                ordered.append(entry)
    return ordered


def query_bootstrap() -> QueryPayload:
    context_map, _change_map, architecture_rules = context_bundle()
    generated_do_not_edit = architecture_rules["edit_zones"]["generated_do_not_edit"]
    payload = {
        "read_first": [
            "CLAUDE.md",
            "docs/architecture_rules.json",
            "uv run python scripts/query_ai_context.py overview",
        ],
        # NOTE: Both lists below used to be typed out here as literals, alongside the same content
        # in architecture_rules.json and in ARCHITECTURE.md. Three copies, none compared. They are
        # now projections of the generated contract, which is the machine source of truth.
        "source_of_truth_order": _source_of_truth_order(architecture_rules["source_of_truth"]),
        "core_wiring_files": sorted(architecture_rules["wiring_files"].values()),
        "task_shortcuts": list(context_map["query_cli"]["shortcuts"]),
        "query_examples": list(context_map["query_cli"]["supported_queries"]),
        "edit_protocol": [
            "overview",
            "focused query",
            "confirm real wiring",
            "minimal checks",
            "make quality-gates",
        ],
        "forbidden_moves": [
            *architecture_rules["anti_patterns"],
            *[f"Do not edit generated file directly: {path}" for path in generated_do_not_edit],
        ],
        "generated_artifacts": list(context_map["generated_maps"]),
        "template_kernel_paths": list(architecture_rules["template_kernel_paths"]),
        "reference_implementation_paths": list(
            architecture_rules["reference_implementation_paths"]
        ),
        "canonical_examples": dict(context_map["canonical_examples"]),
        "cold_paths": list(context_map["cold_paths"]),
        "read_last_paths": list(context_map["read_last_paths"]),
    }
    return QueryPayload(kind="bootstrap", identifier=None, payload=payload)
