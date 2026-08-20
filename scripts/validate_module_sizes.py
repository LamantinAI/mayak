#!/usr/bin/env python3
# FILE: validate_module_sizes.py
# SUMMARY: Enforce a production-module budget measured in executable lines, so documentation never counts against a module's size.
#
# The budget deliberately charges for `code_lines` rather than for raw `splitlines()`, which makes
# writing a CBM comment — or an explanation of why the code looks the way it does — free. The
# measurement that settled this is written once, in `ai_context/line_metrics.py`, next to the code
# that does the classifying; it is not repeated here.

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ai_context.line_metrics import ModuleMetrics, measure_path
from ai_context.validator_contract import build_validator_issue_payload


# ATTRIBUTE: ROOT_DIR (Path)
# SUMMARY: Absolute repository root scanned by the validator.
ROOT_DIR = Path(__file__).resolve().parent.parent

# ATTRIBUTE: MAX_CODE_LINES (int)
# SUMMARY: Maximum executable lines allowed in a production Python module. Comments, docstrings, and blank lines are not counted.
# NOTE: The budget has two axes, and this is the first. Counting executable lines rather than raw
# ones is deliberate — a raw limit taxes the file that explains itself, and the cheapest way under
# such a limit is to delete the explanation. See ai_context/line_metrics.py for the measurement.
MAX_CODE_LINES = 450

# ATTRIBUTE: MAX_LINES (int)
# SUMMARY: Backwards-compatible alias for MAX_CODE_LINES, kept so existing importers keep working.
MAX_LINES = MAX_CODE_LINES

# ATTRIBUTE: MAX_FUNCTION_CODE_LINES (int)
# SUMMARY: Maximum executable lines allowed in a single function or method. The second axis of the budget: a module can stay under its own limit while hiding one function nobody can hold in their head.
# 200 rather than something tighter because the two axes answer different questions. The module
# budget asks whether a file is too much to hold in one's head; this one asks whether a single body
# is. A module can sit comfortably under its own limit while one function inside it accounts for
# nearly all of it, and that shape is what makes an edit unsafe — the reader has no seam to stop at.
# Nothing in this repository is near the limit, so it costs nothing here and catches the shape early
# in a project that grows one.
MAX_FUNCTION_CODE_LINES = 200

# ATTRIBUTE: EXCLUDED_FILES (set[str])
# SUMMARY: Repository-relative files excluded from the module size limit.
# Intentionally empty. Under the raw-line budget this set grew into a backlog of deferred
# refactors, because the cheapest way past a limit that charged for comments was to add an
# exception rather than to split the module. A module that exceeds the executable-line budget is
# genuinely large, so the answer is to split it — not to list it here. Verticals built on this
# kernel may still add an entry when a domain module is legitimately split-resistant; keep each
# entry accompanied by a comment explaining why.
EXCLUDED_FILES: set[str] = set()

# ATTRIBUTE: RULE_ID (str)
# SUMMARY: Stable rule identifier used in JSON output and failure playbooks for oversized modules.
RULE_ID = "size.module_exceeds_limit"

# ATTRIBUTE: FUNCTION_RULE_ID (str)
# SUMMARY: Stable rule identifier for a single function that exceeds the per-function budget.
FUNCTION_RULE_ID = "size.function_exceeds_limit"

# ATTRIBUTE: READ_ERROR_RULE_ID (str)
# SUMMARY: Stable rule identifier emitted when a module cannot be decoded as UTF-8.
READ_ERROR_RULE_ID = "module_size.read_error"


