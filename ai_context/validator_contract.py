# FILE: validator_contract.py
# SUMMARY: Canonical error-payload contract shared by every scripts/validate_*.py JSON converter, so `stop_widening_condition` (and the rest of the remediation guidance already present in each validator's internal rule playbook) reaches the actual `--json` output instead of being dropped by ad-hoc per-validator dict literals.

from __future__ import annotations

from typing import TypedDict


# CLASS: ai_context.validator_contract.ValidatorIssuePayload
# SUMMARY: Minimum required shape of a single validator issue in `--json` output. Individual validators may add extra keys (e.g. migrations' `command_name`/`returncode`/`stderr`, file_policy's `entry_key`/`field`) — this TypedDict documents the floor every converter must guarantee, not a closed schema.
class ValidatorIssuePayload(TypedDict):
    # ATTRIBUTE: rule_id (str)
    # SUMMARY: Stable rule identifier consumed by query_ai_context.py failure rule and agent tooling.
    rule_id: str

    # ATTRIBUTE: category (str)
    # SUMMARY: Top-level issue category (e.g. "architecture", "cbm", "runtime_ownership").
    category: str

    # ATTRIBUTE: file (str)
    # SUMMARY: Repo-relative path most closely identifying the issue location. For validators whose
    # native issue shape has no source file (file_policy's entry_key, migrations' command_name,
    # project_context's field path), this is a best-effort mapping — the original field is preserved
    # unchanged alongside it.
    file: str

    # ATTRIBUTE: line (int)
    # SUMMARY: 1-based line number, or 1 as a neutral placeholder when the issue has no line concept.
    line: int

    # ATTRIBUTE: message (str)
    # SUMMARY: Human-readable description of the issue.
    message: str

    # ATTRIBUTE: severity (str)
    # SUMMARY: 'error' (blocks gate), 'warning' (visible but non-blocking), or 'info'.
    severity: str

    # ATTRIBUTE: suggested_fix (str)
    # SUMMARY: Actionable remediation instruction for this specific issue.
    suggested_fix: str

    # ATTRIBUTE: read_first (list[str])
    # SUMMARY: Ordered list of files/paths an agent should read before attempting a fix.
    read_first: list[str]

    # ATTRIBUTE: next_commands (list[str])
    # SUMMARY: Ordered list of commands to run after applying the fix, to confirm resolution.
    next_commands: list[str]

    # ATTRIBUTE: stop_widening_condition (str)
    # SUMMARY: The condition under which the agent should stop expanding the diff — prevents runaway
    # scope creep once the specific rule is satisfied.
    stop_widening_condition: str


# FUNCTION: build_validator_issue_payload
# SUMMARY: Assemble a validator issue payload guaranteed to satisfy ValidatorIssuePayload's required keys, sourcing suggested_fix/read_first/next_commands/stop_widening_condition from the validator's own rule-playbook dict so every JSON converter surfaces the same remediation guidance already available to query_ai_context.py failure rule.
# INPUT: file (str): Repo-relative path (or best-effort mapping) identifying the issue location.
# INPUT: line (int): 1-based line number, or 1 when the issue has no line concept.
# INPUT: playbook (dict[str, object] | None): The validator's own rule-playbook dict for this rule_id (e.g. from get_architecture_rule_playbook), or None when no playbook is registered for the rule_id.
# INPUT: severity (str): 'error', 'warning', or 'info'. Defaults to 'error' to match every validator's existing default for issues that carry no explicit severity field.
# INPUT: extra (dict[str, object] | None): Validator-specific additional fields to merge in without overwriting the canon keys above (e.g. entry_key, command_name, field, source_file, path, line_count, limit, returncode, stderr).
# OUTPUT: (dict[str, object]): Plain dict satisfying ValidatorIssuePayload's required keys — the canon block is annotated as that TypedDict, so mypy enforces the floor — plus any extra fields, ready for JSON serialization.
def build_validator_issue_payload(
    *,
    rule_id: str,
    category: str,
    file: str,
    line: int,
    message: str,
    playbook: dict[str, object] | None,
    severity: str = "error",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    suggested_fix = ""
    read_first: list[str] = []
    next_commands: list[str] = []
    stop_widening_condition = ""

    if playbook is not None:
        # **LOGIC_STEP**: suggested_fix falls back to likely_fix_shape for playbooks that only
        # describe the fix shape (e.g. runtime_ownership's env_access_restricted entry has no
        # explicit suggested_fix key today) so no validator regresses to an empty string.
        raw_suggested_fix = playbook.get("suggested_fix") or playbook.get("likely_fix_shape")
        if isinstance(raw_suggested_fix, str):
            suggested_fix = raw_suggested_fix

        raw_read_first = playbook.get("read_first")
        if isinstance(raw_read_first, (list, tuple)):
            read_first = list(raw_read_first)

        # **LOGIC_STEP**: Internal playbooks name this key next_checks; a few external call sites
        # may already use next_commands — accept either without preferring one over the other.
        raw_next_commands = playbook.get("next_checks")
        if raw_next_commands is None:
            raw_next_commands = playbook.get("next_commands")
        if isinstance(raw_next_commands, (list, tuple)):
            next_commands = list(raw_next_commands)

        raw_stop_widening = playbook.get("stop_widening_condition")
        if isinstance(raw_stop_widening, str):
            stop_widening_condition = raw_stop_widening

    # **LOGIC_STEP**: Annotated as the TypedDict rather than dict[str, object] so mypy checks the
    # canon keys against the contract declared above. Built as a plain dict the class was
    # documentation only: dropping a required key here type-checked cleanly and every validator's
    # --json output silently lost a field the agent tooling reads.
    canonical: ValidatorIssuePayload = {
        "rule_id": rule_id,
        "category": category,
        "file": file,
        "line": line,
        "message": message,
        "severity": severity,
        "suggested_fix": suggested_fix,
        "read_first": read_first,
        "next_commands": next_commands,
        "stop_widening_condition": stop_widening_condition,
    }
    # **LOGIC_STEP**: Widened before merging extras — a TypedDict is closed, and validators
    # legitimately add their own keys (entry_key, command_name, returncode).
    payload: dict[str, object] = dict(canonical)

    if extra:
        for key, value in extra.items():
            if key not in payload:
                payload[key] = value

    return payload
