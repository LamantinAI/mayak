# FILE: validate_architecture.py
# SUMMARY: Repository utility that enforces the hexagonal architecture import boundaries for production modules.

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

from ai_context.dynamic_imports import dynamic_import_targets
from ai_context.rendering import render_json
from ai_context.validator_contract import build_validator_issue_payload


# ATTRIBUTE: _DOMAIN_ALLOWED_PREFIXES (tuple[str, ...])
# SUMMARY: The only non-stdlib imports a domain module may make.
# NOTE: The domain is checked with an allowlist, and the other two layers with blacklists, because
# only the domain has a dependency set small enough to enumerate: measured across this template and
# a project with two verticals, every domain import in existence was `__future__`, `dataclasses`,
# `datetime`, `typing`, `uuid`, or another `project.domain` module — 21 imports, six roots.
#
# It used to be a blacklist naming `langchain`, `fastapi`, `sqlalchemy`, `openai` and six more, and
# on 2026-08-12 that list was measured to miss the one import a vertical author would actually
# write. `_matches_prefix` compares exactly or on `prefix + "."`, so `langchain_core` — imported on
# eight lines across five modules under `project/`, thirteen counting the tests
# (`git grep -c 'from langchain_core'`) — did not match `langchain`, and neither did
# `langchain_openai`. A domain module importing either passed lint, mypy and this validator.
#
# The failure is structural, not a typo: a blacklist bans what somebody remembered, in the spelling
# they remembered it in, and every library released after it was written is allowed by default. An
# allowlist inverts that — `pydantic-ai`, `instructor`, whatever ships next quarter, all rejected
# with no edit here. The stdlib half needs no maintenance either: it comes from the interpreter.
_DOMAIN_ALLOWED_PREFIXES = ("project.domain",)

# ATTRIBUTE: _APPLICATION_BANNED_PREFIXES (tuple[str, ...])
# SUMMARY: Import prefixes that application modules must never depend on.
# NOTE: Application and infrastructure stay on blacklists on purpose. Both legitimately import
# third-party code — pydantic, psycopg, langchain, whatever an adapter needs — so there is no small
# set to enumerate, and an allowlist there would be a copy of pyproject.toml maintained by hand.
_APPLICATION_BANNED_PREFIXES = ("project.infrastructure",)

# ATTRIBUTE: _INFRASTRUCTURE_BANNED_PREFIXES (tuple[str, ...])
# SUMMARY: Import prefixes that infrastructure modules must never depend on because they couple adapters back to runtime assembly.
_INFRASTRUCTURE_BANNED_PREFIXES = (
    "project.core.composition_root",
    "project.core.service_registration",
    "project.core.lifecycle",
    "project.launcher",
)

_RULE_STRENGTH_RUNTIME_ENFORCED = "runtime_enforced"
_RULE_STRENGTH_GUIDANCE_ONLY = "guidance_only"
_RULE_STRENGTH_NOT_ENFORCED = "not_enforced_in_validator"

# ATTRIBUTE: _LAYER_RULES (dict[str, dict[str, object]])
# SUMMARY: Canonical machine-readable layer rules shared by the runtime validator and generated architecture docs.
_LAYER_RULES = {
    "domain": {
        "declared_allowed_dependencies": _DOMAIN_ALLOWED_PREFIXES,
        "runtime_enforced_forbidden_imports": (),
        "runtime_enforced_allowed_imports": _DOMAIN_ALLOWED_PREFIXES,
        "runtime_validator": "scripts/validate_architecture.py",
        "enforcement_summary": (
            "The declared allowed dependencies are runtime_enforced as a whitelist gate: a domain "
            "module may import the standard library and project.domain, and nothing else."
        ),
        "enforcement_notes": (
            "Runtime validation enforces the allowlist, so a library nobody thought to ban is "
            "rejected by default.",
            "The standard-library half comes from sys.stdlib_module_names and needs no maintenance.",
        ),
    },
    "application": {
        "declared_allowed_dependencies": ("project.domain", "project.application"),
        "runtime_enforced_forbidden_imports": _APPLICATION_BANNED_PREFIXES,
        "runtime_validator": "scripts/validate_architecture.py",
        "enforcement_summary": (
            "Forbidden import prefixes are runtime_enforced. "
            "Declared allowed dependencies remain guidance_only and are "
            "not_enforced_in_validator as a whitelist gate."
        ),
        "enforcement_notes": (
            "Runtime validation currently enforces forbidden import prefixes only.",
            "Declared allowed dependencies remain architectural guidance, not a whitelist gate.",
        ),
    },
    "infrastructure": {
        "declared_allowed_dependencies": (
            "project.domain",
            "project.application",
            "project.infrastructure",
        ),
        "runtime_enforced_forbidden_imports": _INFRASTRUCTURE_BANNED_PREFIXES,
        "runtime_validator": "scripts/validate_architecture.py",
        "enforcement_summary": (
            "Infrastructure keeps broad dependency freedom, but runtime assembly and "
            "entrypoint imports are runtime_enforced forbidden prefixes."
        ),
        "enforcement_notes": (
            "Infrastructure may depend on project.core utility modules such as config and logging.",
            "Runtime assembly modules and launcher entrypoints remain runtime_enforced forbidden imports.",
        ),
    },
}