# DATACLASS: validate_module_sizes.ModuleSizeIssue
# SUMMARY: Structured representation of a module-size validation problem (oversize or read failure).
@dataclass(slots=True)
class ModuleSizeIssue:
    # ATTRIBUTE: path (Path)
    # SUMMARY: Module path under validation.
    path: Path

    # ATTRIBUTE: line_count (int)
    # SUMMARY: Executable lines in the module. Zero when the file could not be read.
    line_count: int

    # ATTRIBUTE: rule_id (str)
    # SUMMARY: Stable rule identifier so JSON consumers route to the correct playbook.
    rule_id: str = RULE_ID

    # ATTRIBUTE: message (str | None)
    # SUMMARY: Human-readable diagnostic; populated for read errors so the agent sees the cause.
    message: str | None = None

    # ATTRIBUTE: raw_lines (int)
    # SUMMARY: Physical line count, reported alongside the budget so the agent sees how much of the file is documentation rather than logic.
    raw_lines: int = 0

    # ATTRIBUTE: symbol (str)
    # SUMMARY: Qualified name of the offending function for FUNCTION_RULE_ID; empty for module-level issues.
    symbol: str = ""

    # ATTRIBUTE: line (int)
    # SUMMARY: 1-based line the issue points at — the `def` line for a function issue, 1 for a module issue.
    line: int = 1

    # FUNCTION: validate_module_sizes.ModuleSizeIssue.describe
    # SUMMARY: Render the one-sentence diagnostic for this issue, so every surface — CLI, JSON, doctor — says the same thing instead of each rebuilding its own wording.
    # OUTPUT: (str): Human-readable description including the relevant budget.
    def describe(self) -> str:
        if self.rule_id == READ_ERROR_RULE_ID:
            return f"{self.path}: {self.message or 'read error'} — re-save as UTF-8"
        if self.rule_id == FUNCTION_RULE_ID:
            return (
                f"{self.path}:{self.line}: function `{self.symbol}` has {self.line_count} code "
                f"lines (limit: {MAX_FUNCTION_CODE_LINES}) — split it into named steps"
            )
        return (
            f"{self.path}: {self.line_count} code lines (limit: {MAX_CODE_LINES}, file total "
            f"{self.raw_lines}) — extract into submodules; comments do not count"
        )


# FUNCTION: _is_target_module
# SUMMARY: Determine whether a file should be checked against the production size limit.
# INPUT: path (Path): Repository-relative file path.
# OUTPUT: (bool): True when the file is a production Python module under project/** and not excluded.
def _is_target_module(path: Path) -> bool:
    return (
        len(path.parts) >= 2
        and path.parts[0] == "project"
        and path.suffix == ".py"
        and str(path) not in EXCLUDED_FILES
    )


# FUNCTION: collect_module_size_issues
# SUMMARY: Find production modules whose executable-line count exceeds the budget.
def collect_module_size_issues(root_dir: Path) -> list[ModuleSizeIssue]:
    issues: list[ModuleSizeIssue] = []
    # **LOGIC_STEP**: Walk only project/ instead of the whole repository. rglob from the root also
    # descends into .venv (tens of thousands of files) just to filter them out afterwards.
    for path in sorted((root_dir / "project").rglob("*.py")):
        relative_path = path.relative_to(root_dir)
        if not _is_target_module(relative_path):
            continue
        metrics = measure_path(path)
        # **LOGIC_STEP**: A file the tooling cannot decode surfaces as a structured issue rather
        # than a raw UnicodeDecodeError, so the agent loop sees a normal rule_id payload.
        if metrics.detail.startswith("UnicodeDecodeError"):
            issues.append(
                ModuleSizeIssue(
                    path=relative_path,
                    line_count=0,
                    rule_id=READ_ERROR_RULE_ID,
                    message=metrics.detail,
                )
            )
            continue
        if metrics.code_lines > MAX_CODE_LINES:
            issues.append(
                ModuleSizeIssue(
                    path=relative_path,
                    line_count=metrics.code_lines,
                    raw_lines=metrics.raw_lines,
                )
            )
        # **LOGIC_STEP**: The second axis is checked independently, not only on the longest
        # function, because a module can hold several oversized ones and reporting them one
        # release at a time would turn a single refactor into a queue of gate failures.
        for span in metrics.functions:
            if span.code_lines > MAX_FUNCTION_CODE_LINES:
                issues.append(
                    ModuleSizeIssue(
                        path=relative_path,
                        line_count=span.code_lines,
                        rule_id=FUNCTION_RULE_ID,
                        raw_lines=metrics.raw_lines,
                        symbol=span.qualified_name,
                        line=span.line,
                    )
                )
    return issues


