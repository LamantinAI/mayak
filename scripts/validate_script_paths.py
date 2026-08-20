#!/usr/bin/env python3
# FILE: validate_script_paths.py
# SUMMARY: Walk scripts/*.py for path-shaped string literals (tests/X, project/Y, ...) and assert each exists on disk.

from __future__ import annotations

import argparse
import ast
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from ai_context.rendering import render_json
from ai_context.validator_contract import build_validator_issue_payload

ROOT_DIR = Path(__file__).resolve().parent.parent

# ATTRIBUTE: _SCAN_PREFIXES (tuple[str, ...])
# SUMMARY: Top-level directories whose path-shaped literals inside scripts/*.py are checked for on-disk existence.
_SCAN_PREFIXES: tuple[str, ...] = (
    "tests/",
    "project/",
    "docs/",
    "alembic/",
    ".agents/skills/",
    "ai_context/",
    "ai_query/",
    ".github/",
    ".githooks/",
)

# ATTRIBUTE: _PATH_PATTERN (re.Pattern)
# SUMMARY: Match a path-shaped string literal. Anchored to the start to avoid false positives on substrings.
_PATH_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:tests|project|docs|alembic|skills|ai_context|ai_query|\.github|\.githooks)/[\w./\-]+$"
)

# ATTRIBUTE: _ALLOWLIST (frozenset[str])
# SUMMARY: Path-shaped literals that intentionally do not need to exist on disk.
# Each entry is the literal exactly as it appears in scripts/*.py source.
# Add entries sparingly with a comment explaining why the literal is legitimate.
_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Path prefix used by validate_runtime_ownership._ENV_ALLOWLIST_PREFIXES — not a real file.
        # It is intentionally just a prefix (string startswith match), covering both the legacy
        # `project/core/config.py` and any future split into `project/core/config/`.
        "project/core/config",
        # (Removed 2026-05-10 by docs-drift bundle: project/domain/ports.py now exists
        # as a real file with a canonical LLMPort Protocol — no longer needs an allowlist.)
    }
)


