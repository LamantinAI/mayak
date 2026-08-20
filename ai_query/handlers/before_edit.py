from __future__ import annotations

from ai_query.common import (
    context_bundle,
    diff_style_guidance,
    file_policy_entry,
    likely_failure_entries,
    match_tasks_for_file,
    regeneration_plan_from_commands,
    tests_for_file,
)
from ai_query.models import QueryPayload


def query_before_edit(repo_path: str) -> QueryPayload:
    context_map, change_map, architecture_rules = context_bundle()
    normalized_path, metadata = file_policy_entry(architecture_rules, repo_path)
    watch_rules = list(metadata.get("likely_failure_rules", []))
    common_tasks = list(metadata["common_tasks"])
    payload = {
        "file": normalized_path,
        "role": metadata["role"],
        "common_tasks": common_tasks,
        "edit_zone": metadata["edit_zone"],
        "risk": metadata["risk"],
        "kernel_or_reference": metadata["kernel_or_reference"],
        "do_not_edit_directly": metadata["do_not_edit_directly"],
        "recommended_diff_style": metadata["recommended_diff_style"],
        "smallest_diff_guidance": diff_style_guidance(metadata["recommended_diff_style"]),
        "regeneration": regeneration_plan_from_commands(list(metadata["regenerate_if_changed"])),
        "required_validators": list(metadata["validators_if_changed"]),
        "watch_rules": watch_rules,
        "likely_failures": likely_failure_entries(watch_rules),
        "related_tests": tests_for_file(context_map, normalized_path),
        "task_guidance": match_tasks_for_file(change_map, normalized_path, common_tasks),
        "derived": bool(metadata.get("derived", False)),
    }
    return QueryPayload(kind="before-edit", identifier=normalized_path, payload=payload)
