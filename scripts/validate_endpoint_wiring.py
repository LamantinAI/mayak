#!/usr/bin/env python3
# FILE: validate_endpoint_wiring.py
# SUMMARY: Repository utility that validates endpoint-facing dependency wiring contracts for FastAPI endpoint modules.

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path

from ai_context.rendering import render_json
from ai_context.validator_contract import build_validator_issue_payload
from scripts.generate_ai_context import build_context_map


# ATTRIBUTE: ROOT_DIR (Path)
# SUMMARY: Absolute repository root used by the validator entrypoint.
ROOT_DIR = Path(__file__).resolve().parent.parent

# ATTRIBUTE: REGISTRATION_RELATIVE_PATH (Path)
# SUMMARY: Repository-relative module where every application router must be included on the app.
REGISTRATION_RELATIVE_PATH = Path("project/infrastructure/api/router_registration.py")

_ENDPOINT_RULE_PLAYBOOKS = {
    "endpoint.no_direct_service_import": {
        "meaning": (
            "An endpoint imported an application service directly instead of staying on the "
            "typed dependency alias surface."
        ),
        "suggested_fix": (
            "Import DTOs only and route all service access through typed dependency aliases "
            "from project/infrastructure/api/dependencies.py."
        ),
        "read_first": [
            "CLAUDE.md",
            "project/infrastructure/api/dependencies.py",
            "docs/architecture_rules.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_endpoint_wiring.py",
        "likely_fix_shape": (
            "Remove the direct service import, add or reuse a typed alias in dependencies.py, "
            "and inject that alias into the route signature."
        ),
        "next_checks": [
            "uv run python scripts/validate_endpoint_wiring.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once validate_endpoint_wiring.py passes and the endpoint imports "
            "only DTOs plus typed dependency aliases."
        ),
    },
    "endpoint.no_depends_without_alias": {
        "meaning": (
            "An endpoint used Depends(get_...) directly instead of the canonical Annotated alias."
        ),
        "suggested_fix": (
            "Replace Depends(get_...) parameters with the matching Annotated alias from "
            "project/infrastructure/api/dependencies.py."
        ),
        "read_first": [
            "CLAUDE.md",
            "project/infrastructure/api/dependencies.py",
            "docs/architecture_rules.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_endpoint_wiring.py",
        "likely_fix_shape": (
            "Replace the parameter annotation/default pair with the existing typed alias, or "
            "define the missing alias in dependencies.py first."
        ),
        "next_checks": [
            "uv run python scripts/validate_endpoint_wiring.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once validate_endpoint_wiring.py passes and the route uses a typed "
            "dependency alias instead of Depends(get_...)."
        ),
    },
    "endpoint.no_direct_service_annotation": {
        "meaning": (
            "An endpoint parameter is annotated with an application service type directly "
            "instead of the typed alias contract."
        ),
        "suggested_fix": (
            "Use a typed dependency alias instead of annotating the route parameter with the "
            "application service type directly."
        ),
        "read_first": [
            "CLAUDE.md",
            "project/infrastructure/api/dependencies.py",
            "docs/architecture_rules.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_endpoint_wiring.py",
        "likely_fix_shape": (
            "Swap the direct service type annotation for the matching Annotated alias from "
            "dependencies.py."
        ),
        "next_checks": [
            "uv run python scripts/validate_endpoint_wiring.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once validate_endpoint_wiring.py passes and the route signature "
            "contains only typed dependency aliases for services."
        ),
    },
    "endpoint.alias_chain_invalid": {
        "meaning": (
            "The endpoint uses a typed alias, but the alias -> getter -> service_key chain "
            "does not resolve to a known service."
        ),
        "suggested_fix": (
            "Repair the alias -> getter -> service_key chain in dependencies.py so the route "
            "depends on a known service key."
        ),
        "read_first": [
            "CLAUDE.md",
            "project/infrastructure/api/dependencies.py",
            "docs/architecture_rules.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_endpoint_wiring.py",
        "likely_fix_shape": (
            "Align the alias, getter, and service key with the actual service registration in "
            "dependencies.py and the composition-root wiring files."
        ),
        "next_checks": [
            "uv run python scripts/validate_endpoint_wiring.py",
            "uv run python scripts/generate_ai_context.py --check",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once validate_endpoint_wiring.py passes and generate_ai_context.py "
            "--check confirms the alias chain resolves cleanly."
        ),
    },
    "endpoint.no_direct_service_construction": {
        "meaning": (
            "The endpoint constructs an application service directly instead of receiving it "
            "through typed dependency aliases and composition-root wiring."
        ),
        "suggested_fix": (
            "Keep endpoint wiring thin and use typed dependency aliases instead of direct "
            "service construction."
        ),
        "read_first": [
            "CLAUDE.md",
            "project/infrastructure/api/dependencies.py",
            "docs/architecture_rules.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_endpoint_wiring.py",
        "likely_fix_shape": (
            "Move service construction into composition-root wiring and inject the resolved "
            "service through a typed alias."
        ),
        "next_checks": [
            "uv run python scripts/validate_endpoint_wiring.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once validate_endpoint_wiring.py passes and the endpoint contains "
            "no direct application service construction."
        ),
    },
    "endpoint.router_not_registered": {
        "meaning": (
            "An endpoint module declares an APIRouter with route handlers, but that router is "
            "never included in project/infrastructure/api/router_registration.py, so every "
            "route on it is unreachable at runtime while all gates still pass."
        ),
        "suggested_fix": (
            "Import the router in project/infrastructure/api/router_registration.py and add an "
            "app.include_router(...) call for it inside include_application_routers()."
        ),
        "read_first": [
            "project/infrastructure/api/router_registration.py",
            "CLAUDE.md",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_endpoint_wiring.py",
        "likely_fix_shape": (
            "Add one import line and one app.include_router(<router>) call in "
            "include_application_routers()."
        ),
        "next_checks": [
            "uv run python scripts/validate_endpoint_wiring.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once validate_endpoint_wiring.py passes and the route answers "
            "through the assembled FastAPI application."
        ),
    },
    "endpoint.syntax_error": {
        "meaning": (
            "An endpoint module could not be parsed because it contains a SyntaxError. "
            "Surfaced as a structured issue so the agent loop sees a normal rule_id payload."
        ),
        "suggested_fix": (
            "Open the endpoint module at the reported line and repair the syntax. Other "
            "endpoint-wiring rules apply only after parsing succeeds."
        ),
        "read_first": [
            "the file referenced by the issue",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_endpoint_wiring.py",
        "likely_fix_shape": ("Restore valid Python syntax on the reported line."),
        "next_checks": [
            "uv run python scripts/validate_endpoint_wiring.py",
        ],
        "stop_widening_condition": ("Stop widening once the endpoint file parses cleanly."),
    },
    "endpoint.read_error": {
        "meaning": (
            "An endpoint module could not be decoded as UTF-8 by validate_endpoint_wiring.py."
        ),
        "suggested_fix": ("Re-save the file as UTF-8."),
        "read_first": [
            "the file referenced by the issue",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_endpoint_wiring.py",
        "likely_fix_shape": ("Restore valid UTF-8 encoding."),
        "next_checks": [
            "uv run python scripts/validate_endpoint_wiring.py",
        ],
        "stop_widening_condition": ("Stop widening once the file is readable."),
    },
}


# DATACLASS: validate_endpoint_wiring.EndpointWiringIssue
# SUMMARY: Structured representation of a single endpoint-wiring contract violation.
@dataclass(slots=True)
class EndpointWiringIssue:
    # ATTRIBUTE: path (Path)
    # SUMMARY: Endpoint module path containing the violation.
    path: Path

    # ATTRIBUTE: line (int)
    # SUMMARY: 1-based line number where the violation was detected.
    line: int

    # ATTRIBUTE: rule_id (str)
    # SUMMARY: Stable rule identifier for automation-friendly remediation.
    rule_id: str

    # ATTRIBUTE: message (str)
    # SUMMARY: Human-readable explanation of the broken endpoint-wiring contract.
    message: str

    # ATTRIBUTE: category (str)
    # SUMMARY: Top-level issue category used in structured validator output.
    category: str = "endpoint_wiring"


# FUNCTION: _is_endpoint_module
# SUMMARY: Report whether a repository-relative path belongs to the validated FastAPI endpoint module set.
# INPUT: path (Path): Repository-relative file path.
# OUTPUT: (bool): True when the file is an endpoint module under project/infrastructure/api/endpoints.
def _is_endpoint_module(path: Path) -> bool:
    return (
        path.parts[:4] == ("project", "infrastructure", "api", "endpoints")
        and path.suffix == ".py"
        and path.name != "__init__.py"
    )


# FUNCTION: _build_import_map
# SUMMARY: Build a local symbol-to-module map for top-level imports in an endpoint module.
# OUTPUT: (dict[str, str]): Imported symbol names mapped to their fully qualified modules.
def _build_import_map(tree: ast.AST) -> dict[str, str]:
    imports: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_name = alias.asname or alias.name.rsplit(".", 1)[-1]
                imports[imported_name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                imported_name = alias.asname or alias.name
                imports[imported_name] = f"{node.module}.{alias.name}"
    return imports


# FUNCTION: _service_contracts
# SUMMARY: Extract the application-service modules, service types, and dependency alias registry from the context map.
# OUTPUT: (tuple[set[str], set[str], dict[str, dict[str, str]]]): Service module paths, service type names, and alias registry.
def _service_contracts(
    context_map: dict[str, object],
) -> tuple[set[str], set[str], dict[str, dict[str, str]]]:
    service_modules: set[str] = set()
    service_types: set[str] = set()
    for service in context_map["service_registry"].values():
        module_name = service.get("module")
        class_name = service.get("class")
        if not isinstance(module_name, str) or not module_name.startswith("project.application."):
            continue
        if not isinstance(class_name, str) or not class_name:
            continue
        service_modules.add(module_name)
        service_types.add(class_name)
    return service_modules, service_types, context_map["dependency_registry"]["aliases"]


# FUNCTION: _router_definitions
# SUMMARY: Extract APIRouter variable names declared in an endpoint module with their line numbers.
# OUTPUT: (dict[str, int]): Router variable names mapped to their 1-based assignment lines.
def _router_definitions(tree: ast.AST) -> dict[str, int]:
    routers: dict[str, int] = {}
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        if isinstance(node.value.func, ast.Name) and node.value.func.id == "APIRouter":
            routers[node.targets[0].id] = node.lineno
    return routers


# FUNCTION: collect_registered_router_qualnames
# SUMMARY: Collect fully qualified router names that router_registration.py actually includes on the app.
# INPUT: repo_root (Path): Repository root containing project/infrastructure/api/router_registration.py.
# OUTPUT: (set[str]): Dotted "module.router_name" entries reachable through include_router calls.
def collect_registered_router_qualnames(repo_root: Path) -> set[str]:
    registration_path = repo_root / REGISTRATION_RELATIVE_PATH
    try:
        source = registration_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(registration_path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        # **LOGIC_STEP**: An unreadable registration module is reported by other rules and
        # by the import-time failure itself; treat it as registering nothing rather than
        # crashing the whole validator run.
        return set()

    import_map = _build_import_map(tree)
    registered: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "include_router":
            continue
        for argument in node.args:
            if isinstance(argument, ast.Name):
                registered.add(import_map.get(argument.id, argument.id))
            elif isinstance(argument, ast.Attribute) and isinstance(argument.value, ast.Name):
                module_path = import_map.get(argument.value.id, argument.value.id)
                registered.add(f"{module_path}.{argument.attr}")
    return registered


# FUNCTION: _route_handler_routers
# SUMMARY: Report which known APIRouter variables a function is bound to as a route handler.
# OUTPUT: (set[str]): Router variable names whose HTTP-method decorator wraps this function.
def _route_handler_routers(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    routers: set[str],
) -> set[str]:
    bound: set[str] = set()
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        if not isinstance(decorator.func, ast.Attribute):
            continue
        if not isinstance(decorator.func.value, ast.Name):
            continue
        if decorator.func.value.id not in routers:
            continue
        if decorator.func.attr.lower() in {"get", "post", "put", "patch", "delete"}:
            bound.add(decorator.func.value.id)
    return bound


# FUNCTION: _iter_parameters
# SUMMARY: Yield route-handler parameters with their aligned default values.
# OUTPUT: (list[tuple[ast.arg, ast.AST | None]]): Parameters paired with optional defaults.
def _iter_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[ast.arg, ast.AST | None]]:
    parameters: list[tuple[ast.arg, ast.AST | None]] = []
    positional = node.args.posonlyargs + node.args.args
    positional_defaults: list[ast.AST | None] = [None] * (
        len(positional) - len(node.args.defaults)
    ) + list(node.args.defaults)
    parameters.extend(zip(positional, positional_defaults))
    parameters.extend(zip(node.args.kwonlyargs, node.args.kw_defaults))
    return parameters


# FUNCTION: _is_depends_call
# SUMMARY: Report whether a default-value expression is a FastAPI Depends call.
# OUTPUT: (bool): True when the node is a Depends(...) call.
def _is_depends_call(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id == "Depends"
    if isinstance(node.func, ast.Attribute):
        return node.func.attr == "Depends"
    return False


# FUNCTION: get_endpoint_rule_playbook
# SUMMARY: Return the shared remediation playbook for a stable endpoint-wiring rule ID.
# OUTPUT: (dict[str, object] | None): Remediation metadata or None when the rule is unknown.
def get_endpoint_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _ENDPOINT_RULE_PLAYBOOKS.get(rule_id)
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


# FUNCTION: _issue_to_payload
# SUMMARY: Convert an endpoint-wiring issue into a JSON-serializable remediation payload.
# INPUT: repo_root (Path): Repository root used for relative-path rendering.
def _issue_to_payload(issue: EndpointWiringIssue, repo_root: Path) -> dict[str, object]:
    playbook = get_endpoint_rule_playbook(issue.rule_id)
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category=issue.category,
        file=str(issue.path.relative_to(repo_root)),
        line=issue.line,
        message=issue.message,
        playbook=playbook,
    )


# FUNCTION: validate_endpoint_module
# SUMMARY: Validate a single endpoint module against the endpoint-facing wiring contract.
# INPUT: path (Path): Absolute endpoint-module path.
# INPUT: context_map (dict[str, object]): Context map providing service and alias contracts.
def validate_endpoint_module(
    path: Path,
    repo_root: Path,
    context_map: dict[str, object],
    registered_routers: set[str] | None = None,
) -> list[EndpointWiringIssue]:
    # **LOGIC_STEP**: Read & parse source under guarded exceptions so a broken file
    # surfaces as a structured EndpointWiringIssue rather than a raw Python traceback.
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        return [
            EndpointWiringIssue(
                path=path,
                line=1,
                rule_id="endpoint.read_error",
                message=f"UnicodeDecodeError while reading source: {error.reason}",
            )
        ]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        return [
            EndpointWiringIssue(
                path=path,
                line=error.lineno or 1,
                rule_id="endpoint.syntax_error",
                message=f"SyntaxError while parsing source: {error.msg}",
            )
        ]
    import_map = _build_import_map(tree)
    router_definitions = _router_definitions(tree)
    routers = set(router_definitions)
    service_modules, service_types, alias_registry = _service_contracts(context_map)
    known_service_keys = set(context_map["service_registry"].keys())
    service_module_roots = {module.rsplit(".", 1)[0] for module in service_modules}
    issues: list[EndpointWiringIssue] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                if f"{node.module}.{alias.name}" in service_modules:
                    issues.append(
                        EndpointWiringIssue(
                            path=path,
                            line=node.lineno,
                            rule_id="endpoint.no_direct_service_import",
                            message=(
                                "Endpoint modules must not import application services directly; "
                                "use typed dependency aliases instead."
                            ),
                        )
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in service_module_roots:
                    issues.append(
                        EndpointWiringIssue(
                            path=path,
                            line=node.lineno,
                            rule_id="endpoint.no_direct_service_import",
                            message=(
                                "Endpoint modules must not import application service modules directly; "
                                "use typed dependency aliases instead."
                            ),
                        )
                    )
                if alias.name.startswith("project.application.") and any(
                    alias.name == module.rsplit(".", 1)[0] for module in service_modules
                ):
                    issues.append(
                        EndpointWiringIssue(
                            path=path,
                            line=node.lineno,
                            rule_id="endpoint.no_direct_service_import",
                            message=(
                                "Endpoint modules must not import application service modules directly; "
                                "use typed dependency aliases instead."
                            ),
                        )
                    )

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            imported_target = import_map.get(node.func.id)
            if imported_target in service_modules:
                issues.append(
                    EndpointWiringIssue(
                        path=path,
                        line=node.lineno,
                        rule_id="endpoint.no_direct_service_construction",
                        message=(
                            "Endpoint modules must not instantiate application services directly; "
                            "route through typed dependency aliases and composition-root wiring."
                        ),
                    )
                )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                module_path = import_map.get(node.func.value.id)
                if module_path and f"{module_path}.{node.func.attr}" in service_modules:
                    issues.append(
                        EndpointWiringIssue(
                            path=path,
                            line=node.lineno,
                            rule_id="endpoint.no_direct_service_construction",
                            message=(
                                "Endpoint modules must not construct application services directly; "
                                "resolve them through typed dependency aliases."
                            ),
                        )
                    )

    routers_with_handlers: set[str] = set()
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound_routers = _route_handler_routers(node, routers)
        if not bound_routers:
            continue
        routers_with_handlers |= bound_routers

        for parameter, default in _iter_parameters(node):
            if not isinstance(parameter.annotation, ast.Name):
                if _is_depends_call(default):
                    issues.append(
                        EndpointWiringIssue(
                            path=path,
                            line=parameter.lineno,
                            rule_id="endpoint.no_depends_without_alias",
                            message=(
                                "Endpoint parameters that use Depends(...) must be declared with a typed "
                                "dependency alias from dependencies.py."
                            ),
                        )
                    )
                continue

            annotation_name = parameter.annotation.id
            alias_metadata = alias_registry.get(annotation_name)
            if alias_metadata is not None:
                service_key = alias_metadata["service_key"]
                if not alias_metadata["getter"] or service_key not in known_service_keys:
                    issues.append(
                        EndpointWiringIssue(
                            path=path,
                            line=parameter.lineno,
                            rule_id="endpoint.alias_chain_invalid",
                            message=(
                                f"Dependency alias '{annotation_name}' does not resolve to a known "
                                "service key through dependencies.py."
                            ),
                        )
                    )
                continue

            if _is_depends_call(default):
                issues.append(
                    EndpointWiringIssue(
                        path=path,
                        line=parameter.lineno,
                        rule_id="endpoint.no_depends_without_alias",
                        message=(
                            f"Endpoint parameter '{parameter.arg}' uses Depends(...) without a typed "
                            "dependency alias."
                        ),
                    )
                )

            if annotation_name in service_types:
                issues.append(
                    EndpointWiringIssue(
                        path=path,
                        line=parameter.lineno,
                        rule_id="endpoint.no_direct_service_annotation",
                        message=(
                            f"Endpoint parameter '{parameter.arg}' uses application service type "
                            f"'{annotation_name}' directly instead of a typed dependency alias."
                        ),
                    )
                )
                continue

            imported_target = import_map.get(annotation_name, "")
            if imported_target.startswith("project.infrastructure.api.dependencies.") and (
                annotation_name.endswith("Dep") or imported_target.endswith(annotation_name)
            ):
                issues.append(
                    EndpointWiringIssue(
                        path=path,
                        line=parameter.lineno,
                        rule_id="endpoint.alias_chain_invalid",
                        message=(
                            f"Endpoint parameter '{parameter.arg}' references unknown dependency "
                            f"alias '{annotation_name}'."
                        ),
                    )
                )

    # **LOGIC_STEP**: A router carrying handlers but never included on the app produces a
    # dead route that answers 404 in production while every gate stays green. Compare each
    # such router against what router_registration.py actually includes.
    if registered_routers is None:
        registered_routers = collect_registered_router_qualnames(repo_root)
    module_dotted = ".".join(path.relative_to(repo_root).with_suffix("").parts)
    for router_name in sorted(routers_with_handlers):
        if f"{module_dotted}.{router_name}" in registered_routers:
            continue
        issues.append(
            EndpointWiringIssue(
                path=path,
                line=router_definitions[router_name],
                rule_id="endpoint.router_not_registered",
                message=(
                    f"Router '{router_name}' carries route handlers but is never included in "
                    f"{REGISTRATION_RELATIVE_PATH.as_posix()}; its routes are unreachable."
                ),
            )
        )

    return issues


# FUNCTION: collect_endpoint_wiring_issues
# SUMMARY: Validate all endpoint modules in the repository against the endpoint-facing wiring contract.
# INPUT: context_map (dict[str, object] | None): Optional pre-built context map used by tests or callers.
def collect_endpoint_wiring_issues(
    repo_root: Path,
    context_map: dict[str, object] | None = None,
) -> list[EndpointWiringIssue]:
    if context_map is None:
        context_map = build_context_map()

    registered_routers = collect_registered_router_qualnames(repo_root)
    issues: list[EndpointWiringIssue] = []
    for path in sorted(repo_root.rglob("*.py")):
        relative_path = path.relative_to(repo_root)
        if not _is_endpoint_module(relative_path):
            continue
        issues.extend(validate_endpoint_module(path, repo_root, context_map, registered_routers))
    return issues


# FUNCTION: main
# SUMMARY: Run endpoint-wiring validation from the repository root and return a process exit code.
# OUTPUT: (int): Zero when validation succeeds and non-zero otherwise.
def main() -> int:
    parser = argparse.ArgumentParser(description="Validate endpoint-facing wiring contracts.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured machine-readable validation results.",
    )
    args = parser.parse_args()

    issues = collect_endpoint_wiring_issues(ROOT_DIR)
    if not issues:
        if args.json:
            print(render_json({"status": "ok", "issues": []}), end="")
        else:
            print("Endpoint wiring validation passed.")
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
# SUMMARY: Script entrypoint for the endpoint-wiring validator.
if __name__ == "__main__":
    raise SystemExit(main())
