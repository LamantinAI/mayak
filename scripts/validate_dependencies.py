#!/usr/bin/env python3
# FILE: validate_dependencies.py
# SUMMARY: Quality gate rejecting runtime imports that no declared dependency provides.

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, metadata, packages_distributions
from pathlib import Path
from typing import Sequence

from ai_context.dynamic_imports import dynamic_import_targets
from ai_context.rendering import render_json
from ai_context.validator_contract import build_validator_issue_payload

# ATTRIBUTE: ROOT_DIR (Path)
# SUMMARY: Absolute repository root scanned by this validator.
ROOT_DIR = Path(__file__).resolve().parent.parent

# ATTRIBUTE: RUNTIME_PACKAGE (str)
# SUMMARY: Package whose imports must be backed by a declared dependency.
RUNTIME_PACKAGE = "project"

# ATTRIBUTE: FIRST_PARTY_ROOTS (frozenset[str])
# SUMMARY: Top-level modules that live in this repository rather than in a distribution.
FIRST_PARTY_ROOTS = frozenset({"project", "ai_context", "ai_query", "scripts", "tests", "alembic"})

_DEPENDENCIES_RULE_PLAYBOOKS: dict[str, dict[str, object]] = {
    "dependencies.undeclared_import": {
        "meaning": (
            "A module under project/ imports a third-party package that is not among the "
            "project's declared dependencies. It resolves today only because something else "
            "pulled it in transitively — a version bump of that other package can remove it "
            "and break the service at import time, with every gate still green."
        ),
        "suggested_fix": (
            "Add the providing distribution to [project].dependencies in pyproject.toml and "
            "re-lock with `uv lock`, or stop importing it from runtime code."
        ),
        "read_first": [
            "pyproject.toml",
            "the file referenced by the issue",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_dependencies.py",
        "likely_fix_shape": "One new entry in [project].dependencies plus a refreshed uv.lock.",
        "next_checks": [
            "uv lock",
            "uv run python scripts/validate_dependencies.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop once every third-party import under project/ maps to a declared dependency."
        ),
    },
    "dependencies.unresolvable_import": {
        "meaning": (
            "A module under project/ imports a top-level name that is neither part of the "
            "standard library, nor first-party, nor provided by any installed distribution."
        ),
        "suggested_fix": (
            "Install and declare the package that provides it, or fix the import if the name "
            "is a typo or a leftover from removed code."
        ),
        "read_first": [
            "the file referenced by the issue",
            "pyproject.toml",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_dependencies.py",
        "likely_fix_shape": "Remove the dead import, or add the missing dependency and sync.",
        "next_checks": [
            "uv sync --frozen",
            "uv run python scripts/validate_dependencies.py",
        ],
        "stop_widening_condition": "Stop once the import resolves to an installed distribution.",
    },
}


# DATACLASS: validate_dependencies.DependencyIssue
# SUMMARY: One runtime import that no declared dependency accounts for.
@dataclass(slots=True)
class DependencyIssue:
    # ATTRIBUTE: path (Path)
    # SUMMARY: Absolute path of the importing module.
    path: Path

    # ATTRIBUTE: line (int)
    # SUMMARY: 1-based line of the import statement.
    line: int

    # ATTRIBUTE: rule_id (str)
    # SUMMARY: Stable rule identifier.
    rule_id: str

    # ATTRIBUTE: message (str)
    # SUMMARY: Human-readable description.
    message: str

    # ATTRIBUTE: category (str)
    # SUMMARY: Top-level issue category used in structured validator output.
    category: str = "dependencies"


# FUNCTION: get_dependencies_rule_playbook
# SUMMARY: Return the shared remediation playbook for a stable dependency rule ID.
# OUTPUT: (dict[str, object] | None): Remediation metadata, or None when the rule is unknown.
def get_dependencies_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _DEPENDENCIES_RULE_PLAYBOOKS.get(rule_id)
    return dict(playbook) if playbook is not None else None


# FUNCTION: normalize_distribution_name
# SUMMARY: Normalize a distribution name per PEP 503 so pyproject and metadata spellings compare equal.
# INPUT: name (str): Raw distribution name.
# OUTPUT: (str): Lowercase name with runs of -, _ and . collapsed to a single dash.
def normalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# FUNCTION: _requirement_strings
# SUMMARY: Collect every requirement string a pyproject document declares, from all three places
#          a dependency can be named.
# INPUT: pyproject_text (str): Full text of pyproject.toml.
# OUTPUT: (list[str]): Requirement strings from [project].dependencies, optional-dependencies
#         and dependency-groups.
# NOTE: This was a hand-rolled line scanner, justified by a 3.10 floor where tomllib does not
# exist. That scanner cost two defects before it worked — it counted the brackets inside
# "uvicorn[standard]==0.40.0" as array delimiters and silently truncated the declared set to six
# entries, and it knew nothing about [dependency-groups] until the duplicate `test` extra was
# removed. With the floor at 3.13 tomllib is stdlib, and a real parser cannot make either mistake.
def _requirement_strings(pyproject_text: str) -> list[str]:
    document = tomllib.loads(pyproject_text)
    requirements: list[str] = []

    project_table = document.get("project", {})
    requirements.extend(
        item for item in project_table.get("dependencies", []) if isinstance(item, str)
    )

    # **LOGIC_STEP**: Both remaining tables map a name to an array of requirements. A group entry
    # may also be a table such as {include-group = "dev"}, which names no distribution — hence the
    # isinstance filter rather than a blind extend.
    for table in (
        project_table.get("optional-dependencies", {}),
        document.get("dependency-groups", {}),
    ):
        for entries in table.values():
            requirements.extend(item for item in entries if isinstance(item, str))

    return requirements


# FUNCTION: declared_distributions
# SUMMARY: Read the distribution names this project declares as its own dependencies.
# INPUT: repo_root (Path): Repository root containing pyproject.toml.
# OUTPUT: (set[str]): Normalized names from [project].dependencies, optional-dependencies and
#         dependency-groups.
def declared_distributions(repo_root: Path) -> set[str]:
    raw = _requirement_strings((repo_root / "pyproject.toml").read_text(encoding="utf-8"))

    names: set[str] = set()
    for requirement in raw:
        # **LOGIC_STEP**: Take the name up to the first version specifier, extra or marker.
        head = re.split(r"[<>=!~;\[\s]", requirement.strip(), maxsplit=1)[0]
        if not head:
            continue
        names.add(normalize_distribution_name(head))
        # **LOGIC_STEP**: An extra is a declaration too — psycopg[pool] is how psycopg-pool is
        # declared here. Without expanding extras the validator would report the entire runtime
        # database layer as undeclared.
        extras = re.search(r"\[([^\]]+)\]", requirement)
        if extras:
            for extra in extras.group(1).split(","):
                names |= _distributions_required_by_extra(head, extra.strip())
    return names


# FUNCTION: _distributions_required_by_extra
# SUMMARY: List the distributions a declared package pulls in through one of its extras.
# INPUT: distribution (str): Declared distribution name.
# INPUT: extra (str): Extra name requested in the declaration.
# OUTPUT: (set[str]): Normalized names required by that extra, empty when metadata is unavailable.
def _distributions_required_by_extra(distribution: str, extra: str) -> set[str]:
    try:
        requires = metadata(distribution).get_all("Requires-Dist") or []
    except PackageNotFoundError:
        return set()

    names: set[str] = set()
    for requirement in requires:
        if f'extra == "{extra}"' not in requirement and f"extra == '{extra}'" not in requirement:
            continue
        head = re.split(r"[<>=!~;\[\s]", requirement.strip(), maxsplit=1)[0]
        if head:
            names.add(normalize_distribution_name(head))
    return names


# FUNCTION: _top_level_imports
# SUMMARY: Collect the top-level module name of every import in a module, with its line.
# OUTPUT: (list[tuple[str, int]]): Pairs of top-level module name and 1-based line number.
def _top_level_imports(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            # **LOGIC_STEP**: level > 0 is a relative import, which is first-party by definition.
            if node.level == 0 and node.module:
                found.append((node.module.split(".")[0], node.lineno))
    # **LOGIC_STEP**: A module pulled in by importlib.import_module("x") is as undeclared as one
    # pulled in by `import x`, and until 2026-09-02 only the second was checked — so the dynamic
    # spelling imported a transitively-installed distribution with this gate green.
    # ai_context/dynamic_imports.py owns which call shapes count and why.
    found.extend((target.split(".")[0], line) for target, line in dynamic_import_targets(tree))
    return found


# FUNCTION: collect_dependency_issues
# SUMMARY: Check every third-party import under project/ against the declared dependency set.
# INPUT: repo_root (Path): Repository root to scan.
# OUTPUT: (list[DependencyIssue]): Issues found, ordered by path then line.
def collect_dependency_issues(repo_root: Path) -> list[DependencyIssue]:
    runtime_dir = repo_root / RUNTIME_PACKAGE
    if not runtime_dir.is_dir():
        return []

    declared = declared_distributions(repo_root)
    provided_by = packages_distributions()
    stdlib = sys.stdlib_module_names

    issues: list[DependencyIssue] = []
    for path in sorted(runtime_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        seen: set[str] = set()
        for module_name, line in _top_level_imports(tree):
            if module_name in seen or module_name in stdlib or module_name in FIRST_PARTY_ROOTS:
                continue
            seen.add(module_name)

            distributions = provided_by.get(module_name)
            if not distributions:
                issues.append(
                    DependencyIssue(
                        path=path,
                        line=line,
                        rule_id="dependencies.unresolvable_import",
                        message=(f"Import '{module_name}' resolves to no installed distribution."),
                    )
                )
                continue

            normalized = {normalize_distribution_name(name) for name in distributions}
            if normalized & declared:
                continue

            issues.append(
                DependencyIssue(
                    path=path,
                    line=line,
                    rule_id="dependencies.undeclared_import",
                    message=(
                        f"Import '{module_name}' is provided by "
                        f"{', '.join(sorted(distributions))}, which pyproject.toml does not "
                        "declare — it is available only transitively."
                    ),
                )
            )

    return issues


# FUNCTION: _issue_to_payload
# SUMMARY: Convert one issue into a JSON-serializable remediation payload.
# INPUT: repo_root (Path): Repository root used for relative-path rendering.
def _issue_to_payload(issue: DependencyIssue, repo_root: Path) -> dict[str, object]:
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category=issue.category,
        file=str(issue.path.relative_to(repo_root)),
        line=issue.line,
        message=issue.message,
        playbook=get_dependencies_rule_playbook(issue.rule_id),
    )


# FUNCTION: main
# SUMMARY: Run dependency validation and return a process exit code.
# OUTPUT: (int): Zero when every runtime import is backed by a declared dependency.
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate runtime imports against declared dependencies."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured machine-readable validation results.",
    )
    args = parser.parse_args([] if argv is None else argv)

    issues = collect_dependency_issues(ROOT_DIR)
    if not issues:
        if args.json:
            print(render_json({"status": "ok", "issues": []}), end="")
        else:
            print("Dependency validation passed.")
        return 0

    if args.json:
        print(
            render_json(
                {
                    "status": "error",
                    "issues": [_issue_to_payload(issue, ROOT_DIR) for issue in issues],
                }
            ),
            end="",
        )
        return 1

    for issue in issues:
        print(f"{issue.path.relative_to(ROOT_DIR)}:{issue.line}: [{issue.rule_id}] {issue.message}")
    return 1


# FUNCTION: __main__
# SUMMARY: Script entrypoint for the dependency validator.
if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
