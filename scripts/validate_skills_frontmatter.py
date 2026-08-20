#!/usr/bin/env python3
# FILE: validate_skills_frontmatter.py
# SUMMARY: Validate YAML frontmatter in skills and Claude Code commands for required fields and path correctness.

from __future__ import annotations

import argparse
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from ai_context.rendering import render_json
from ai_context.validator_contract import build_validator_issue_payload

ROOT_DIR = Path(__file__).resolve().parent.parent

SKILL_DIRS = [
    ROOT_DIR / ".agents" / "skills",
]

COMMAND_DIRS: list[Path] = [
    # .claude/commands/ are generated from .agents/skills/ and have their
    # frontmatter stripped. Validate only the source skills.
]

VALID_COMMAND_PREFIXES = ("make ", "uv run")


# ATTRIBUTE: _SKILLS_FRONTMATTER_RULE_PLAYBOOKS (dict[str, dict[str, object]])
# SUMMARY: Stable rule_id → playbook mapping for skills frontmatter validator failures.
_SKILLS_FRONTMATTER_RULE_PLAYBOOKS: dict[str, dict[str, object]] = {
    "skills_frontmatter.missing_frontmatter": {
        "meaning": (
            "A skill's SKILL.md does not start with a YAML frontmatter block delimited by --- markers."
        ),
        "suggested_fix": (
            "Add a YAML frontmatter block at the top of SKILL.md with at least 'name', "
            "'description', and 'triggers' fields."
        ),
        "read_first": [
            ".agents/skills/",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_skills_frontmatter.py",
        "likely_fix_shape": ("Prepend a `---\\n<fields>\\n---` block before the skill body."),
        "next_checks": [
            "uv run python scripts/validate_skills_frontmatter.py",
        ],
        "stop_widening_condition": ("Stop once every SKILL.md has a parseable frontmatter block."),
    },
    "skills_frontmatter.missing_field": {
        "meaning": (
            "A required frontmatter field (e.g., name, description, triggers) is missing or empty."
        ),
        "suggested_fix": (
            "Add the missing field to the frontmatter. Required: name, description, "
            "triggers (must be a non-empty list)."
        ),
        "read_first": [
            ".agents/skills/",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_skills_frontmatter.py",
        "likely_fix_shape": ("Insert the named field with a non-empty value or list."),
        "next_checks": [
            "uv run python scripts/validate_skills_frontmatter.py",
        ],
        "stop_widening_condition": ("Stop once every required field is present and non-empty."),
    },
    "skills_frontmatter.invalid_value": {
        "meaning": (
            "A frontmatter field has a value that does not match the expected format (e.g., "
            "validation_command does not start with `make ` or `uv run`)."
        ),
        "suggested_fix": ("Replace the invalid value with one matching the documented format."),
        "read_first": [
            ".agents/skills/",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_skills_frontmatter.py",
        "likely_fix_shape": (
            "Set validation_command to a value starting with `make ` or `uv run`."
        ),
        "next_checks": [
            "uv run python scripts/validate_skills_frontmatter.py",
        ],
        "stop_widening_condition": ("Stop once every value matches the expected format."),
    },
    "skills_frontmatter.broken_path": {
        "meaning": (
            "A path listed in minimal_read_set does not exist on disk relative to the repo root."
        ),
        "suggested_fix": (
            "Either fix the path to point at an existing file, or remove the entry from minimal_read_set."
        ),
        "read_first": [
            ".agents/skills/",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_skills_frontmatter.py",
        "likely_fix_shape": (
            "Update the minimal_read_set entry to a real repo-relative path or delete it."
        ),
        "next_checks": [
            "uv run python scripts/validate_skills_frontmatter.py",
        ],
        "stop_widening_condition": (
            "Stop once every minimal_read_set entry resolves to an existing file."
        ),
    },
}


@dataclass(slots=True)
class SkillFrontmatterIssue:
    # ATTRIBUTE: rule_id (str)
    # SUMMARY: Stable rule identifier consumable by query_ai_context.py failure rule.
    rule_id: str

    # ATTRIBUTE: path (Path)
    # SUMMARY: File containing the frontmatter issue.
    path: Path

    # ATTRIBUTE: field (str)
    # SUMMARY: Frontmatter field that failed validation.
    field: str

    # ATTRIBUTE: message (str)
    # SUMMARY: Human-readable description of the issue.
    message: str

    # ATTRIBUTE: severity (str)
    # SUMMARY: 'error' (blocks gate), 'warning' (visible but non-blocking), or 'info' (purely informational).
    severity: str = "error"


# FUNCTION: get_skills_frontmatter_rule_playbook
# SUMMARY: Return a copy of the playbook for a skills frontmatter rule_id, or None if unknown.
def get_skills_frontmatter_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _SKILLS_FRONTMATTER_RULE_PLAYBOOKS.get(rule_id)
    if playbook is None:
        return None
    return dict(playbook)


def _parse_frontmatter(text: str) -> dict[str, object] | None:
    """Extract YAML frontmatter from markdown text. Returns None if no frontmatter found."""
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    raw = text[3:end].strip()
    result: dict[str, object] = {}
    current_key: str | None = None
    current_list: list[str] = []
    for line in raw.splitlines():
        # **LOGIC_STEP**: Handle inline list syntax: key: [val1, val2]
        inline_match = re.match(r"^(\w[\w_]*)\s*:\s*\[(.+)\]\s*$", line)
        if inline_match:
            if current_key and current_list:
                result[current_key] = current_list
                current_list = []
            key = inline_match.group(1)
            values = [v.strip().strip("'\"") for v in inline_match.group(2).split(",")]
            result[key] = values
            current_key = None
            continue
        # **LOGIC_STEP**: Handle scalar key: value
        scalar_match = re.match(r"^(\w[\w_]*)\s*:\s*(.+)$", line)
        if scalar_match and not line.strip().endswith(":"):
            if current_key and current_list:
                result[current_key] = current_list
                current_list = []
            result[scalar_match.group(1)] = scalar_match.group(2).strip()
            current_key = None
            continue
        # **LOGIC_STEP**: Handle list header: key:
        list_header = re.match(r"^(\w[\w_]*)\s*:\s*$", line)
        if list_header:
            if current_key and current_list:
                result[current_key] = current_list
            current_key = list_header.group(1)
            current_list = []
            continue
        # **LOGIC_STEP**: Handle list item: - value
        item_match = re.match(r"^\s+-\s+(.+)$", line)
        if item_match and current_key:
            current_list.append(item_match.group(1).strip())
            continue
    if current_key and current_list:
        result[current_key] = current_list
    return result


def _validate_file(path: Path, is_skill: bool) -> list[SkillFrontmatterIssue]:
    """Validate a single skill or command file."""
    issues: list[SkillFrontmatterIssue] = []
    text = path.read_text(encoding="utf-8")

    fm = _parse_frontmatter(text)
    if fm is None:
        issues.append(
            SkillFrontmatterIssue(
                rule_id="skills_frontmatter.missing_frontmatter",
                path=path,
                field="frontmatter",
                message="Missing YAML frontmatter.",
            )
        )
        return issues

    if is_skill:
        if "name" not in fm:
            issues.append(
                SkillFrontmatterIssue(
                    rule_id="skills_frontmatter.missing_field",
                    path=path,
                    field="name",
                    message="Missing required field 'name'.",
                )
            )
        if "description" not in fm:
            issues.append(
                SkillFrontmatterIssue(
                    rule_id="skills_frontmatter.missing_field",
                    path=path,
                    field="description",
                    message="Missing required field 'description'.",
                )
            )

    triggers = fm.get("triggers")
    if not isinstance(triggers, list) or len(triggers) == 0:
        issues.append(
            SkillFrontmatterIssue(
                rule_id="skills_frontmatter.missing_field",
                path=path,
                field="triggers",
                message="Field 'triggers' must be a non-empty list.",
            )
        )

    minimal_read_set = fm.get("minimal_read_set")
    if isinstance(minimal_read_set, list):
        for entry in minimal_read_set:
            entry_path = ROOT_DIR / entry
            if not entry_path.exists():
                issues.append(
                    SkillFrontmatterIssue(
                        rule_id="skills_frontmatter.broken_path",
                        path=path,
                        field="minimal_read_set",
                        message=f"Path does not exist: {entry}",
                    )
                )

    validation_command = fm.get("validation_command")
    if isinstance(validation_command, str):
        if not any(validation_command.startswith(prefix) for prefix in VALID_COMMAND_PREFIXES):
            issues.append(
                SkillFrontmatterIssue(
                    rule_id="skills_frontmatter.invalid_value",
                    path=path,
                    field="validation_command",
                    message=f"validation_command must start with one of: {', '.join(VALID_COMMAND_PREFIXES)}",
                )
            )

    return issues


def collect_skills_frontmatter_issues(root_dir: Path) -> list[SkillFrontmatterIssue]:
    """Scan .agents/skills/ and .claude/commands/ for frontmatter issues."""
    del root_dir  # SKILL_DIRS / COMMAND_DIRS are absolute via module-level ROOT_DIR.
    issues: list[SkillFrontmatterIssue] = []

    for skill_dir in SKILL_DIRS:
        if not skill_dir.exists():
            continue
        for skill_file in sorted(skill_dir.rglob("SKILL.md")):
            issues.extend(_validate_file(skill_file, is_skill=True))

    for cmd_dir in COMMAND_DIRS:
        if not cmd_dir.exists():
            continue
        for cmd_file in sorted(cmd_dir.glob("*.md")):
            issues.extend(_validate_file(cmd_file, is_skill=False))

    return issues


# FUNCTION: _issue_to_payload
# SUMMARY: Convert a SkillFrontmatterIssue into a JSON-serializable payload with playbook hints.
def _issue_to_payload(issue: SkillFrontmatterIssue) -> dict[str, object]:
    # **LOGIC_STEP**: SkillFrontmatterIssue names its file field path (not file) and has no line
    # concept — map path into the canon's `file` field with a neutral `line=1` while keeping
    # path/field/severity (asdict) unchanged.
    relative_path = (
        str(issue.path.relative_to(ROOT_DIR)) if issue.path.is_absolute() else str(issue.path)
    )
    extra: dict[str, object] = asdict(issue)
    extra["path"] = relative_path
    playbook = get_skills_frontmatter_rule_playbook(issue.rule_id)
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category="skills_frontmatter",
        file=relative_path,
        line=1,
        message=issue.message,
        severity=issue.severity,
        playbook=playbook,
        extra=extra,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate .agents/skills/*/SKILL.md frontmatter for required fields and path correctness."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured machine-readable validation results.",
    )
    args = parser.parse_args()

    issues = collect_skills_frontmatter_issues(ROOT_DIR)
    if not issues:
        if args.json:
            print(render_json({"status": "ok", "issues": []}), end="")
        else:
            print("Skills frontmatter validation passed.")
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
        rel = issue.path.relative_to(ROOT_DIR)
        print(f"{rel}: [{issue.rule_id}] [{issue.field}] {issue.message}")
    print(f"\n{len(issues)} issue(s) found.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