# FUNCTION: collect_module_metrics
# SUMMARY: Measure every production module without applying the budget, for a caller that wants the code-versus-documentation split rather than a pass/fail verdict.
# NOTE: The surface this was written for — `query_ai_context.py metrics` — was deleted on
# 2026-08-14, so today the only caller is this module's own test file. Kept rather than removed
# because the split it reports is the answer to "why is this module over budget when half of it is
# comments"; delete it, and the seven tests that pin the measurement, if nothing calls it by 2026-10.
# OUTPUT: (dict[str, ModuleMetrics]): Metrics keyed by repository-relative path.
def collect_module_metrics(root_dir: Path) -> dict[str, ModuleMetrics]:
    measured: dict[str, ModuleMetrics] = {}
    for path in sorted((root_dir / "project").rglob("*.py")):
        relative_path = path.relative_to(root_dir)
        if not _is_target_module(relative_path):
            continue
        measured[str(relative_path)] = measure_path(path)
    return measured


# FUNCTION: _issue_to_json
# SUMMARY: Convert a ModuleSizeIssue to a JSON-serialisable dict with remediation guidance.
# OUTPUT: (dict): JSON-friendly dict with rule_id, file, line_count, limit, and guidance fields.
def _issue_to_json(issue: ModuleSizeIssue) -> dict:
    playbook = get_module_size_playbook(issue.rule_id)
    if issue.rule_id == READ_ERROR_RULE_ID:
        entry = build_validator_issue_payload(
            rule_id=issue.rule_id,
            category="module_size",
            file=str(issue.path),
            line=1,
            message=issue.message or "UnicodeDecodeError while reading source",
            playbook=playbook,
            extra={"line_count": issue.line_count, "limit": MAX_CODE_LINES},
        )
        entry["suggested_fix"] = (
            "Re-save the file as UTF-8 so the validator can read it; "
            "remove any binary content from the .py path."
        )
        entry["next_commands"] = ["uv run python scripts/validate_module_sizes.py"]
        return entry
    if issue.rule_id == FUNCTION_RULE_ID:
        entry = build_validator_issue_payload(
            rule_id=issue.rule_id,
            category="module_size",
            file=str(issue.path),
            line=issue.line,
            message=issue.describe(),
            playbook=playbook,
            extra={
                "line_count": issue.line_count,
                "limit": MAX_FUNCTION_CODE_LINES,
                "symbol": issue.symbol,
                "metric": "function_code_lines",
            },
        )
        entry["suggested_fix"] = (
            "Split the function into named steps in the same module. Comments and docstrings do "
            "not count towards this budget, so documenting it will not help — and neither will "
            "moving it to another file."
        )
        entry["next_commands"] = [
            "uv run python scripts/validate_module_sizes.py",
            "make quality-gates",
        ]
        return entry
    entry = build_validator_issue_payload(
        rule_id=issue.rule_id,
        category="module_size",
        file=str(issue.path),
        line=1,
        message=(
            f"{issue.path}: {issue.line_count} code lines "
            f"(limit: {MAX_CODE_LINES}; file is {issue.raw_lines} lines in total)"
        ),
        playbook=playbook,
        extra={
            "line_count": issue.line_count,
            "limit": MAX_CODE_LINES,
            "raw_lines": issue.raw_lines,
            "metric": "code_lines",
        },
    )
    entry["suggested_fix"] = (
        "Move a group of related functions into a sibling module. The budget counts executable "
        "lines only, so deleting comments buys nothing. "
        "`query_ai_context.py before-edit file <path>` shows who imports what before you move it."
    )
    entry["next_commands"] = [
        "uv run python scripts/validate_module_sizes.py",
        "make quality-gates",
    ]
    return entry


