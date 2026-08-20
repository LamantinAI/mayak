#!/usr/bin/env python3
# FILE: validate_file_policy.py
# SUMMARY: Validate ai_context.file_policy.FILE_POLICY_INDEX entries for required keys, valid zones, and on-disk path correctness.

from __future__ import annotations

import argparse
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from ai_context.constants import EDIT_ZONES, ZONE_RISK
from ai_context.file_policy import FILE_POLICY_INDEX
from ai_context.rendering import render_json
from ai_context.validator_contract import build_validator_issue_payload


ROOT_DIR = Path(__file__).resolve().parent.parent

# ATTRIBUTE: REQUIRED_FIELDS (frozenset[str])
# SUMMARY: Required fields every FILE_POLICY_INDEX entry must declare.
REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "role",
        "why_it_exists",
        "layer",
        "common_tasks",
        "kernel_or_reference",
        "classification_reason",
        "source_of_truth",
        "edit_zone",
        "regenerate_if_changed",
        "validators_if_changed",
        "generated_artifacts",
        "do_not_edit_directly",
        "recommended_diff_style",
        "likely_failure_rules",
    }
)


# ATTRIBUTE: _FILE_POLICY_RULE_PLAYBOOKS (dict[str, dict[str, object]])
# SUMMARY: Stable rule_id → playbook mapping for FILE_POLICY_INDEX schema failures.
_FILE_POLICY_RULE_PLAYBOOKS: dict[str, dict[str, object]] = {
    "file_policy.missing_field": {
        "meaning": (
            "A FILE_POLICY_INDEX entry is missing a required field. The 14 required fields are: "
            f"{', '.join(sorted(REQUIRED_FIELDS))}."
        ),
        "suggested_fix": (
            "Add the missing field to the entry, copying the shape from a neighboring entry."
        ),
        "read_first": [
            "ai_context/file_policy.py",
            "ai_context/constants.py",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_file_policy.py",
        "likely_fix_shape": (
            "Insert the named field with the appropriate value type (str, list, or bool)."
        ),
        "next_checks": [
            "uv run python scripts/validate_file_policy.py",
            "make quality-gates",
        ],
        "stop_widening_condition": ("Stop once every entry contains all 14 required fields."),
    },
    "file_policy.invalid_zone": {
        "meaning": (
            "A FILE_POLICY_INDEX entry has an edit_zone value not present in ZONE_RISK. "
            "Allowed zones: safe, caution, expert, generated_do_not_edit. Common cause: "
            "trailing whitespace or a typo."
        ),
        "suggested_fix": (
            "Replace the edit_zone value with one of: safe, caution, expert, generated_do_not_edit. "
            "Strip any trailing whitespace."
        ),
        "read_first": [
            "ai_context/constants.py",
            "ai_context/file_policy.py",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_file_policy.py",
        "likely_fix_shape": (
            "Set edit_zone to one of the four allowed values without surrounding whitespace."
        ),
        "next_checks": [
            "uv run python scripts/validate_file_policy.py",
        ],
        "stop_widening_condition": ("Stop once every entry's edit_zone is in ZONE_RISK."),
    },
    "file_policy.broken_path": {
        "meaning": (
            "A path referenced by a FILE_POLICY_INDEX entry does not exist on disk. This applies "
            "to the entry key itself, paths inside generated_artifacts, and script paths "
            "appearing in validators_if_changed (e.g., `uv run python scripts/X.py`)."
        ),
        "suggested_fix": (
            "Either fix the path to point at the renamed/moved file, remove the entry/list element, "
            "or restore the missing file."
        ),
        "read_first": [
            "ai_context/file_policy.py",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_file_policy.py",
        "likely_fix_shape": (
            "Update the path to the current location, or remove the dangling entry."
        ),
        "next_checks": [
            "uv run python scripts/validate_file_policy.py",
        ],
        "stop_widening_condition": ("Stop once every referenced path resolves on disk."),
    },
    "file_policy.zone_pattern_drift": {
        "meaning": (
            "A FILE_POLICY_INDEX entry declares an edit_zone that contradicts the zone an "
            "EDIT_ZONES pattern would assign to the same path. After this drift exists, "
            "before-edit (FILE_POLICY-based) and workset/file_impact (EDIT_ZONES-based) would "
            "disagree about the file's edit zone. The two sources must agree, or the EDIT_ZONES "
            "pattern must not match the file at all (FILE_POLICY-only paths are allowed)."
        ),
        "suggested_fix": (
            "Either change the entry's edit_zone to match the EDIT_ZONES pattern zone, or "
            "remove the conflicting EDIT_ZONES pattern, or narrow the pattern so it no longer "
            "matches this path."
        ),
        "read_first": [
            "ai_context/file_policy.py",
            "ai_context/constants.py",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_file_policy.py",
        "likely_fix_shape": (
            "Align entry.edit_zone with EDIT_ZONES pattern membership, or stop the pattern "
            "from matching this file."
        ),
        "next_checks": [
            "uv run python scripts/validate_file_policy.py",
        ],
        "stop_widening_condition": (
            "Stop once every entry's edit_zone is consistent with EDIT_ZONES pattern membership "
            "or the entry's path is not matched by any EDIT_ZONES pattern."
        ),
    },
}


@dataclass(slots=True)
class FilePolicyIssue:
    # ATTRIBUTE: rule_id (str)
    # SUMMARY: Stable rule identifier consumable by query_ai_context.py failure rule.
    rule_id: str

    # ATTRIBUTE: entry_key (str)
    # SUMMARY: FILE_POLICY_INDEX key (path) where the issue was found.
    entry_key: str

    # ATTRIBUTE: field (str)
    # SUMMARY: Field of the entry that failed (or "<entry>" when the issue is the entry key itself).
    field: str

    # ATTRIBUTE: message (str)
    # SUMMARY: Human-readable description.
    message: str

    # ATTRIBUTE: severity (str)
    # SUMMARY: 'error' (blocks gate), 'warning' (visible but non-blocking), or 'info' (purely informational).
    severity: str = "error"


# FUNCTION: get_file_policy_rule_playbook
# SUMMARY: Return a copy of the playbook for a file_policy rule_id, or None if unknown.
def get_file_policy_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _FILE_POLICY_RULE_PLAYBOOKS.get(rule_id)
    if playbook is None:
        return None
    return dict(playbook)


# FUNCTION: _extract_script_path
# SUMMARY: Extract a 'scripts/<file>.py' path from a 'uv run python scripts/<file>.py [...]' command, if present.
# OUTPUT: (str | None): The relative path to the script, or None when the command does not reference one.
def _extract_script_path(command: str) -> str | None:
    match = re.search(r"\b(scripts/[^\s]+\.py)\b", command)
    if match:
        return match.group(1)
    return None


# FUNCTION: collect_file_policy_issues
# SUMMARY: Validate the FILE_POLICY_INDEX (or an override) against schema invariants.
# INPUT: edit_zones_override (dict[str, list[str]] | None): Optional override of EDIT_ZONES used for zone-drift detection (tests only).
# OUTPUT: (list[FilePolicyIssue]): All issues found. Empty list means the index is clean.
def collect_file_policy_issues(
    root_dir: Path,
    index_override: dict[str, dict[str, object]] | None = None,
    edit_zones_override: dict[str, list[str]] | None = None,
) -> list[FilePolicyIssue]:
    # Local import breaks the circular dependency: ai_query.common imports from this
    # module (get_file_policy_rule_playbook) at module load, so we cannot import the
    # zone helper at top level. Doing it lazily here is safe — by the time
    # collect_file_policy_issues is called, ai_query.common is fully initialized.
    from ai_query.common import zone_via_edit_zones_patterns

    index = index_override if index_override is not None else FILE_POLICY_INDEX
    edit_zones = edit_zones_override if edit_zones_override is not None else EDIT_ZONES
    issues: list[FilePolicyIssue] = []

    for entry_key, metadata in index.items():
        # **LOGIC_STEP**: Required fields present.
        for field_name in sorted(REQUIRED_FIELDS):
            if field_name not in metadata:
                issues.append(
                    FilePolicyIssue(
                        rule_id="file_policy.missing_field",
                        entry_key=entry_key,
                        field=field_name,
                        message=f"FILE_POLICY entry '{entry_key}' is missing required field '{field_name}'.",
                    )
                )

        # **LOGIC_STEP**: edit_zone valid.
        if "edit_zone" in metadata:
            zone = metadata["edit_zone"]
            if not isinstance(zone, str) or zone not in ZONE_RISK:
                issues.append(
                    FilePolicyIssue(
                        rule_id="file_policy.invalid_zone",
                        entry_key=entry_key,
                        field="edit_zone",
                        message=(
                            f"FILE_POLICY entry '{entry_key}' has invalid edit_zone "
                            f"{zone!r}. Must be one of: {', '.join(sorted(ZONE_RISK.keys()))}."
                        ),
                    )
                )
            elif isinstance(zone, str) and zone in ZONE_RISK:
                # **LOGIC_STEP**: edit_zone consistent with EDIT_ZONES pattern membership.
                pattern_lookup = zone_via_edit_zones_patterns(entry_key, edit_zones)
                pattern_zone = pattern_lookup["zone"]
                if pattern_zone != "unclassified" and pattern_zone != zone:
                    issues.append(
                        FilePolicyIssue(
                            rule_id="file_policy.zone_pattern_drift",
                            entry_key=entry_key,
                            field="edit_zone",
                            message=(
                                f"FILE_POLICY entry '{entry_key}' has edit_zone={zone!r} but "
                                f"EDIT_ZONES pattern lookup classifies it as "
                                f"{pattern_zone!r}. Two sources of truth disagree — align them."
                            ),
                        )
                    )

        # **LOGIC_STEP**: Entry key path exists on disk.
        if not (root_dir / entry_key).exists():
            issues.append(
                FilePolicyIssue(
                    rule_id="file_policy.broken_path",
                    entry_key=entry_key,
                    field="<entry>",
                    message=f"FILE_POLICY entry key '{entry_key}' does not exist on disk.",
                )
            )

        # **LOGIC_STEP**: generated_artifacts paths exist.
        artifacts = metadata.get("generated_artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if not isinstance(artifact, str):
                    continue
                if not (root_dir / artifact).exists():
                    issues.append(
                        FilePolicyIssue(
                            rule_id="file_policy.broken_path",
                            entry_key=entry_key,
                            field="generated_artifacts",
                            message=(
                                f"FILE_POLICY entry '{entry_key}' references missing "
                                f"generated artifact '{artifact}'."
                            ),
                        )
                    )

        # **LOGIC_STEP**: Script paths inside validators_if_changed exist.
        validators = metadata.get("validators_if_changed")
        if isinstance(validators, list):
            for command in validators:
                if not isinstance(command, str):
                    continue
                script_path = _extract_script_path(command)
                if script_path is None:
                    continue
                if not (root_dir / script_path).exists():
                    issues.append(
                        FilePolicyIssue(
                            rule_id="file_policy.broken_path",
                            entry_key=entry_key,
                            field="validators_if_changed",
                            message=(
                                f"FILE_POLICY entry '{entry_key}' references missing "
                                f"validator script '{script_path}'."
                            ),
                        )
                    )

    return issues


# FUNCTION: _issue_to_payload
# SUMMARY: Convert a FilePolicyIssue into a JSON-serializable payload with playbook hints.
def _issue_to_payload(issue: FilePolicyIssue) -> dict[str, object]:
    # **LOGIC_STEP**: FilePolicyIssue has no native file/line — entry_key is the closest
    # identifying location. Map it into the canon's `file` field with a neutral `line=1`
    # while keeping entry_key/field/severity in the payload (asdict) unchanged.
    playbook = get_file_policy_rule_playbook(issue.rule_id)
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category="file_policy",
        file=issue.entry_key,
        line=1,
        message=issue.message,
        severity=issue.severity,
        playbook=playbook,
        extra=asdict(issue),
    )


# FUNCTION: main
# SUMMARY: Validate the FILE_POLICY_INDEX and return a process exit code.
# OUTPUT: (int): Zero when the index is clean, non-zero on detected issues.
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate ai_context.file_policy.FILE_POLICY_INDEX schema invariants."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured machine-readable validation results.",
    )
    args = parser.parse_args()

    issues = collect_file_policy_issues(ROOT_DIR)
    if not issues:
        if args.json:
            print(render_json({"status": "ok", "issues": []}), end="")
        else:
            print("FILE_POLICY_INDEX validation passed.")
        return 0

    if args.json:
        print(
            render_json(
                {
                    "status": "error",
                    "issues": [_issue_to_payload(issue) for issue in issues],
                }
            ),
            end="",
        )
        return 1

    for issue in issues:
        print(f"{issue.entry_key}: [{issue.rule_id}] [{issue.field}] {issue.message}")
    print(f"\n{len(issues)} issue(s) found.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
