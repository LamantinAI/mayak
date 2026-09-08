from __future__ import annotations

import ast
import fnmatch
import subprocess
from collections.abc import Iterable, Iterator
from pathlib import Path

from ai_context.constants import (
    QUERY_SUPPORTED_COMMANDS,
    ROOT_DIR,
    ZONE_RISK,
)
from scripts.generate_ai_context import (
    _build_generated_outputs,
    build_architecture_rules,
    build_change_map,
    build_context_map,
    generated_output_issues,
)

# **LOGIC_STEP**: The ten playbook getters this module used to import here are imported inside
# failure_playbook() instead, next to the three that already were. Each is used in exactly one
# place — the resolution chain — and importing them at module level made every consumer of
# ai_query.common, the doctor included, die during import when any one validator was missing or
# broken. Measured on 2026-08-13: deleting scripts/validate_cbm.py turned `make doctor` into a
# ModuleNotFoundError traceback, at the one moment a diagnostic tool has a job to do.


_DRIFT_RULE_PLAYBOOKS = {
    "drift.agent_docs.missing": {
        "meaning": "A generated agent wrapper file is missing from the checked-in repository state.",
        "smallest_files_to_read": [
            "docs/agent_rules.md",
            "CLAUDE.md",
        ],
        "smallest_command_to_rerun": "uv run python scripts/sync_agent_docs.py --check",
        "likely_fix_shape": (
            "CLAUDE.md is output, not a source. Edit docs/agent_rules.md and let "
            "sync_agent_docs.py write the wrapper."
        ),
        "next_checks": [
            "uv run python scripts/sync_agent_docs.py",
            "uv run python scripts/sync_agent_docs.py --check",
        ],
        "stop_widening_condition": (
            "Stop widening once the missing wrapper files are regenerated and sync_agent_docs.py "
            "--check passes."
        ),
    },
    "drift.agent_docs.outdated": {
        "meaning": "A generated agent wrapper file no longer matches docs/agent_rules.md.",
        "smallest_files_to_read": [
            "docs/agent_rules.md",
            "CLAUDE.md",
        ],
        "smallest_command_to_rerun": "uv run python scripts/sync_agent_docs.py --check",
        "likely_fix_shape": (
            "Somebody changed one side without the other. Put the wording in "
            "docs/agent_rules.md and regenerate — a hand edit to CLAUDE.md is lost on the "
            "next refresh."
        ),
        "next_checks": [
            "uv run python scripts/sync_agent_docs.py",
            "uv run python scripts/sync_agent_docs.py --check",
        ],
        "stop_widening_condition": (
            "Stop widening once sync_agent_docs.py --check passes with regenerated wrappers."
        ),
    },
    "drift.integrity.error": {
        "meaning": (
            "The generated AI context detected a broken wiring relationship such as an alias, "
            "getter, service key, router, or scaffold marker mismatch."
        ),
        "smallest_files_to_read": [
            "docs/ai_context_map.json",
            "project/infrastructure/api/dependencies.py",
            "project/infrastructure/api/router_registration.py",
            "project/core/composition_root.py",
        ],
        "smallest_command_to_rerun": "uv run python scripts/generate_ai_context.py --check",
        "likely_fix_shape": (
            "Repair the smallest broken wiring edge, then refresh the AI context artifacts "
            "instead of widening into unrelated runtime code."
        ),
        "next_checks": [
            "make refresh-ai-context",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "stop_widening_condition": (
            "Stop widening once generate_ai_context.py --check passes and the integrity section "
            "returns status=ok."
        ),
    },
    "drift.generated.missing": {
        "meaning": "A generated AI context file is missing from the checked-in repository state.",
        "smallest_files_to_read": [
            "CLAUDE.md",
            "docs/ai_context_map.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/generate_ai_context.py --check",
        "likely_fix_shape": (
            "Regenerate the AI-facing derived artifacts instead of editing generated JSON by hand."
        ),
        "next_checks": [
            "make refresh-generated-docs",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "stop_widening_condition": (
            "Stop widening once the generated file exists again and generate_ai_context.py --check passes."
        ),
    },
    "drift.generated.outdated": {
        "meaning": "A generated AI context artifact no longer matches the repository sources.",
        "smallest_files_to_read": [
            "CLAUDE.md",
            "docs/ai_context_map.json",
            "docs/architecture_rules.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/generate_ai_context.py --check",
        "likely_fix_shape": (
            "Regenerate the derived AI context artifacts after changing query tooling, file policy, or wiring sources."
        ),
        "next_checks": [
            "make refresh-ai-context",
            "uv run python scripts/generate_ai_context.py --check",
        ],
        "stop_widening_condition": (
            "Stop widening once generate_ai_context.py --check passes with freshly regenerated artifacts."
        ),
    },
}


def _query_recovery_commands() -> list[str]:
    return [
        "uv run python scripts/generate_ai_context.py --check",
        "make refresh-generated-docs",
        "uv run python scripts/validate_architecture.py",
        "uv run python scripts/validate_endpoint_wiring.py",
        "uv run python scripts/validate_runtime_ownership.py",
        "uv run python scripts/doctor_ai_context.py",
    ]


def degraded_query_payload(
    *,
    degraded_status: str,
    issues: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "status": "error",
        "degraded_status": degraded_status,
        "issues": issues,
        "available_commands": _query_recovery_commands(),
        "temporarily_unavailable_queries": list(QUERY_SUPPORTED_COMMANDS),
    }


def _is_low_confidence_entry(metadata: dict[str, object]) -> bool:
    return metadata.get("confidence") != "high" or metadata.get("source") != "ast_exact"


def partial_context_warnings(context_map: dict[str, object]) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []
    for service_key, service_metadata in context_map["service_registry"].items():
        if not _is_low_confidence_entry(service_metadata):
            continue
        warnings.append(
            {
                "kind": "service",
                "identifier": service_key,
                "path": service_metadata.get("source_file"),
                "confidence": service_metadata.get("confidence"),
                "source": service_metadata.get("source"),
                "message": (
                    f"service '{service_key}' was resolved heuristically; confirm real wiring files before edit"
                ),
            }
        )
    for module_name, route_metadata in context_map["route_inventory"].items():
        if not _is_low_confidence_entry(route_metadata):
            continue
        warnings.append(
            {
                "kind": "route",
                "identifier": module_name,
                "path": route_metadata.get("file"),
                "confidence": route_metadata.get("confidence"),
                "source": route_metadata.get("source"),
                "message": (
                    f"route '{module_name}' was resolved heuristically; confirm real wiring files before edit"
                ),
            }
        )
    return warnings


def warnings_for_service_keys(
    context_map: dict[str, object],
    service_keys: list[str],
) -> list[dict[str, object]]:
    warning_map = {
        warning["identifier"]: warning
        for warning in partial_context_warnings(context_map)
        if warning["kind"] == "service"
    }
    return [
        warning_map[service_key]
        for service_key in sorted(dict.fromkeys(service_keys))
        if service_key in warning_map
    ]


def query_context_status() -> dict[str, object]:
    context_map, _change_map, _architecture_rules = context_bundle()
    if context_map["integrity"]["status"] != "ok":
        return degraded_query_payload(
            degraded_status="integrity_error",
            issues=[
                {
                    "issue_type": issue["issue_type"],
                    "message": issue["message"],
                    "recommended_next_command": issue["recommended_next_command"],
                    "repair_protocol": list(issue["repair_protocol"]),
                }
                for issue in context_map["integrity"]["issues"]
            ],
        )

    drift_issues = generated_output_issues(_build_generated_outputs())
    if drift_issues:
        return {
            "status": "generated_outdated",
            "issues": [
                {
                    **issue,
                    "issue_type": "generated_outdated",
                    "recommended_next_command": "make refresh-generated-docs",
                }
                for issue in drift_issues
            ],
            "available_commands": _query_recovery_commands(),
            "temporarily_unavailable_queries": [],
        }

    warnings = partial_context_warnings(context_map)
    if warnings:
        return {
            "status": "partial_context",
            "issues": warnings,
            "available_commands": _query_recovery_commands(),
            "temporarily_unavailable_queries": [],
        }

    return {
        "status": "ok",
        "issues": [],
        "available_commands": _query_recovery_commands(),
        "temporarily_unavailable_queries": [],
    }


def context_bundle() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    return build_context_map(), build_change_map(), build_architecture_rules()


def regeneration_plan_from_commands(commands: list[str]) -> dict[str, object]:
    if not commands:
        return {
            "decision": "none",
            "reason": (
                "This file does not normally regenerate derived artifacts; use validators and "
                "targeted tests only."
            ),
            "commands": [],
        }

    if len(commands) > 1:
        return {
            "decision": "refresh-multiple",
            "reason": (
                "This file touches more than one generated surface, so use the narrow refresh "
                "targets in sequence."
            ),
            "commands": list(commands),
        }

    target = commands[0]
    return {
        "decision": target.removeprefix("make ").replace(" ", "-"),
        "reason": (
            "This file maps to one generated surface, so use the narrow refresh target instead "
            "of a wider generated-doc refresh."
        ),
        "commands": [target],
    }


def file_policy_entry(
    architecture_rules: dict[str, object],
    repo_path: str,
) -> tuple[str, dict[str, object]]:
    normalized_path = relative_repo_path(repo_path)
    file_policy_index = architecture_rules.get("file_policy_index", {})
    metadata = file_policy_index.get(normalized_path)
    if metadata is not None:
        return normalized_path, metadata

    # **LOGIC_STEP**: Fallback — derive a minimal policy payload for files that
    # belong to a known EDIT_ZONES classification but lack an explicit
    # FILE_POLICY entry. Reserves the KeyError for paths truly outside the
    # project tree.
    zone_info = zone_for_path(normalized_path, architecture_rules)
    if zone_info["zone"] == "unclassified":
        raise KeyError(
            f"Unknown or unindexed file policy path: {normalized_path}\n"
            f"\n"
            f"This path is not in FILE_POLICY_INDEX and matches no EDIT_ZONES pattern.\n"
            f"\n"
            f"Next actions:\n"
            f"  1. Add an explicit entry to ai_context/file_policy.py:FILE_POLICY_INDEX. "
            f"Required fields (14): role, why_it_exists, layer, common_tasks, "
            f"kernel_or_reference, classification_reason, source_of_truth, edit_zone "
            f"(one of: safe, caution, expert, generated_do_not_edit), regenerate_if_changed, "
            f"validators_if_changed, generated_artifacts, do_not_edit_directly, "
            f"recommended_diff_style, likely_failure_rules. "
            f"Copy the shape from a neighboring entry (e.g., the composition_root.py entry).\n"
            f"  2. Or extend ai_context/constants.py:EDIT_ZONES with a path or prefix matching "
            f"this file (exact match for single files like 'Makefile', or trailing-slash prefix "
            f"like '.github/workflows/'). The agent will then receive a derived (derived=true) "
            f"FILE_POLICY payload via the EDIT_ZONES fallback.\n"
            f"\n"
            f"See: ai_context/constants.py:EDIT_ZONES, "
            f"scripts/validate_file_policy.py:REQUIRED_FIELDS, "
            f"ai_query/common.py:_derived_file_policy (fallback shape)."
        )

    return normalized_path, _derived_file_policy(normalized_path, zone_info, architecture_rules)


def _derived_file_policy(
    normalized_path: str,
    zone_info: dict[str, str],
    architecture_rules: dict[str, object],
) -> dict[str, object]:
    """Build a minimal FILE_POLICY-shaped payload from EDIT_ZONES classification.

    Returns a dict with all 14 standard FILE_POLICY fields plus a
    ``"derived": True`` marker so callers can distinguish derived payloads from
    explicit FILE_POLICY entries.
    """
    zone = zone_info["zone"]
    risk = zone_info["risk"]

    # **LOGIC_STEP**: Heuristic layer assignment from path prefix.
    layer_map = {
        "project/core/": "core",
        "project/infrastructure/": "infrastructure",
        "project/application/": "application",
        "project/domain/": "domain",
        "scripts/": "tooling",
        "ai_context/": "tooling",
        "ai_query/": "tooling",
        "tests/": "tests",
    }
    layer = "unclassified"
    for prefix, layer_name in layer_map.items():
        if normalized_path.startswith(prefix):
            layer = layer_name
            break

    # **LOGIC_STEP**: kernel_or_reference heuristic — match against the
    # template_kernel_paths block surfaced in architecture_rules.
    template_kernel_paths = architecture_rules.get("template_kernel_paths", []) or []
    kernel_or_reference = "unclassified"
    for kernel_path in template_kernel_paths:
        if not isinstance(kernel_path, str):
            continue
        if kernel_path.endswith("/"):
            if normalized_path.startswith(kernel_path):
                kernel_or_reference = "template_kernel"
                break
        elif normalized_path == kernel_path:
            kernel_or_reference = "template_kernel"
            break

    # **LOGIC_STEP**: Diff-style default per zone — minimal for high-risk
    # surfaces, content-local for safe-zone iteration.
    if zone in {"expert", "generated_do_not_edit"}:
        diff_style = "minimal-diff"
    else:
        diff_style = "content-local-diff"

    refresh_decision = regeneration_targets_for_paths([normalized_path])
    validators = validator_recommendations_for_paths([normalized_path])
    artifacts = generated_artifacts_for_paths([normalized_path])

    return {
        "role": (
            f"Derived policy for {zone} edit zone (no explicit FILE_POLICY entry "
            f"in ai_context/file_policy.py)."
        ),
        "why_it_exists": (
            "Auto-derived from EDIT_ZONES membership. Add an explicit FILE_POLICY "
            "entry in ai_context/file_policy.py if this file becomes a frequent "
            "agent target."
        ),
        "layer": layer,
        "common_tasks": [],
        "kernel_or_reference": kernel_or_reference,
        "classification_reason": (
            f"Derived from EDIT_ZONES classification ({zone}). Explicit FILE_POLICY "
            "entries override this fallback."
        ),
        "source_of_truth": "docs/architecture_rules.json:file_policy_index",
        "edit_zone": zone,
        "risk": risk,
        "regenerate_if_changed": refresh_decision,
        "validators_if_changed": validators,
        "generated_artifacts": artifacts,
        "do_not_edit_directly": zone == "generated_do_not_edit",
        "recommended_diff_style": diff_style,
        "likely_failure_rules": [],
        "derived": True,
    }


def failure_playbook(rule_id: str) -> dict[str, object]:
    # **LOGIC_STEP**: Imported here rather than at module scope. scripts.doctor_ai_context pulls in
    # the collectors for every validator, and importing that chain at the top of this module ties
    # every `query_ai_context.py` invocation to it for the sake of a handful of rule ids.
    from scripts.doctor_ai_context import get_doctor_layer_playbook

    # **LOGIC_STEP**: These were missing while nothing printed their rule_ids, and became a live
    # defect the moment the doctor grew layers for dependencies, test quality and secrets: it
    # started naming ids on screen that `failure rule` answered "Unknown failure rule ID" for.
    # Covered by tests/application/test_doctor_ai_context.py, parametrised over every rule_id any
    # doctor layer can emit rather than over a list written by hand.
    from scripts.validate_architecture import get_architecture_rule_playbook
    from scripts.validate_cbm import get_cbm_rule_playbook
    from scripts.validate_dependencies import get_dependencies_rule_playbook
    from scripts.validate_endpoint_wiring import get_endpoint_rule_playbook
    from scripts.validate_file_policy import get_file_policy_rule_playbook
    from scripts.validate_migrations import get_migrations_rule_playbook
    from scripts.validate_module_sizes import get_module_size_playbook
    from scripts.validate_repository_metadata import get_repository_metadata_rule_playbook
    from scripts.validate_runtime_ownership import get_runtime_ownership_rule_playbook
    from scripts.validate_secrets import get_secrets_rule_playbook
    from scripts.validate_test_quality import get_test_quality_rule_playbook

    for getter in (
        get_doctor_layer_playbook,
        get_dependencies_rule_playbook,
        get_secrets_rule_playbook,
        get_test_quality_rule_playbook,
        get_architecture_rule_playbook,
        get_endpoint_rule_playbook,
        get_runtime_ownership_rule_playbook,
        get_cbm_rule_playbook,
        get_module_size_playbook,
        get_repository_metadata_rule_playbook,
        get_migrations_rule_playbook,
        get_file_policy_rule_playbook,
    ):
        playbook = getter(rule_id)
        if playbook is None:
            continue
        return {
            "rule_id": rule_id,
            "meaning": playbook["meaning"],
            "smallest_files_to_read": list(playbook["read_first"]),
            "smallest_command_to_rerun": playbook["smallest_command_to_rerun"],
            "likely_fix_shape": playbook["likely_fix_shape"],
            "next_checks": list(playbook["next_checks"]),
            "stop_widening_condition": playbook["stop_widening_condition"],
        }

    playbook = _DRIFT_RULE_PLAYBOOKS.get(rule_id)
    if playbook is None:
        raise KeyError(f"Unknown failure rule ID: {rule_id}")
    return {
        "rule_id": rule_id,
        "meaning": playbook["meaning"],
        "smallest_files_to_read": list(playbook["smallest_files_to_read"]),
        "smallest_command_to_rerun": playbook["smallest_command_to_rerun"],
        "likely_fix_shape": playbook["likely_fix_shape"],
        "next_checks": list(playbook["next_checks"]),
        "stop_widening_condition": playbook["stop_widening_condition"],
    }


def likely_failure_entries(rule_ids: list[str]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for rule_id in rule_ids:
        playbook = failure_playbook(rule_id)
        entries.append(
            {
                "rule_id": rule_id,
                "meaning": playbook["meaning"],
                "smallest_command_to_rerun": playbook["smallest_command_to_rerun"],
                "likely_fix_shape": playbook["likely_fix_shape"],
                "smallest_files_to_read": list(playbook["smallest_files_to_read"]),
            }
        )
    return entries


def diff_style_guidance(recommended_diff_style: str) -> str:
    guidance_map = {
        "minimal-diff": "Touch the smallest local block and preserve the existing wiring shape.",
        "content-local-diff": "Keep edits content-local and avoid incidental formatting or structure changes nearby.",
        "feature-local-diff": "Confine edits to the feature surface and avoid widening into shared kernel files.",
    }
    return guidance_map.get(
        recommended_diff_style,
        "Keep the change narrowly scoped to the existing structure.",
    )


def relative_repo_path(path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            return candidate.relative_to(ROOT_DIR).as_posix()
        except ValueError:
            return candidate.as_posix()
    return candidate.as_posix()


def display_repo_path(path: str) -> str:
    normalized = relative_repo_path(path)
    if path.endswith("/") and not normalized.endswith("/"):
        return f"{normalized}/"
    return normalized


def unique_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for path in paths:
        normalized = relative_repo_path(path)
        if not normalized or normalized == "." or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def zone_via_edit_zones_patterns(path: str, edit_zones: dict[str, list[str]]) -> dict[str, str]:
    # **LOGIC_STEP**: The most specific pattern wins, not the first one found. The old loop
    # returned on first match while iterating zones in dict order, so a broad prefix in an early
    # zone shadowed an exact one in a later zone. That was harmless only while no zone held a
    # broad prefix: adding `project/` to `caution` — the fix for the 21 unclassified files below
    # it — silently demoted `project/core/logging/` from expert/high to caution/medium, which is
    # the opposite of what the zone table says. Longest normalized pattern wins instead.
    normalized_path = relative_repo_path(path)
    best_zone: str | None = None
    best_length = -1
    for zone_name, patterns in edit_zones.items():
        for pattern in patterns:
            normalized_pattern = relative_repo_path(pattern)
            if pattern.endswith("/"):
                prefix = normalized_pattern.rstrip("/") + "/"
                matched = normalized_path == normalized_pattern.rstrip(
                    "/"
                ) or normalized_path.startswith(prefix)
            else:
                matched = normalized_path == normalized_pattern
            if matched and len(normalized_pattern) > best_length:
                best_zone, best_length = zone_name, len(normalized_pattern)
    if best_zone is None:
        return {"zone": "unclassified", "risk": "unknown"}
    return {"zone": best_zone, "risk": ZONE_RISK[best_zone]}


def zone_for_path(path: str, architecture_rules: dict[str, object]) -> dict[str, str]:
    normalized_path = relative_repo_path(path)
    file_policy_index = architecture_rules.get("file_policy_index", {})
    if isinstance(file_policy_index, dict):
        entry = file_policy_index.get(normalized_path)
        if isinstance(entry, dict):
            edit_zone = entry.get("edit_zone")
            if isinstance(edit_zone, str) and edit_zone in ZONE_RISK:
                return {"zone": edit_zone, "risk": ZONE_RISK[edit_zone]}
    edit_zones = architecture_rules["edit_zones"]
    zone = zone_via_edit_zones_patterns(normalized_path, edit_zones)
    if zone["zone"] != "unclassified" or "/" in normalized_path:
        return zone
    # **LOGIC_STEP**: A file at the repository root that nothing else claims. Every directory here
    # has a catch-all, the root had none, and adding an ordinary CHANGELOG.md or .editorconfig
    # therefore turned the gate red until somebody edited ai_context/constants.py — a papercut
    # inherited by every project built on this template. Caution rather than safe because a root
    # file usually configures the whole build. A new top-level DIRECTORY is deliberately still
    # unclassified: that is a decision somebody should make once, out loud.
    return {"zone": "caution", "risk": ZONE_RISK["caution"]}


def annotate_paths(
    paths: list[str],
    architecture_rules: dict[str, object],
) -> list[dict[str, str]]:
    annotated: list[dict[str, str]] = []
    for path in paths:
        metadata = zone_for_path(path, architecture_rules)
        annotated.append({"path": display_repo_path(path), **metadata})
    return annotated


def _git_diff_lines(argv: list[str]) -> list[str]:
    try:
        completed = subprocess.run(
            argv,
            cwd=ROOT_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            "Git is not available, so the current diff cannot be resolved. Ask about a single "
            "file instead: 'uv run python scripts/query_ai_context.py before-edit file <path>'."
        ) from error
    except subprocess.CalledProcessError as error:
        message = (error.stderr or error.stdout or "").strip()
        if "not a git repository" in message.lower():
            raise RuntimeError(
                "The current workspace is not a git worktree, so there is no diff to resolve. "
                "Ask about a single file instead: "
                "'uv run python scripts/query_ai_context.py before-edit file <path>'."
            ) from error
        raise RuntimeError(
            "Unable to resolve the current git diff. Ask about a single file instead: "
            "'uv run python scripts/query_ai_context.py before-edit file <path>'."
        ) from error
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def classify_workset_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    changed_files: list[str] = []
    deleted_files: list[str] = []
    for normalized_path in unique_paths(paths):
        absolute_path = ROOT_DIR / normalized_path
        if absolute_path.exists():
            changed_files.append(normalized_path)
        else:
            deleted_files.append(normalized_path)
    return changed_files, deleted_files


def diff_workset_paths() -> dict[str, object]:
    staged = _git_diff_lines(["git", "diff", "--name-only", "--cached"])
    unstaged = _git_diff_lines(["git", "diff", "--name-only"])
    untracked = _git_diff_lines(["git", "ls-files", "--others", "--exclude-standard"])
    changed_files, deleted_files = classify_workset_paths([*staged, *unstaged, *untracked])
    status = "empty" if not changed_files and not deleted_files else "ok"
    return {
        "subject": "diff",
        "status": status,
        "changed_files": changed_files,
        "deleted_files": deleted_files,
    }


def resolve_workset_subject(subject_kind: str, subject_identifiers: list[str]) -> dict[str, object]:
    if subject_kind == "diff":
        return diff_workset_paths()
    raise ValueError(f"Unsupported workset subject kind: {subject_kind}")


def service_file_path(service: dict[str, object]) -> str | None:
    module_name = service.get("module")
    if not isinstance(module_name, str) or not module_name:
        return None
    module_parts = module_name.split(".")
    if len(module_parts) < 2:
        return None
    return "/".join(module_parts[:-1]) + ".py"


def service_routes(
    context_map: dict[str, object],
    service_key: str,
) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    route_inventory = context_map["route_inventory"]
    for module_name, route_metadata in route_inventory.items():
        matching_endpoints = [
            endpoint
            for endpoint in route_metadata["endpoints"]
            if any(
                dependency["service_key"] == service_key for dependency in endpoint["dependencies"]
            )
        ]
        if not matching_endpoints:
            continue
        matches.append(
            {
                "module": module_name,
                "file": route_metadata["file"],
                "router": route_metadata["router"],
                "prefix": route_metadata["prefix"],
                "endpoints": matching_endpoints,
            }
        )
    return matches


def path_exists(path: str) -> bool:
    return (ROOT_DIR / relative_repo_path(path)).exists()


def sort_paths_by_risk(paths: list[str], architecture_rules: dict[str, object]) -> list[str]:
    risk_order = {"high": 0, "medium": 1, "low": 2, "unknown": 3, "do-not-edit": 4}
    unique = unique_paths(paths)
    return sorted(
        unique,
        key=lambda path: (
            risk_order.get(zone_for_path(path, architecture_rules)["risk"], 99),
            path,
        ),
    )


def normalize_test_candidates(candidates: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        normalized = display_repo_path(candidate)
        if normalized in seen:
            continue
        if "<" not in normalized and not path_exists(normalized):
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def service_test_candidates(service_key: str) -> list[str]:
    base_name = service_key
    for suffix in ("_service", "_repository"):
        if base_name.endswith(suffix):
            base_name = base_name[: -len(suffix)]
            break
    candidates = [
        # **LOGIC_STEP**: `test_<name>_vertical.py` is the name .agents/skills/add-vertical prescribes and
        # the shipped vertical uses, and it was the one spelling missing here. The consequence was
        # not a worse suggestion but a silent one: `make quality-gates` on a changed service
        # printed "no likely tests for this workset", ran zero tests, and reported "workset checks
        # passed". Measured on the reference vertical — 0 tests before this line, 25 after.
        f"tests/application/test_{base_name}_vertical.py",
        f"tests/application/test_{base_name}_service.py",
        f"tests/application/test_{base_name}.py",
    ]
    return normalize_test_candidates(candidates)


def name_matched_test_candidates(normalized_path: str) -> list[str]:
    # **LOGIC_STEP**: The convention this repository already follows — tests/<suite>/test_<stem>.py
    # next to project/<anything>/<stem>.py. Before this, mapping went only through the service
    # registry and the route inventory, so a changed repository, ORM module or middleware returned
    # `likely_tests: []` while two files named after it sat in tests/. Measured: a diff of eight
    # files, one of them a repository test, ran zero tests and reported "workset checks passed".
    #
    # tests/functional is deliberately absent. pytest.ini ignores that directory, and naming a file
    # there by path overrides the ignore — the narrow loop would try to run a suite that needs
    # Docker and a live database. Those tests belong to `make test-e2e`, which is where the skills
    # already send you.
    stem = Path(normalized_path).stem
    if not stem or stem.startswith("__"):
        return []
    return normalize_test_candidates(
        [
            f"tests/application/test_{stem}.py",
            f"tests/infrastructure/test_{stem}.py",
        ]
    )


# ATTRIBUTE: _LAYER_SUFFIXES (tuple[str, ...])
# SUMMARY: Suffixes a vertical's file carries to say which layer it belongs to.
# NOTE: The order matters only in that the first match wins; no stem here ends in two of them.
# `_agent`, `_mock`, `_tools` and `_verdict` were missing until 2026-09-08: an agentic vertical's
# `<name>_agent.py`, `<name>_mock.py`, `<name>_tools.py` and `<name>_verdict.py` files kept their
# suffix through `vertical_names_for_path`, so the derived "name" was never a name the project had
# registered and `workset diff`'s "each vertical file finds its own tests" gate reported no likely
# tests for them — reproduced on an agentic vertical built from this template in the 2026-09
# audit. The kernel ships no such vertical itself, so nothing here caught it until then.
_LAYER_SUFFIXES = (
    "_service",
    "_repository",
    "_repo",
    "_dtos",
    "_dto",
    "_port",
    "_ports",
    "_models",
    "_model",
    "_endpoints",
    "_adapter",
    "_orm",
    "_agent",
    "_mock",
    "_tools",
    "_verdict",
)

# ATTRIBUTE: _RUNNABLE_TEST_SUITES (tuple[str, ...])
# SUMMARY: The suites the narrow loop is allowed to run — functional needs Docker and a database.
_RUNNABLE_TEST_SUITES = ("tests/application", "tests/infrastructure")

# ATTRIBUTE: _SHORTEST_VERTICAL_NAME (int)
# SUMMARY: Below this a name is too generic to match test files by, so nothing is guessed.
_SHORTEST_VERTICAL_NAME = 4


def vertical_names_for_path(normalized_path: str) -> list[str]:
    # **LOGIC_STEP**: The name of the thing, recovered from the file that implements one layer of
    # it. `reference_task_service.py`, `reference_task_repository.py` and the plural endpoint
    # module `reference_tasks.py` are all the `reference_task` vertical, and its tests are named
    # after the vertical rather than after any one of those files. Both spellings are returned
    # because the endpoint module is conventionally plural and the domain model is not.
    if not normalized_path.startswith("project/"):
        return []
    stem = Path(normalized_path).stem
    if not stem or stem.startswith("__"):
        return []
    for suffix in _LAYER_SUFFIXES:
        if stem.endswith(suffix) and len(stem) > len(suffix):
            stem = stem[: -len(suffix)]
            break
    names = [stem]
    if stem.endswith("s"):
        names.append(stem[:-1])
    return [name for name in names if len(name) >= _SHORTEST_VERTICAL_NAME]


def registered_vertical_names(context_map: dict[str, object]) -> set[str]:
    # **LOGIC_STEP**: The wiring is the authority on which verticals exist. Reading it from the
    # context map rather than from docs/project_context.json keeps this working in a project that
    # has not filled that file in — and that file's declared status is a description, checked by
    # nothing, since the rule comparing it against the wiring was removed.
    registry = context_map.get("service_registry", {})
    if not isinstance(registry, dict):
        return set()
    names: set[str] = set()
    for key, metadata in registry.items():
        if isinstance(metadata, dict) and metadata.get("category") != "vertical":
            continue
        names.add(str(key).removesuffix("_service").removesuffix("_repository"))
    return names


def vertical_test_candidates(normalized_path: str, known_verticals: Iterable[str]) -> list[str]:
    # **LOGIC_STEP**: Named after the vertical, not spelled exactly like the file. Before this,
    # mapping was by exact stem, so `before-edit` on the shipped vertical's domain model and on
    # its endpoint module both answered with no tests at all, while four files named after that
    # vertical sat in tests/ — the tool was empty for the one vertical the template ships, and
    # would be empty for every vertical copied from it.
    #
    # The name has to be one the project actually registered. Deriving it from the stem alone was
    # enough to match a whole underscore-delimited segment of a test's name, and plenty of files
    # are named after no vertical at all: `project/core/logging/context.py` answered with four
    # tests for the unrelated ai_context tooling, and `dependencies.py` — a wiring hotspot — with
    # the unit tests of the dependency-pinning validator. A wrong suggestion here is worse than
    # none: the narrow loop runs it and reports that the change was exercised.
    names = [name for name in vertical_names_for_path(normalized_path) if name in known_verticals]
    if not names:
        return []
    candidates: list[str] = []
    for suite in _RUNNABLE_TEST_SUITES:
        directory = ROOT_DIR / suite
        if not directory.is_dir():
            continue
        for test_file in sorted(directory.rglob("test_*.py")):
            segments = f"_{test_file.stem.removeprefix('test_')}_"
            if any(f"_{name}_" in segments for name in names):
                candidates.append(str(test_file.relative_to(ROOT_DIR)))
    return normalize_test_candidates(candidates)


def changed_test_is_its_own_candidate(normalized_path: str) -> list[str]:
    # **LOGIC_STEP**: A changed test is a test to run, and nothing else maps it. This is a
    # complement to the by-name rule above, never a replacement: on a diff that touches only
    # production code it contributes nothing, which is why it cannot be the whole fix. Restricted
    # to the two suites the narrow loop is allowed to run, for the same reason as above.
    if not normalized_path.startswith(("tests/application/", "tests/infrastructure/")):
        return []
    if not Path(normalized_path).name.startswith("test_"):
        return []
    return normalize_test_candidates([normalized_path])


def endpoint_test_candidates(
    module_names: list[str],
    service_keys: list[str],
) -> list[str]:
    candidates: list[str] = []
    for module_name in module_names:
        candidates.append(f"tests/application/test_{module_name}_endpoints.py")
        if module_name.endswith("s"):
            candidates.append(f"tests/application/test_{module_name[:-1]}_endpoints.py")
    for service_key in service_keys:
        base_name = service_key.removesuffix("_service").removesuffix("_repository")
        candidates.append(f"tests/application/test_{base_name}_endpoints.py")
    return normalize_test_candidates(candidates)


def validator_recommendations_for_paths(paths: list[str]) -> list[str]:
    commands: list[str] = []
    normalized_paths = [relative_repo_path(path) for path in paths]

    def add(command: str) -> None:
        if command not in commands:
            commands.append(command)

    if any(path.startswith("project/") for path in normalized_paths):
        add("uv run python scripts/validate_architecture.py")
        add("uv run python scripts/validate_runtime_ownership.py")
    # **LOGIC_STEP**: A hand-edited or hand-written revision is exactly what this validator exists
    # for, and it was the one file class that got an empty required_validators list.
    if any(path.startswith("alembic/") for path in normalized_paths):
        add("uv run python scripts/validate_migrations.py")
    if any(
        path.startswith("project/infrastructure/api/endpoints/")
        or path == "project/infrastructure/api/dependencies.py"
        or path == "project/infrastructure/api/router_registration.py"
        or path == "project/core/service_registration.py"
        or path == "project/core/composition_root.py"
        for path in normalized_paths
    ):
        add("uv run python scripts/validate_endpoint_wiring.py")
    if any(
        path == "project/core/composition_root.py"
        or path == "project/core/service_registration.py"
        or path == "project/infrastructure/api/dependencies.py"
        or path == "project/infrastructure/api/router_registration.py"
        or path == "scripts/generate_ai_context.py"
        or path == "scripts/query_ai_context.py"
        or path.startswith("ai_context/")
        or path.startswith("ai_query/")
        for path in normalized_paths
    ):
        add("uv run python scripts/generate_ai_context.py --check")
    if any(
        path == "docs/agent_rules.md" or path == "scripts/sync_agent_docs.py"
        for path in normalized_paths
    ):
        add("uv run python scripts/sync_agent_docs.py --check")
    if any(
        path.startswith("project/core/logging/") or path == "project/core/serialization.py"
        for path in normalized_paths
    ):
        add("uv run python scripts/run_all_tests.py --skip-functional")
    return commands


def generated_artifacts_for_paths(paths: list[str]) -> list[str]:
    normalized_paths = {relative_repo_path(path) for path in paths}
    artifacts: list[str] = []

    def add(entries: list[str]) -> None:
        for entry in entries:
            if entry not in artifacts:
                artifacts.append(entry)

    if normalized_paths & {
        "project/core/composition_root.py",
        "project/core/service_registration.py",
        "project/infrastructure/api/dependencies.py",
        "project/infrastructure/api/router_registration.py",
        "scripts/generate_ai_context.py",
        "scripts/query_ai_context.py",
    } or any(
        path.startswith("ai_context/") or path.startswith("ai_query/") for path in normalized_paths
    ):
        add(
            [
                "docs/ai_context_map.json",
                "docs/ai_change_map.json",
                "docs/architecture_rules.json",
            ]
        )
    if normalized_paths & {
        "scripts/structure_builder.py",
        "docs/project_map.md",
    }:
        add(["docs/project_map.md"])
    if normalized_paths & {
        "docs/agent_rules.md",
        "scripts/sync_agent_docs.py",
    }:
        add(["CLAUDE.md"])
    if normalized_paths & {
        "docs/ai_context_map.json",
        "docs/ai_change_map.json",
        "docs/architecture_rules.json",
    }:
        add(
            [
                "docs/ai_context_map.json",
                "docs/ai_change_map.json",
                "docs/architecture_rules.json",
            ]
        )
    if any(path.startswith("project/infrastructure/api/endpoints/") for path in normalized_paths):
        add(["docs/ai_context_map.json"])
    return artifacts


def regeneration_targets_for_paths(paths: list[str]) -> list[str]:
    normalized_paths = {relative_repo_path(path) for path in paths}
    targets: list[str] = []

    def add(target: str) -> None:
        if target not in targets:
            targets.append(target)

    if (
        normalized_paths
        & {
            "project/core/composition_root.py",
            "project/core/service_registration.py",
            "project/infrastructure/api/dependencies.py",
            "project/infrastructure/api/router_registration.py",
            "scripts/generate_ai_context.py",
            "scripts/query_ai_context.py",
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
            "docs/architecture_rules.json",
        }
        or any(
            path.startswith("ai_context/") or path.startswith("ai_query/")
            for path in normalized_paths
        )
        or any(
            path.startswith("project/infrastructure/api/endpoints/") for path in normalized_paths
        )
    ):
        add("make refresh-ai-context")

    if normalized_paths & {
        "docs/agent_rules.md",
        "scripts/sync_agent_docs.py",
        "CLAUDE.md",
    }:
        add("make refresh-agent-docs")

    if normalized_paths & {
        "scripts/structure_builder.py",
        "docs/project_map.md",
    }:
        add("make refresh-project-map")

    return targets


def regeneration_plan_for_paths(paths: list[str]) -> dict[str, object]:
    targets = regeneration_targets_for_paths(paths)
    if len(targets) > 1:
        return {
            "decision": "refresh-multiple",
            "reason": (
                "The selected paths touch more than one generated surface, so run the "
                "narrow refresh targets in sequence."
            ),
            "commands": targets,
        }

    if targets:
        target = targets[0]
        return {
            "decision": target.removeprefix("make ").replace(" ", "-"),
            "reason": (
                "The selected path maps to one generated surface, so use the narrow "
                "refresh target instead of a full generated-doc refresh."
            ),
            "commands": [target],
        }

    return {
        "decision": "none",
        "reason": (
            "The selected path does not normally regenerate derived artifacts; use "
            "validators and targeted tests only."
        ),
        "commands": [],
    }


def matching_tasks_for_paths(
    paths: list[str],
    tasks: dict[str, object],
) -> list[str]:
    normalized_paths = [relative_repo_path(path) for path in paths]
    matches: list[str] = []
    for task_name, task_metadata in tasks.items():
        for pattern in task_metadata["files"]:
            normalized_pattern = relative_repo_path(pattern)
            for path in normalized_paths:
                if pattern.endswith("/"):
                    prefix = normalized_pattern.rstrip("/") + "/"
                    if path.startswith(prefix):
                        matches.append(task_name)
                        break
                    continue
                if any(char in normalized_pattern for char in "*?[]"):
                    if fnmatch.fnmatch(path, normalized_pattern):
                        matches.append(task_name)
                        break
                    continue
                if path == normalized_pattern:
                    matches.append(task_name)
                    break
            else:
                continue
            break
    return sorted(dict.fromkeys(matches))


# ATTRIBUTE: _E2E_GATE_PATH_PREFIXES (tuple[str, ...])
# SUMMARY: Directory prefixes docs/agent_rules.md names as finished only by `make test-e2e`.
# NOTE: The rule ("Finish with `make quality-gates`, and with `make test-e2e` as well when the
# diff touched persistence, endpoints, wiring, or a migration") lived in agent_rules.md prose
# only —
# workset_payload's final_gate was hardcoded to ["make quality-gates"] regardless of what the
# diff touched, so `workset diff` on a persistence-only change recommended the one gate that runs
# none of the project's own queries and said nothing about the one that does. Measured on
# 2026-09-02 in that same file: reversing an ORDER BY clause under
# project/infrastructure/persistence/ left every quality-gates check green. Wiring files are
# matched separately below, against architecture_rules["wiring_files"], because they are exact
# files rather than a directory prefix.
_E2E_GATE_PATH_PREFIXES = (
    "project/infrastructure/persistence/",
    "project/infrastructure/api/endpoints/",
    # **LOGIC_STEP**: A migration belongs here for a reason the other two do not share: it is the
    # one change `make quality-gates` cannot check at all without a database. With none reachable
    # `scripts/validate_migrations.py` announces that it skipped and stays green, so `make
    # test-e2e`, which runs the same check against the functional stack's own database, is the
    # only local gate that ever executes the revision. Measured in both projects of the
    # 2026-09-07 duel: a migration that dropped a column instead of renaming it passed every
    # local gate.
    "alembic/versions/",
)


def requires_e2e_gate(paths: list[str], architecture_rules: dict[str, object]) -> bool:
    wiring_files = set(architecture_rules["wiring_files"].values())
    return any(path in wiring_files or path.startswith(_E2E_GATE_PATH_PREFIXES) for path in paths)


def final_gate_for_paths(paths: list[str], architecture_rules: dict[str, object]) -> list[str]:
    gate = ["make quality-gates"]
    if requires_e2e_gate(paths, architecture_rules):
        gate.append("make test-e2e")
    return gate


def tests_payload(
    unit_tests: list[str],
    integration_tests: list[str],
    validators: list[str],
    final_gate: list[str],
) -> dict[str, object]:
    return {
        "heuristic": True,
        "likely_unit_tests": unit_tests,
        "likely_integration_tests": integration_tests,
        "required_validators": validators,
        "final_gate": final_gate,
    }


# **LOGIC_STEP**: `architecture_rules` is threaded in for one line — the final gate. Without it
# this payload hardcoded ["make quality-gates"], the same defect `workset diff` had until
# 2026-09-08 and in the more misleading place of the two: `before-edit file` is asked about ONE
# file, usually right before editing it, so a reader looking at a repository path was told the
# gates would finish the job and never heard about `make test-e2e`.
def tests_for_file(
    context_map: dict[str, object],
    repo_path: str,
    architecture_rules: dict[str, object],
) -> dict[str, object]:
    normalized_path = relative_repo_path(repo_path)
    unit_tests: list[str] = []
    integration_tests: list[str] = []

    for service_key, service_metadata in context_map["service_registry"].items():
        # **LOGIC_STEP**: Two paths identify a service, and only one of them was checked.
        # service_file_path() derives the module that defines the constructor; `source_file` is
        # where the service is actually registered. project/core/service_registration.py is a
        # Wiring Hotspot with its own TestWiring suite, and because it is never a constructor's
        # module it matched nothing here — the one file most likely to break wiring was invisible
        # to the narrow loop by construction.
        if normalized_path not in {
            service_file_path(service_metadata),
            service_metadata.get("source_file"),
        }:
            continue
        unit_tests.extend(service_test_candidates(service_key))
        integration_tests.extend(
            endpoint_test_candidates(
                [match["module"] for match in service_routes(context_map, service_key)],
                [service_key],
            )
        )

    for module_name, route_metadata in context_map["route_inventory"].items():
        if route_metadata["file"] != normalized_path:
            continue
        route_service_keys = sorted(
            {
                dependency["service_key"]
                for endpoint in route_metadata["endpoints"]
                for dependency in endpoint["dependencies"]
            }
        )
        integration_tests.extend(endpoint_test_candidates([module_name], route_service_keys))

    unit_tests.extend(name_matched_test_candidates(normalized_path))
    unit_tests.extend(
        vertical_test_candidates(normalized_path, registered_vertical_names(context_map))
    )
    unit_tests.extend(changed_test_is_its_own_candidate(normalized_path))

    if normalized_path == "project/core/composition_root.py":
        unit_tests.extend(["tests/application/test_generate_ai_context.py"])
        # Kernel ships no integration test that exercises composition_root in a
        # full lifespan; verticals add their own integration coverage.
    if normalized_path.startswith("project/core/logging/"):
        unit_tests.extend(
            [
                "tests/application/test_logging_api.py",
                "tests/application/test_logging_redaction.py",
            ]
        )
    # **LOGIC_STEP**: A changed migration used to return an empty validator list — not even the
    # architecture check, because the path is outside project/. The ledger test is the one that
    # goes red when a revision and the ORM metadata disagree, and it is cheap.
    if normalized_path.startswith("alembic/"):
        unit_tests.extend(["tests/application/test_validate_migrations.py"])

    return tests_payload(
        unit_tests=normalize_test_candidates(unit_tests),
        integration_tests=normalize_test_candidates(integration_tests),
        validators=validator_recommendations_for_paths([normalized_path]),
        final_gate=final_gate_for_paths([normalized_path], architecture_rules),
    )


def match_tasks_for_file(
    change_map: dict[str, object],
    file_path: str,
    common_tasks: list[str],
) -> list[dict[str, object]]:
    """Match change-map tasks that are relevant to the given file path."""
    tasks = change_map.get("tasks", {})
    matched: list[dict[str, object]] = []
    for task_name, task_meta in tasks.items():
        # Match by common_tasks from file policy, or by file glob patterns in the task.
        by_name = task_name in common_tasks
        by_glob = any(
            fnmatch.fnmatch(file_path, pattern) or file_path.startswith(pattern.rstrip("*"))
            for pattern in task_meta.get("files", [])
        )
        if not (by_name or by_glob):
            continue
        entry: dict[str, object] = {"task": task_name}
        for key in (
            "tests_to_run",
            "common_mistakes",
            "forbidden_shortcuts",
            "minimal_checks",
        ):
            if key in task_meta:
                entry[key] = task_meta[key]
        matched.append(entry)
    return matched


def recommended_diff_style_for_paths(
    architecture_rules: dict[str, object], paths: list[str]
) -> str:
    priority = {
        "minimal-diff": 0,
        "content-local-diff": 1,
        "feature-local-diff": 2,
    }
    selected = "feature-local-diff"
    for path in unique_paths(paths):
        metadata = architecture_rules.get("file_policy_index", {}).get(path)
        style = "minimal-diff"
        if isinstance(metadata, dict):
            style = str(metadata.get("recommended_diff_style", "minimal-diff"))
        if priority.get(style, 0) < priority[selected]:
            selected = style
    return selected


def workset_payload(
    context_map: dict[str, object],
    change_map: dict[str, object],
    architecture_rules: dict[str, object],
    subject_kind: str,
    subject: str,
    changed_files: list[str],
    deleted_files: list[str],
    status: str,
) -> dict[str, object]:
    all_paths = unique_paths([*changed_files, *deleted_files])
    existing_changed_files = [path for path in changed_files if path_exists(path)]
    likely_unit_tests: list[str] = []
    likely_integration_tests: list[str] = []
    affected_service_keys: list[str] = []
    affected_aliases: list[str] = []
    affected_routes: list[str] = []

    for normalized_path in existing_changed_files:
        file_tests = tests_for_file(context_map, normalized_path, architecture_rules)
        likely_unit_tests.extend(file_tests["likely_unit_tests"])
        likely_integration_tests.extend(file_tests["likely_integration_tests"])
        impact_payload = file_impact_payload(
            context_map,
            change_map,
            architecture_rules,
            normalized_path,
        )
        affected_service_keys.extend(impact_payload["affected_service_keys"])
        affected_aliases.extend(impact_payload["affected_aliases"])
        affected_routes.extend(impact_payload["affected_routes"])

    context_warnings = warnings_for_service_keys(
        context_map,
        list(dict.fromkeys(affected_service_keys)),
    )
    read_first = [
        "CLAUDE.md",
        *sort_paths_by_risk(existing_changed_files, architecture_rules)[:3],
    ]
    return {
        "subject_kind": subject_kind,
        "subject": subject,
        "status": status,
        "changed_files": list(existing_changed_files),
        "deleted_files": list(unique_paths(deleted_files)),
        "edit_points": annotate_paths(
            sort_paths_by_risk(all_paths, architecture_rules), architecture_rules
        ),
        "matched_tasks": matching_tasks_for_paths(all_paths, change_map["tasks"]),
        "affected_service_keys": sorted(dict.fromkeys(affected_service_keys)),
        "affected_aliases": sorted(dict.fromkeys(affected_aliases)),
        "affected_routes": sorted(dict.fromkeys(affected_routes)),
        "affected_generated_artifacts": generated_artifacts_for_paths(all_paths),
        "regeneration": regeneration_plan_for_paths(all_paths),
        "required_validators": validator_recommendations_for_paths(all_paths),
        "likely_unit_tests": normalize_test_candidates(likely_unit_tests),
        "likely_integration_tests": normalize_test_candidates(likely_integration_tests),
        "likely_tests": normalize_test_candidates([*likely_unit_tests, *likely_integration_tests]),
        "partial_context": bool(context_warnings),
        "context_warnings": context_warnings,
        "final_gate": final_gate_for_paths(all_paths, architecture_rules),
        "recommended_diff_style": recommended_diff_style_for_paths(architecture_rules, all_paths),
        "read_first": read_first,
    }


def file_impact_payload(
    context_map: dict[str, object],
    change_map: dict[str, object],
    architecture_rules: dict[str, object],
    repo_path: str,
) -> dict[str, object]:
    normalized_path = relative_repo_path(repo_path)
    affected_aliases: list[str] = []
    affected_routes: list[str] = []
    affected_service_keys: list[str] = []

    for service_key, service_metadata in context_map["service_registry"].items():
        if service_file_path(service_metadata) == normalized_path:
            affected_service_keys.append(service_key)

    for route_metadata in context_map["route_inventory"].values():
        if route_metadata["file"] != normalized_path:
            continue
        affected_routes.extend(endpoint["full_path"] for endpoint in route_metadata["endpoints"])
        for endpoint in route_metadata["endpoints"]:
            affected_aliases.extend(dependency["alias"] for dependency in endpoint["dependencies"])
            affected_service_keys.extend(
                dependency["service_key"] for dependency in endpoint["dependencies"]
            )

    if normalized_path == "project/infrastructure/api/dependencies.py":
        affected_aliases = sorted(context_map["dependency_registry"]["aliases"].keys())
        affected_routes = sorted(
            endpoint["full_path"]
            for route_metadata in context_map["route_inventory"].values()
            for endpoint in route_metadata["endpoints"]
        )
    elif normalized_path == "project/infrastructure/api/router_registration.py":
        affected_routes = sorted(
            endpoint["full_path"]
            for route_metadata in context_map["route_inventory"].values()
            for endpoint in route_metadata["endpoints"]
        )
    elif normalized_path in {
        "project/core/composition_root.py",
        "project/core/service_registration.py",
    }:
        affected_service_keys = sorted(context_map["service_registry"].keys())

    affected_service_keys = sorted(dict.fromkeys(affected_service_keys))
    affected_aliases = sorted(dict.fromkeys(affected_aliases))
    affected_routes = sorted(dict.fromkeys(affected_routes))
    likely_tests = tests_for_file(context_map, normalized_path, architecture_rules)
    context_warnings = warnings_for_service_keys(context_map, affected_service_keys)
    return {
        "subject_kind": "file",
        "subject": normalized_path,
        "edit_zone": zone_for_path(normalized_path, architecture_rules),
        "affected_service_keys": affected_service_keys,
        "affected_aliases": affected_aliases,
        "affected_routes": affected_routes,
        "affected_tasks": matching_tasks_for_paths([normalized_path], change_map["tasks"]),
        "affected_generated_artifacts": generated_artifacts_for_paths([normalized_path]),
        "regeneration": regeneration_plan_for_paths([normalized_path]),
        "likely_tests": likely_tests["likely_unit_tests"]
        + likely_tests["likely_integration_tests"],
        "required_validators": likely_tests["required_validators"],
        "partial_context": bool(context_warnings),
        "context_warnings": context_warnings,
    }


# ATTRIBUTE: _SYMBOL_SEARCH_ROOTS (tuple[str, ...])
# SUMMARY: First-party source directories `symbol` scans. Excludes .venv and every generated path.
_SYMBOL_SEARCH_ROOTS = ("project", "ai_context", "ai_query", "scripts", "tests", "alembic")

# ATTRIBUTE: _SYMBOL_RESULT_LIMIT (int)
# SUMMARY: Cap on matches returned for one name, so a generic identifier does not flood the payload.
_SYMBOL_RESULT_LIMIT = 25

# ATTRIBUTE: _SYMBOL_KIND_ORDER (dict[str, int])
# SUMMARY: Sort weight so a class or function definition is listed ahead of a same-named local
# variable — the two things someone searching a symbol name is almost always after.
_SYMBOL_KIND_ORDER = {"class": 0, "function": 1, "attribute": 2}


# FUNCTION: _symbol_search_files
# SUMMARY: List every first-party .py file `symbol` is allowed to open.
# OUTPUT: (Iterator[Path]): Files under _SYMBOL_SEARCH_ROOTS, sorted for deterministic output.
def _symbol_search_files() -> Iterator[Path]:
    for root_name in _SYMBOL_SEARCH_ROOTS:
        root = ROOT_DIR / root_name
        if not root.is_dir():
            continue
        yield from sorted(root.rglob("*.py"))


# FUNCTION: _symbol_matches_in_file
# SUMMARY: Find every definition or name binding matching `name` in one source file.
# INPUT: path (Path): File to parse.
# INPUT: name (str): Exact identifier to match (case-sensitive — Python names are).
# OUTPUT: (list[dict[str, object]]): {"file", "line", "kind"} entries, kind in class/function/attribute.
# **LOGIC_STEP**: `ast`, not a regex over `def NAME(` / `class NAME` / `NAME =` — indentation,
# multi-line signatures and string literals containing the name all defeat a line-based scan
# without visibly failing, and this repository already leans on `ast` for the same reason in
# ai_context/extraction.py and scripts/validate_cbm.py. A plain `Assign`/`AnnAssign` target is an
# `ast.Name` only for a module-level constant or a class-body field (`llm_mode: Literal[...] = ...`
# in AgentSettings, say) — `self.x = ...` binds an `ast.Attribute` instead, so instance attributes
# set in `__init__` are excluded without a special case. Bindings are read from module and class
# bodies only, never from inside a function: `ast.walk` reaches every local variable too, and a
# common name paid for it — measured on 2026-09-08, `symbol result` returned 75 matches, 24 of
# them locals reported as `attribute`, which is both noise and a different claim than the one this
# command makes. A definition, by contrast, is worth finding wherever it sits, nested helpers
# included, so classes and functions are still walked in full.
def _symbol_matches_in_file(path: Path, name: str) -> list[dict[str, object]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    relative_path = path.relative_to(ROOT_DIR).as_posix()
    found: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            found.append({"file": relative_path, "line": node.lineno, "kind": "class"})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            found.append({"file": relative_path, "line": node.lineno, "kind": "function"})
    found.extend(_field_bindings_in_scope(tree, name, relative_path))
    return found


# FUNCTION: _field_bindings_in_scope
# SUMMARY: Find name bindings written directly in a module body or a class body, never in a
# function body.
# INPUT: scope (ast.AST): Module or ClassDef whose own statements are read.
# INPUT: name (str): Exact identifier to match.
# INPUT: relative_path (str): Repository-relative path, carried into each entry.
# OUTPUT: (list[dict[str, object]]): {"file", "line", "kind"} entries with kind "attribute".
def _field_bindings_in_scope(
    scope: ast.AST,
    name: str,
    relative_path: str,
) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for statement in getattr(scope, "body", []):
        if isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name) and statement.target.id == name:
                found.append({"file": relative_path, "line": statement.lineno, "kind": "attribute"})
        elif isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    found.append(
                        {"file": relative_path, "line": statement.lineno, "kind": "attribute"}
                    )
        elif isinstance(statement, ast.ClassDef):
            found.extend(_field_bindings_in_scope(statement, name, relative_path))
    return found


# FUNCTION: find_symbol
# SUMMARY: Locate a function, class or field by exact name — file and line, without a repo-wide grep.
# INPUT: name (str): Exact identifier to search for.
# OUTPUT: (dict[str, object]): {"name", "matches", "truncated"}. `matches` is capped at
# _SYMBOL_RESULT_LIMIT and sorted class-before-function-before-attribute, then by file and line;
# `truncated` is true when more matches existed than the cap kept.
def find_symbol(name: str) -> dict[str, object]:
    matches: list[dict[str, object]] = []
    for path in _symbol_search_files():
        matches.extend(_symbol_matches_in_file(path, name))
    matches.sort(
        key=lambda match: (_SYMBOL_KIND_ORDER[str(match["kind"])], match["file"], match["line"])
    )
    return {
        "name": name,
        "matches": matches[:_SYMBOL_RESULT_LIMIT],
        "truncated": len(matches) > _SYMBOL_RESULT_LIMIT,
    }