_ARCHITECTURE_RULE_PLAYBOOKS = {
    "arch.application.no_forbidden_import": {
        "meaning": (
            "The application layer imported infrastructure code directly, which breaks "
            "the runtime_enforced boundary between orchestration and adapters."
        ),
        "suggested_fix": (
            "Move the dependency boundary into project.domain or inject the implementation "
            "through the composition-root wiring flow instead of importing infrastructure."
        ),
        "read_first": [
            "CLAUDE.md",
            "docs/architecture_rules.json",
            "project/core/composition_root.py",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_architecture.py",
        "likely_fix_shape": (
            "Replace the infrastructure import with a domain port, DTO boundary, or "
            "composition-root-wired dependency."
        ),
        "next_checks": [
            "uv run python scripts/validate_architecture.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once validate_architecture.py passes and the module no longer "
            "imports project.infrastructure.*."
        ),
    },
    "arch.domain.import_not_allowed": {
        "meaning": (
            "A domain module imported something outside its allowlist. The domain may import the "
            "Python standard library and project.domain, and nothing else — the rule is a "
            "whitelist rather than a list of banned libraries, so anything nobody thought to ban "
            "is rejected too. This rule was a blacklist until 2026-08-12, when `langchain_core` "
            "was measured to slip past a ban written as `langchain`."
        ),
        "suggested_fix": (
            "Keep domain modules framework-free by moving external concerns into "
            "project/application or project/infrastructure and depend on ports instead. A domain "
            "type that needs to describe something a library models — a chat turn, a tool call — "
            "gets its own dataclass here, and the adapter translates."
        ),
        "read_first": [
            "CLAUDE.md",
            "project/domain/ports.py",
            "docs/architecture_rules.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_architecture.py",
        "likely_fix_shape": (
            "Move the external dependency behind a domain port or relocate the behavior "
            "into application or infrastructure."
        ),
        "next_checks": [
            "uv run python scripts/validate_architecture.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once validate_architecture.py passes and the domain module "
            "contains no framework or infrastructure imports."
        ),
    },
    "arch.infrastructure.no_forbidden_import": {
        "meaning": (
            "An infrastructure module imported runtime assembly or launcher code directly, "
            "which couples adapters back into the composition-root entrypoint flow."
        ),
        "suggested_fix": (
            "Keep infrastructure on reusable adapter surfaces. Move wiring logic back into "
            "project.core.composition_root, project.core.service_registration, or launcher code "
            "instead of importing those modules from infrastructure."
        ),
        "read_first": [
            "CLAUDE.md",
            "project/core/composition_root.py",
            "project/core/service_registration.py",
            "docs/architecture_rules.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_architecture.py",
        "likely_fix_shape": (
            "Replace the runtime-assembly import with an injected dependency, typed setting, "
            "or a helper that lives outside the composition-root and launcher modules."
        ),
        "next_checks": [
            "uv run python scripts/validate_architecture.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once validate_architecture.py passes and the infrastructure module "
            "no longer imports composition-root, service-registration, lifecycle, or launcher modules."
        ),
    },
    "arch.syntax_error": {
        "meaning": (
            "A Python source file under validation could not be parsed because the file "
            "contains a SyntaxError. validate_architecture.py emits this as a structured "
            "issue rather than crashing so the agent loop sees a normal rule_id payload."
        ),
        "suggested_fix": (
            "Open the file at the reported line and repair the syntax. The validator will "
            "continue past this file once it parses cleanly; do not widen scope to other modules."
        ),
        "read_first": [
            "the file referenced by the issue",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_architecture.py",
        "likely_fix_shape": (
            "Restore valid Python syntax on the reported line — close brackets, fix indentation, "
            "or revert the partial edit that introduced the parse error."
        ),
        "next_checks": [
            "uv run python scripts/validate_architecture.py",
        ],
        "stop_widening_condition": (
            "Stop widening once the file parses cleanly. Other architecture issues (if any) "
            "surface only after the syntax error is resolved."
        ),
    },
    "arch.read_error": {
        "meaning": (
            "A Python source file could not be decoded as UTF-8 by validate_architecture.py. "
            "Surfaced as a structured issue so the agent loop can recover."
        ),
        "suggested_fix": (
            "Re-save the file as UTF-8, or remove the binary content that slipped into a .py path."
        ),
        "read_first": [
            "the file referenced by the issue",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_architecture.py",
        "likely_fix_shape": ("Restore valid UTF-8 encoding on the reported file."),
        "next_checks": [
            "uv run python scripts/validate_architecture.py",
        ],
        "stop_widening_condition": (
            "Stop widening once the file is readable. No other architecture rule applies "
            "until the file parses."
        ),
    },
}


# FUNCTION: get_layer_rules
# SUMMARY: Return the canonical layer rules with runtime enforcement separated from guidance-only constraints.
# OUTPUT: (dict[str, dict[str, object]]): Mapping of layer names to runtime-enforced and guidance-only metadata.
def get_layer_rules() -> dict[str, dict[str, object]]:
    return {
        layer: {
            "runtime_enforced": {
                "forbidden_imports": rules["runtime_enforced_forbidden_imports"],
                # **LOGIC_STEP**: Empty for every layer whose contract is a blacklist. A consumer
                # reads this key to learn whether the declared dependencies are a real gate, which
                # is the difference between "intended architecture" and "the build stops you".
                "allowed_imports": rules.get("runtime_enforced_allowed_imports", ()),
                "validator": rules["runtime_validator"],
                "enforcement_notes": rules["enforcement_notes"],
                "rule_strength": _RULE_STRENGTH_RUNTIME_ENFORCED,
            },
            "guidance_only": {
                "declared_allowed_dependencies": rules["declared_allowed_dependencies"],
                "narrative_notes": (
                    "Declared allowed dependencies describe the intended layer contract.",
                    "They are guidance-only unless mirrored by runtime-enforced rules.",
                ),
                "rule_strength": _RULE_STRENGTH_GUIDANCE_ONLY,
            },
            "not_enforced_in_validator": {
                "declared_allowed_dependencies_are_not_whitelist_gate": not rules.get(
                    "runtime_enforced_allowed_imports"
                ),
                "explanation": (
                    "Declared allowed dependencies describe the intended layer contract, "
                    "but validate_architecture.py does not enforce them as a whitelist gate."
                )
                if not rules.get("runtime_enforced_allowed_imports")
                else (
                    "The declared allowed dependencies ARE the whitelist gate for this layer: "
                    "validate_architecture.py rejects any import outside them and the standard "
                    "library."
                ),
                "rule_strength": _RULE_STRENGTH_NOT_ENFORCED
                if not rules.get("runtime_enforced_allowed_imports")
                else _RULE_STRENGTH_RUNTIME_ENFORCED,
            },
            "enforcement_summary": rules["enforcement_summary"],
        }
        for layer, rules in _LAYER_RULES.items()
    }


# FUNCTION: get_architecture_rule_playbook
# SUMMARY: Return the shared remediation playbook for a runtime architecture rule ID.
# OUTPUT: (dict[str, object] | None): Remediation metadata or None when the rule is unknown.
def get_architecture_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _ARCHITECTURE_RULE_PLAYBOOKS.get(rule_id)
    if playbook is None:
        return None
    return {
        "meaning": playbook["meaning"],
        "suggested_fix": playbook["suggested_fix"],
        "read_first": list(playbook["read_first"]),
        "smallest_command_to_rerun": playbook["smallest_command_to_rerun"],
        "likely_fix_shape": playbook["likely_fix_shape"],
        "next_checks": list(playbook["next_checks"]),
        "stop_widening_condition": playbook["stop_widening_condition"],
    }


# DATACLASS: validate_architecture.ArchitectureIssue
# SUMMARY: Structured representation of a single architecture boundary violation.
@dataclass(slots=True)
class ArchitectureIssue:
    # ATTRIBUTE: path (Path)
    # SUMMARY: File containing the violating import.
    path: Path

    # ATTRIBUTE: line (int)
    # SUMMARY: 1-based line number of the violating import statement.
    line: int

    # ATTRIBUTE: message (str)
    # SUMMARY: Human-readable explanation of the violated boundary.
    message: str

    # ATTRIBUTE: rule_id (str)
    # SUMMARY: Stable rule identifier consumed by automation-friendly validator output.
    rule_id: str

    # ATTRIBUTE: category (str)
    # SUMMARY: Top-level issue category used by machine-readable diagnostics.
    category: str = "architecture"


# FUNCTION: _is_production_python_file
# SUMMARY: Check whether a file is within the production architecture validation scope.
# OUTPUT: (bool): True when the file belongs to project/** and should be validated.
def _is_production_python_file(path: Path) -> bool:
    parts = path.parts
    return len(parts) >= 2 and parts[0] == "project" and path.suffix == ".py"


# FUNCTION: _module_name_from_path
# SUMMARY: Convert a repository-relative Python file path into an importable module name.
# INPUT: path (Path): Repository-relative file path.
# OUTPUT: (str): Dotted module path without the .py suffix.
def _module_name_from_path(path: Path) -> str:
    # **LOGIC_STEP**: Drop the file suffix and convert path segments to a dotted module path.
    return ".".join(path.with_suffix("").parts)


# FUNCTION: _layer_from_path
# SUMMARY: Determine the architectural layer name from a production file path.
# INPUT: path (Path): Repository-relative production file path.
# OUTPUT: (str | None): Layer identifier or None when the path is outside the layered subtree.
def _layer_from_path(path: Path) -> str | None:
    # **LOGIC_STEP**: Use the first package segment below project/ as the layer key.
    parts = path.parts
    if len(parts) < 2 or parts[0] != "project":
        return None
    return parts[1]


# FUNCTION: _resolve_import_name
# SUMMARY: Resolve an import statement to its absolute dotted name when possible.
# INPUT: current_module (str): Dotted module path of the file being validated.
# OUTPUT: (list[str]): Absolute dotted import targets extracted from the node.
def _resolve_import_name(
    current_module: str,
    node: ast.Import | ast.ImportFrom,
) -> list[str]:
    # **LOGIC_STEP**: Import statements already provide absolute module names per alias.
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]

    # **LOGIC_STEP**: Resolve relative imports against the current module path.
    if node.level > 0:
        current_parts = current_module.split(".")
        parent_parts = current_parts[:-1]
        base_parts = parent_parts[: len(parent_parts) - node.level + 1]
        if node.module:
            return [".".join([*base_parts, node.module])]
        return [".".join(base_parts)] if base_parts else []

    return [node.module] if node.module else []


# FUNCTION: _matches_prefix
# SUMMARY: Check whether an import path matches a banned prefix exactly or as a child module.
# OUTPUT: (bool): True when the import violates the banned prefix.
def _matches_prefix(import_name: str, banned_prefix: str) -> bool:
    # **LOGIC_STEP**: Treat exact matches and nested submodules as violations.
    return import_name == banned_prefix or import_name.startswith(f"{banned_prefix}.")


# FUNCTION: _is_allowed_import
# SUMMARY: Decide whether one import is inside a layer's allowlist.
# INPUT: allowed_prefixes (tuple[str, ...]): Project prefixes permitted on top of the standard library.
# OUTPUT: (bool): True when the import is stdlib or sits under one of the allowed prefixes.
def _is_allowed_import(import_name: str, allowed_prefixes: tuple[str, ...]) -> bool:
    # **LOGIC_STEP**: The standard library is read from the interpreter rather than listed here.
    # A hand-written list of stdlib names is a maintenance debt with no upside, and it would go
    # stale the first time a domain module reached for `tomllib` or `zoneinfo`.
    if import_name.split(".", 1)[0] in sys.stdlib_module_names:
        return True
    return any(_matches_prefix(import_name, prefix) for prefix in allowed_prefixes)


# FUNCTION: _validate_import
# SUMMARY: Validate a resolved import path against the rules for the current architectural layer.
# OUTPUT: (str | None): Violation message when the import breaks a boundary.
def _validate_import(layer: str | None, import_name: str) -> tuple[str, str] | None:
    # **LOGIC_STEP**: Match imports against the canonical forbidden prefixes for the current layer.
    layer_rules = _LAYER_RULES.get(layer)
    if layer_rules is None:
        return None

    # **LOGIC_STEP**: A layer that declares an allowlist is checked against it and nothing else —
    # the allowlist already rejects everything a blacklist would, and running both would be two
    # copies of one rule, free to drift apart. Only the domain declares one; see the note on
    # _DOMAIN_ALLOWED_PREFIXES for why the other layers cannot.
    allowed_prefixes = layer_rules.get("runtime_enforced_allowed_imports")
    if allowed_prefixes and not _is_allowed_import(import_name, allowed_prefixes):
        layer_name = layer or "unknown"
        allowed = ", ".join(allowed_prefixes)
        return (
            f"arch.{layer_name}.import_not_allowed",
            f"{layer.capitalize()} layer may import only the Python standard library and "
            f"{allowed}; '{import_name}' is neither",
        )

    for prefix in layer_rules["runtime_enforced_forbidden_imports"]:
        if _matches_prefix(import_name, prefix):
            layer_name = layer or "unknown"
            return (
                f"arch.{layer_name}.no_forbidden_import",
                f"{layer.capitalize()} layer must not import "
                f"'{import_name}' because it depends on forbidden prefix '{prefix}'",
            )

    return None


# FUNCTION: _issue_to_payload
# SUMMARY: Convert an architecture issue into a stable JSON-serializable payload.
# INPUT: repo_root (Path): Repository root used for relative paths.
def _issue_to_payload(issue: ArchitectureIssue, repo_root: Path) -> dict[str, object]:
    playbook = get_architecture_rule_playbook(issue.rule_id)
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category=issue.category,
        file=str(issue.path.relative_to(repo_root)),
        line=issue.line,
        message=issue.message,
        playbook=playbook,
    )


# FUNCTION: validate_python_source
# SUMMARY: Validate all import statements in a single production Python file.
# INPUT: path (Path): Absolute production source file path.
def validate_python_source(path: Path, repo_root: Path) -> list[ArchitectureIssue]:
    relative_path = path.relative_to(repo_root)
    current_module = _module_name_from_path(relative_path)
    layer = _layer_from_path(relative_path)
    # **LOGIC_STEP**: Read & parse source under guarded exceptions so a broken file
    # surfaces as a structured ArchitectureIssue rather than a raw Python traceback.
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        return [
            ArchitectureIssue(
                path=path,
                line=1,
                message=f"UnicodeDecodeError while reading source: {error.reason}",
                rule_id="arch.read_error",
            )
        ]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        return [
            ArchitectureIssue(
                path=path,
                line=error.lineno or 1,
                message=f"SyntaxError while parsing source: {error.msg}",
                rule_id="arch.syntax_error",
            )
        ]
    issues: list[ArchitectureIssue] = []

    # **LOGIC_STEP**: Written imports and called ones are checked the same way. Until 2026-09-02
    # only the first kind was collected, so `importlib.import_module("psycopg")` in a domain module
    # passed the whole gate — see ai_context/dynamic_imports.py for the measurement, for why the
    # call's names are resolved against this file's own imports, and for what still escapes.
    imported: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported.extend(
                (name, node.lineno) for name in _resolve_import_name(current_module, node)
            )
    imported.extend(dynamic_import_targets(tree))

    for import_name, line in imported:
        if not import_name:
            continue
        violation = _validate_import(layer, import_name)
        if violation is None:
            continue
        rule_id, message = violation
        issues.append(
            ArchitectureIssue(
                path=path,
                line=line,
                message=message,
                rule_id=rule_id,
            )
        )

    return issues


# FUNCTION: collect_architecture_issues
# SUMMARY: Validate all production Python files in the repository against import boundary rules.
def collect_architecture_issues(repo_root: Path) -> list[ArchitectureIssue]:
    # **LOGIC_STEP**: Walk production Python files in a stable order to produce deterministic diagnostics.
    issues: list[ArchitectureIssue] = []
    for path in sorted(repo_root.rglob("*.py")):
        relative_path = path.relative_to(repo_root)
        if not _is_production_python_file(relative_path):
            continue
        issues.extend(validate_python_source(path, repo_root))
    return issues


# FUNCTION: main
# SUMMARY: Run architecture validation from the repository root and return a process exit code.
# OUTPUT: (int): Zero when validation succeeds and non-zero otherwise.
def main() -> int:
    # **LOGIC_STEP**: Validate the current repository and print any architecture boundary failures.
    parser = argparse.ArgumentParser(description="Validate repository architecture boundaries.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured machine-readable validation results.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    issues = collect_architecture_issues(repo_root)
    if not issues:
        if args.json:
            print(render_json({"status": "ok", "issues": []}), end="")
        else:
            print("Architecture validation passed.")
        return 0

    if args.json:
        print(
            render_json(
                {
                    "status": "error",
                    "issues": [_issue_to_payload(issue, repo_root) for issue in issues],
                }
            ),
            end="",
        )
        return 1

    for issue in issues:
        relative_path = issue.path.relative_to(repo_root)
        print(f"{relative_path}:{issue.line}: [{issue.rule_id}] {issue.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
