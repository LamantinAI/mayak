# FILE: validate_cbm.py
# SUMMARY: Check that every Python file under project/ opens with its header — `# FILE:` and `# SUMMARY:`.
# The header is all that is left of Code-Base Markup: tags on classes, functions and attributes were
# removed on 2026-09-24, and whether the header itself earns its two lines is measured by bench3 —
# docs/adr/ADR-001-pragmatic-cbm.md.

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from validation_support.validator_contract import build_validator_issue_payload


@dataclass(slots=True)
class ValidationIssue:
    path: Path
    line: int  # 1-based
    message: str


def _is_production_python_file(path: Path) -> bool:
    parts = path.parts
    return len(parts) >= 2 and parts[0] == "project" and path.suffix == ".py"


# The leading run of comment lines, with the `#` stripped; blank lines before it are skipped. The
# header is whatever that run holds, so a SUMMARY continued over several lines is one header.
def _read_file_header_block(lines: list[str]) -> list[str]:
    block: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if block:
                break
            continue
        if not stripped.startswith("#"):
            break
        block.append(stripped[1:].strip())
    return block


def validate_python_source(path: Path) -> list[ValidationIssue]:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        return [
            ValidationIssue(
                path=path,
                line=1,
                message=f"UnicodeDecodeError while reading source: {error.reason}",
            )
        ]
    issues: list[ValidationIssue] = []

    # A module docstring would be a second place for what the header's SUMMARY says.
    if source.lstrip().startswith(('"""', "'''")):
        issues.append(
            ValidationIssue(
                path=path,
                line=1,
                message="Module docstrings are not allowed; the file header carries the summary",
            )
        )

    header = _read_file_header_block(source.splitlines())
    if not any(line.startswith("FILE:") for line in header):
        issues.append(ValidationIssue(path=path, line=1, message="Missing '# FILE:' tag"))
    if not any(line.startswith("SUMMARY:") for line in header):
        issues.append(ValidationIssue(path=path, line=1, message="Missing file '# SUMMARY:' tag"))
    return issues


def default_repo_root() -> Path:
    # This file lives in scripts/, so the root is two levels up. A single .parent pointed the walk
    # at scripts/ itself, where no relative path starts with "project/", so the production-file
    # filter matched nothing and the validator reported success for every input. Guarded by
    # tests/template/test_validate_cbm.py.
    return Path(__file__).resolve().parent.parent


def collect_validation_issues(repo_root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for path in sorted(repo_root.rglob("*.py")):
        relative_path = path.relative_to(repo_root)
        if not _is_production_python_file(relative_path):
            continue
        issues.extend(validate_python_source(path))
    return issues


# Message keyword -> (rule_id, suggested_fix). Classification finds the keyword inside the finished
# message, so each keyword is a second copy of wording the validator builds above;
# tests/template/test_validate_cbm.py compares the two copies.
_CBM_RULE_MAP: list[tuple[str, str, str]] = [
    (
        "Module docstrings are not allowed",
        "cbm.no_module_docstring",
        "Delete the docstring and put the same sentence after '# FILE:' as '# SUMMARY:'.",
    ),
    (
        "Missing '# FILE:'",
        "cbm.missing_file_tag",
        "Open the file and write '# FILE: <its own path>' on line one, '# SUMMARY:' on line two.",
    ),
    (
        "Missing file '# SUMMARY:'",
        "cbm.missing_file_summary",
        "The '# FILE:' line is there but says nothing about the file. Follow it with '# SUMMARY:'.",
    ),
    (
        "UnicodeDecodeError while reading",
        "cbm.read_error",
        "The bytes are not valid UTF-8. Re-save the file in that encoding and run again.",
    ),
]


# Public so doctor_ai_context.py routes an issue to the rule that fired rather than a literal.
def classify_issue(message: str) -> tuple[str, str]:
    for keyword, rule_id, fix in _CBM_RULE_MAP:
        if keyword in message:
            return rule_id, fix
    return (
        "cbm.unknown",
        "No remediation is registered for this message. Read the reported line and, if the "
        "message is a new one, add it to _CBM_RULE_MAP.",
    )


def classify_issue_rule_id(message: str) -> str:
    rule_id, _ = classify_issue(message)
    return rule_id


def _issue_to_json(issue: ValidationIssue, repo_root: Path) -> dict:
    rule_id, suggested_fix = classify_issue(issue.message)
    entry = build_validator_issue_payload(
        rule_id=rule_id,
        category="cbm",
        file=str(issue.path.relative_to(repo_root)),
        line=issue.line,
        message=issue.message,
        playbook=get_cbm_rule_playbook(rule_id),
    )
    entry["suggested_fix"] = suggested_fix
    return entry


# The playbook the doctor answers with; None for anything not in the table.
def get_cbm_rule_playbook(rule_id: str) -> dict | None:
    fix_map = {rid: fix for _keyword, rid, fix in _CBM_RULE_MAP}
    # The prefix alone is not enough: `--rule cbm.this_does_not_exist` would get a confident
    # answer about a rule that does not exist, while every other rule family answers "Unknown
    # failure rule ID". `cbm.unknown` is a real answer from classify_issue, so it keeps its playbook.
    if rule_id not in fix_map and rule_id != "cbm.unknown":
        return None
    return {
        "meaning": (
            "A Python file under project/ does not open with its header: '# FILE: <path>' "
            "followed by '# SUMMARY: <what the file is for>'."
        ),
        "suggested_fix": fix_map.get(rule_id, "Fix the reported file header."),
        "read_first": ["docs/adr/ADR-001-pragmatic-cbm.md"],
        "smallest_command_to_rerun": "uv run python scripts/validate_cbm.py",
        "likely_fix_shape": "Add or complete the two header lines at the top of the reported file.",
        "next_checks": [
            "uv run python scripts/validate_cbm.py",
            "make quality-gates",
        ],
        "stop_widening_condition": "Stop once validate_cbm.py passes with no reported issues.",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that every Python file under project/ opens with its file header."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output with remediation guidance.",
    )
    # An empty argv when called directly from tests, so pytest's own argv cannot leak in.
    args = parser.parse_args([] if argv is None else argv)

    repo_root = default_repo_root()
    issues = collect_validation_issues(repo_root)

    if args.json:
        payload = {
            "status": "ok" if not issues else "error",
            "issues": [_issue_to_json(issue, repo_root) for issue in issues],
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1 if issues else 0

    if not issues:
        print("File headers present.")
        return 0

    for issue in issues:
        print(f"{issue.path.relative_to(repo_root)}:{issue.line}: {issue.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