# FUNCTION: get_module_size_playbook
# SUMMARY: Return a failure-playbook dict for module-size rule_ids, used by query_ai_context.py. Accepts an optional rule_id to disambiguate between the size-budget rule and the read-error rule.
# INPUT: rule_id (str | None): Specific rule_id to look up, or None for the default size budget rule (back-compat).
# OUTPUT: (dict | None): Playbook dict, or None when rule_id is not a module_size rule.
def get_module_size_playbook(rule_id: str | None = None) -> dict | None:
    if rule_id is None or rule_id == RULE_ID:
        return {
            "meaning": (
                "A production module exceeded the executable-line budget "
                f"({MAX_CODE_LINES} code lines). Comments, docstrings, and blank lines are not "
                "counted, so the size is in the logic itself. What the limit buys is that any "
                "reader — human or agent — can hold a whole module in their head at once."
            ),
            "suggested_fix": (
                "Find a group of functions that talk mostly to each other and give them their "
                "own module in the same package. A private helper travels with its caller."
            ),
            "read_first": ["CLAUDE.md"],
            "smallest_command_to_rerun": "uv run python scripts/validate_module_sizes.py",
            "likely_fix_shape": (
                "One new file beside the original, the chosen group moved into it, imports "
                "updated where they broke."
            ),
            "next_checks": [
                "uv run python scripts/validate_module_sizes.py",
                "make quality-gates",
            ],
            "stop_widening_condition": (
                "Stop when the validator exits 0. Splitting further because a module merely "
                f"looks big is not this budget's business — the line is {MAX_CODE_LINES}."
            ),
        }
    if rule_id == FUNCTION_RULE_ID:
        return {
            "meaning": (
                "A single function exceeded the executable-line budget "
                f"({MAX_FUNCTION_CODE_LINES} code lines). This is the second axis of the size "
                "budget and it is independent of the module one: a file can sit comfortably under "
                "its own limit while holding one function nobody can hold in their head. "
                "Comments and docstrings are not counted."
            ),
            "suggested_fix": (
                "Split the function into named steps in the same module. Each extracted step "
                "should be nameable in a few words; if it is not, the seam is in the wrong place."
            ),
            "read_first": ["CLAUDE.md"],
            "smallest_command_to_rerun": "uv run python scripts/validate_module_sizes.py",
            "likely_fix_shape": (
                "Extract cohesive blocks of the body into private helpers next to it, leaving the "
                "original function as the sequence of those steps."
            ),
            "next_checks": [
                "uv run python scripts/validate_module_sizes.py",
                "make quality-gates",
            ],
            "stop_widening_condition": (
                "Stop once the reported function is under "
                f"{MAX_FUNCTION_CODE_LINES} code lines. Other functions are separate findings."
            ),
        }
    if rule_id == READ_ERROR_RULE_ID:
        return {
            "meaning": (
                "A Python module under module-size validation could not be decoded "
                "as UTF-8. The validator emits this as a structured issue rather "
                "than crashing so the agent loop sees a normal rule_id payload."
            ),
            "suggested_fix": (
                "Re-save the file as UTF-8, or remove the binary content that "
                "slipped into a .py path."
            ),
            "read_first": ["the file referenced by the issue"],
            "smallest_command_to_rerun": "uv run python scripts/validate_module_sizes.py",
            "likely_fix_shape": "Restore valid UTF-8 encoding on the reported file.",
            "next_checks": [
                "uv run python scripts/validate_module_sizes.py",
            ],
            "stop_widening_condition": (
                "Stop widening once the file is readable. Module-size budget checks "
                "apply only after the file parses."
            ),
        }
    return None


# FUNCTION: main
# SUMMARY: Run the module size validator and print any violations.
# OUTPUT: (int): Zero when all target modules fit within the configured line budget.
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enforce module line-count budget.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output with remediation guidance.",
    )
    # **LOGIC_STEP**: Default to an empty argv when called directly from tests so that
    # pytest's own argv does not leak into argparse via sys.argv[1:].
    args = parser.parse_args([] if argv is None else argv)

    issues = collect_module_size_issues(ROOT_DIR)

    if args.json:
        payload = {
            "status": "ok" if not issues else "error",
            "rule_id": RULE_ID,
            "issues": [_issue_to_json(i) for i in issues],
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1 if issues else 0

    if not issues:
        print("Module size validation passed.")
        return 0

    for issue in issues:
        print(issue.describe())
    return 1


# FUNCTION: __main__
# SUMMARY: Script entrypoint for module size validation.
if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