# ATTRIBUTE: _SCRIPT_PATHS_RULE_PLAYBOOKS (dict[str, dict[str, object]])
# SUMMARY: Stable rule_id → playbook mapping for path-reference failures inside scripts/*.py.
_SCRIPT_PATHS_RULE_PLAYBOOKS: dict[str, dict[str, object]] = {
    "script_paths.broken_reference": {
        "meaning": (
            "A string literal inside a scripts/*.py module references a path that does not "
            "exist on disk. Common cause: a directory or file was renamed or deleted but the "
            "string literal in tooling code was not updated."
        ),
        "suggested_fix": (
            "Either fix the literal to the new path, remove the reference if the path was "
            "intentionally deleted, or add the literal to the allowlist in "
            "scripts/validate_script_paths.py if it is genuinely a non-path-shaped string."
        ),
        "read_first": [
            "scripts/",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_script_paths.py",
        "likely_fix_shape": (
            "Rename the literal to its new on-disk location, or delete the reference."
        ),
        "next_checks": [
            "uv run python scripts/validate_script_paths.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop once every path-shaped literal in scripts/*.py resolves on disk."
        ),
    },
}


@dataclass(slots=True)
class ScriptPathIssue:
    # ATTRIBUTE: rule_id (str)
    # SUMMARY: Stable rule identifier consumable by query_ai_context.py failure rule.
    rule_id: str

    # ATTRIBUTE: source_file (str)
    # SUMMARY: Repo-relative path to the script that contains the broken literal.
    source_file: str

    # ATTRIBUTE: line (int)
    # SUMMARY: Line number of the offending literal.
    line: int

    # ATTRIBUTE: literal (str)
    # SUMMARY: The literal string that does not resolve on disk.
    literal: str

    # ATTRIBUTE: message (str)
    # SUMMARY: Human-readable description of the issue.
    message: str

    # ATTRIBUTE: severity (str)
    # SUMMARY: 'error' (blocks gate), 'warning', or 'info'.
    severity: str = "error"


# FUNCTION: get_script_paths_rule_playbook
# SUMMARY: Return a copy of the playbook for a script_paths rule_id, or None if unknown.
def get_script_paths_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _SCRIPT_PATHS_RULE_PLAYBOOKS.get(rule_id)
    if playbook is None:
        return None
    return dict(playbook)


# FUNCTION: _is_path_shaped
# SUMMARY: Decide whether a string literal looks like a repo-relative path that should resolve on disk.
# OUTPUT: (bool): True when the literal matches the path pattern.
def _is_path_shaped(literal: str) -> bool:
    if not literal:
        return False
    if literal in _ALLOWLIST:
        return False
    if not literal.startswith(_SCAN_PREFIXES):
        return False
    return bool(_PATH_PATTERN.match(literal))


# FUNCTION: _extract_string_literals
# SUMMARY: Yield (literal, line) pairs for all string constants in a Python source file.
# INPUT: source_path (Path): Absolute path to the source file.
# OUTPUT: (list[tuple[str, int]]): List of literal/line pairs.
def _extract_string_literals(source_path: Path) -> list[tuple[str, int]]:
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    except SyntaxError:
        return []
    literals: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append((node.value, getattr(node, "lineno", 1)))
    return literals


# FUNCTION: collect_script_path_issues
# SUMMARY: Walk scripts/*.py and return issues for path-shaped literals that do not resolve on disk.
# INPUT: scripts_dir (Path | None): Optional override of the scripts dir scanned (used in tests).
# OUTPUT: (list[ScriptPathIssue]): All issues found. Empty list means scripts/ is clean.
def collect_script_path_issues(
    root_dir: Path,
    scripts_dir: Path | None = None,
) -> list[ScriptPathIssue]:
    base = scripts_dir if scripts_dir is not None else (root_dir / "scripts")
    if not base.exists():
        return []
    issues: list[ScriptPathIssue] = []
    own_path = Path(__file__).resolve()
    for source_path in sorted(base.rglob("*.py")):
        if source_path.resolve() == own_path:
            # Skip the validator's own source so its allowlist / regex literals are not self-flagged.
            continue
        for literal, line in _extract_string_literals(source_path):
            if not _is_path_shaped(literal):
                continue
            target = root_dir / literal
            if target.exists():
                continue
            relative = source_path.relative_to(root_dir).as_posix()
            issues.append(
                ScriptPathIssue(
                    rule_id="script_paths.broken_reference",
                    source_file=relative,
                    line=line,
                    literal=literal,
                    message=(
                        f"{relative}:{line} references missing path '{literal}'. "
                        "Either fix the path, remove the reference, or add it to the allowlist."
                    ),
                )
            )
    return issues


# FUNCTION: _issue_to_payload
# SUMMARY: Convert a ScriptPathIssue into a JSON-serializable payload with playbook hints.
def _issue_to_payload(issue: ScriptPathIssue) -> dict[str, object]:
    # **LOGIC_STEP**: ScriptPathIssue names its file field source_file (not file) — map it into
    # the canon's `file` field while keeping source_file/literal/severity (asdict) unchanged.
    playbook = get_script_paths_rule_playbook(issue.rule_id)
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category="script_paths",
        file=issue.source_file,
        line=issue.line,
        message=issue.message,
        severity=issue.severity,
        playbook=playbook,
        extra=asdict(issue),
    )


# FUNCTION: main
# SUMMARY: Run validate_script_paths and return a process exit code.
# OUTPUT: (int): Zero when scripts/*.py is clean, non-zero on detected broken references.
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate that path-shaped string literals inside scripts/*.py resolve on disk."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured machine-readable validation results.",
    )
    # **LOGIC_STEP**: Default to an empty argv when called directly from tests so that
    # pytest's own argv does not leak into argparse via sys.argv[1:].
    args = parser.parse_args([] if argv is None else argv)

    issues = collect_script_path_issues(ROOT_DIR)
    if not issues:
        if args.json:
            print(render_json({"status": "ok", "issues": []}), end="")
        else:
            print("Script path references validation passed.")
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
        print(f"{issue.source_file}:{issue.line}: [{issue.rule_id}] {issue.message}")
    print(f"\n{len(issues)} issue(s) found.")
    return 1


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
