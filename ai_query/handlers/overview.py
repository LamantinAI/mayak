from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ai_query.common import context_bundle
from ai_query.models import QueryPayload

# ATTRIBUTE: _REPO_ROOT (Path)
# SUMMARY: Repository root used to read the hand-maintained project context.
_REPO_ROOT = Path(__file__).resolve().parents[2]

# ATTRIBUTE: _PROJECT_CONTEXT_PATH (Path)
# SUMMARY: Hand-maintained file declaring which external systems this project uses.
_PROJECT_CONTEXT_PATH = _REPO_ROOT / "docs" / "project_context.json"

# ATTRIBUTE: _RUNTIME_TOGGLES (dict[str, tuple[str, bool]])
# SUMMARY: Integration name mapped to the environment variable that switches it on, plus the
# default that applies when the variable is unset.
_RUNTIME_TOGGLES: dict[str, tuple[str, bool]] = {
    "postgres": ("POSTGRES_ENABLED", True),
}


# FUNCTION: _env_flag
# SUMMARY: Read a boolean environment variable, falling back to the project's .env file.
# INPUT: name (str): Variable name.
# INPUT: default (bool): Value used when the variable is absent everywhere.
# OUTPUT: (bool): Effective value.
def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        env_file = _REPO_ROOT / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{name}="):
                    raw = stripped.split("=", 1)[1].strip().strip("\"'")
                    break
    if raw is None:
        return default
    return raw.lower() not in {"0", "false", "no"}


# FUNCTION: integrations_overview
# SUMMARY: Report which external systems this project declares, and which are switched on now.
# OUTPUT: (dict[str, dict[str, Any]]): Integration name mapped to its declared and live state.
def integrations_overview() -> dict[str, dict[str, Any]]:
    # **LOGIC_STEP**: docs/project_context.json is the one place a project says which external
    # systems it uses. Reporting the declaration next to the live toggle here means an agent does
    # not have to open .env by hand to learn whether the database is even part of this project —
    # and the two cannot silently diverge.
    try:
        context = json.loads(_PROJECT_CONTEXT_PATH.read_text(encoding="utf-8"))
        declared = context.get("integrations", {})
    except (OSError, json.JSONDecodeError):
        declared = {}

    result: dict[str, dict[str, Any]] = {}
    for name, entry in sorted(declared.items()):
        record: dict[str, Any] = {
            "type": entry.get("type", "unknown"),
            "description": entry.get("description", ""),
        }
        toggle = _RUNTIME_TOGGLES.get(name)
        if toggle is not None:
            variable, default = toggle
            record["toggle"] = variable
            record["enabled"] = _env_flag(variable, default)
        result[name] = record
    return result


def query_overview() -> QueryPayload:
    context_map, change_map, architecture_rules = context_bundle()
    payload = {
        "schema_versions": {
            "context_map": context_map["schema_version"],
            "change_map": change_map["schema_version"],
            "architecture_rules": architecture_rules["schema_version"],
        },
        "core_entrypoints": context_map["core_entrypoints"],
        "generated_maps": context_map["generated_maps"],
        "integrations": integrations_overview(),
        "integrity": context_map["integrity"],
        "query_cli": context_map["query_cli"],
        "resolution_model": context_map["resolution_model"],
        "service_counts": {
            "shared": len(context_map["service_keys"]["shared"]),
            "vertical": len(context_map["service_keys"]["vertical"]),
            "total": len(context_map["service_registry"]),
        },
        "task_names": sorted(change_map["tasks"].keys()),
        "layers": architecture_rules["layers"],
        "enforcement_model": architecture_rules["enforcement_model"],
        "file_policy_coverage": sorted(architecture_rules["file_policy_index"].keys()),
        "wiring_files": architecture_rules["wiring_files"],
        "canonical_examples": dict(context_map["canonical_examples"]),
        "cold_paths": list(context_map["cold_paths"]),
        "read_last_paths": list(context_map["read_last_paths"]),
    }
    return QueryPayload(kind="overview", identifier=None, payload=payload)
