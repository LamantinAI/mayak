#!/usr/bin/env python3
# FILE: validate_endpoint_wiring.py
# SUMMARY: Checks the chain from an endpoint to its service: typed alias, getter, registered service key, included router.
# The facts come from the wiring files' own source, read from the repository being checked. A
# generated map used to supply them, and it was only as fresh as its last refresh — and a copy of
# the template, validated through the template's editable install, read the template's services.

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
from pathlib import Path

from validation_support.rendering import render_json
from validation_support.validator_contract import build_validator_issue_payload


# Absolute repository root used by the validator entrypoint.
ROOT_DIR = Path(__file__).resolve().parent.parent

# Repository-relative module where every application router must be included on the app.
REGISTRATION_RELATIVE_PATH = Path("project/infrastructure/api/router_registration.py")

# Where services are registered: the kernel's shared ones, then each vertical's.
SERVICE_REGISTRY_PATHS = (
    Path("project/core/composition_root.py"),
    Path("project/core/service_registration.py"),
)

# Where each typed alias names its getter and each getter the service key it returns.
DEPENDENCIES_RELATIVE_PATH = Path("project/infrastructure/api/dependencies.py")

_ENDPOINTS_PACKAGE = "project.infrastructure.api.endpoints."

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
            "AGENTS.md",
            "project/infrastructure/api/dependencies.py",
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
            "AGENTS.md",
            "project/infrastructure/api/dependencies.py",
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
            "AGENTS.md",
            "project/infrastructure/api/dependencies.py",
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
            "AGENTS.md",
            "project/infrastructure/api/dependencies.py",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_endpoint_wiring.py",
        "likely_fix_shape": (
            "Align the alias, getter, and service key with the actual service registration in "
            "dependencies.py and the composition-root wiring files."
        ),
        "next_checks": [
            "uv run python scripts/validate_endpoint_wiring.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once validate_endpoint_wiring.py passes and the alias resolves to a "
            "registered service key."
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
            "AGENTS.md",
            "project/infrastructure/api/dependencies.py",
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
            "AGENTS.md",
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
    "endpoint.wiring_chain_broken": {
        "meaning": (
            "dependencies.py declares an alias whose getter it does not define, or a getter "
            "returning a service key that neither composition_root.py nor service_registration.py "
            "registers. Reported even when no route uses the alias yet: the first one to use it "
            "would fail at request time."
        ),
        "suggested_fix": (
            "Make each link resolve: the alias's Depends(...) names a getter defined in "
            "dependencies.py, and the getter's key is one a registry dict carries."
        ),
        "read_first": [
            "project/infrastructure/api/dependencies.py",
            "project/core/service_registration.py",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_endpoint_wiring.py",
        "likely_fix_shape": (
            "Fix the key string in the getter's _get_service(request, key, Type) call, or add "
            "the service under that key in the registry dict."
        ),
        "next_checks": [
            "uv run python scripts/validate_endpoint_wiring.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once every alias in dependencies.py resolves to a registered key."
        ),
    },
    "endpoint.router_module_missing": {
        "meaning": (
            "router_registration.py imports an endpoint module that does not exist, so the "
            "application fails to import at startup."
        ),
        "suggested_fix": (
            "Restore the endpoint module or remove its import and include_router call from "
            "project/infrastructure/api/router_registration.py."
        ),
        "read_first": [
            "project/infrastructure/api/router_registration.py",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_endpoint_wiring.py",
        "likely_fix_shape": "Point the import at the module's real name, or delete both lines.",
        "next_checks": [
            "uv run python scripts/validate_endpoint_wiring.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once every endpoint module router_registration.py imports exists."
        ),
    },
    "endpoint.syntax_error": {
        "meaning": (
            "An endpoint or wiring module could not be parsed because it contains a SyntaxError. "
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


# Structured representation of a single endpoint-wiring contract violation.
@dataclass(slots=True)
class EndpointWiringIssue:
    # Endpoint module path containing the violation.
    path: Path

    # 1-based line number where the violation was detected.
    line: int

    # Stable rule identifier for automation-friendly remediation.
    rule_id: str

    # Human-readable explanation of the broken endpoint-wiring contract.
    message: str

    # Top-level issue category used in structured validator output.
    category: str = "endpoint_wiring"


# Report whether a repository-relative path belongs to the validated FastAPI endpoint module set.
# path: Repository-relative file path.
# Returns: True when the file is an endpoint module under project/infrastructure/api/endpoints.
def _is_endpoint_module(path: Path) -> bool:
    return (
        path.parts[:4] == ("project", "infrastructure", "api", "endpoints")
        and path.suffix == ".py"
        and path.name != "__init__.py"
    )


# Build a local symbol-to-module map for top-level imports in an endpoint module.
# Returns: Imported symbol names mapped to their fully qualified modules.
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


# Parse a Python file, turning an unreadable one into an issue. A missing file parses as nothing.
def _parse(path: Path) -> tuple[ast.Module | None, EndpointWiringIssue | None]:
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except UnicodeDecodeError as error:
        message = f"UnicodeDecodeError while reading source: {error.reason}"
        return None, EndpointWiringIssue(path, 1, "endpoint.read_error", message)
    try:
        return ast.parse(source, filename=str(path)), None
    except SyntaxError as error:
        message = f"SyntaxError while parsing source: {error.msg}"
        return None, EndpointWiringIssue(path, error.lineno or 1, "endpoint.syntax_error", message)


def _assignments(tree: ast.AST) -> list[tuple[str, ast.expr]]:
    pairs: list[tuple[str, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target: ast.expr = node.targets[0]
            value: ast.expr | None = node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and value is not None:
            pairs.append((target.id, value))
    return pairs


# "module.Class" a constructor call builds, through the file's imports; None when opaque.
def _call_target(call: ast.Call, import_map: dict[str, str]) -> str | None:
    if isinstance(call.func, ast.Name):
        return import_map.get(call.func.id)
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        return f"{import_map.get(call.func.value.id, call.func.value.id)}.{call.func.attr}"
    return None


# Every key a string and every value something that can BE a service: a name bound elsewhere, a
# constructor call, or None for a service the project turned off. Without this any function
# returning any mapping — request headers, an error body — would register services.
def _looks_like_a_service_registry(node: ast.Dict) -> bool:
    return bool(node.keys) and all(
        isinstance(key, ast.Constant)
        and isinstance(key.value, str)
        and (
            isinstance(value, (ast.Name, ast.Call))
            or (isinstance(value, ast.Constant) and value.value is None)
        )
        for key, value in zip(node.keys, node.values)
    )


# The dicts a file registers services with: any literal bound to `services` — the kernel's own
# spelling — and whatever a function returns that looks like a registry, for projects that spell
# it differently. Returned names are looked up per function: two builders may both call theirs
# `registry`.
def _registry_dicts(tree: ast.Module) -> list[ast.Dict]:
    found = [value for name, value in _assignments(tree) if name == "services"]
    dicts = [value for value in found if isinstance(value, ast.Dict)]
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local = {name: value for name, value in _assignments(function)}
        for node in ast.walk(function):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            returned = local.get(node.value.id) if isinstance(node.value, ast.Name) else node.value
            if isinstance(returned, ast.Dict) and _looks_like_a_service_registry(returned):
                if all(returned is not known for known in dicts):
                    dicts.append(returned)
    return dicts


# Service key -> "module.Class" it is built from. A name in the dict resolves through any
# constructor call bound to it anywhere in the file, so a service declared None and built inside
# a branch — or an `else` — still resolves.
def _services(tree: ast.Module) -> dict[str, str | None]:
    import_map = _build_import_map(tree)
    built: dict[str, str | None] = {}
    for name, value in _assignments(tree):
        if isinstance(value, ast.Call):
            built.setdefault(name, _call_target(value, import_map))
    services: dict[str, str | None] = {}
    for registry in _registry_dicts(tree):
        for key, value in zip(registry.keys, registry.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                continue
            if isinstance(value, ast.Name):
                services[key.value] = built.get(value.id)
            else:
                services[key.value] = (
                    _call_target(value, import_map) if isinstance(value, ast.Call) else None
                )
    return services


# The service key a getter returns: `return _get_service(request, "key", Type)`, or a nullable
# getter (`-> T | None`) that reads `services.get("key")`.
def _getter_service_key(function: ast.FunctionDef) -> str | None:
    for statement in function.body:
        call = statement.value if isinstance(statement, ast.Return) else None
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_get_service"
            and len(call.args) >= 2
            and isinstance(call.args[1], ast.Constant)
            and isinstance(call.args[1].value, str)
        ):
            return call.args[1].value
    returns = function.returns
    if not isinstance(returns, ast.BinOp) or not any(
        isinstance(side, ast.Constant) and side.value is None
        for side in (returns.left, returns.right)
    ):
        return None
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "services"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node.args[0].value
    return None


# The getter an `Alias = Annotated[T, Depends(getter)]` assignment names.
def _alias_getter(node: ast.stmt) -> tuple[str, str] | None:
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return None
    target, value = node.targets[0], node.value
    if not isinstance(target, ast.Name) or not isinstance(value, ast.Subscript):
        return None
    if not isinstance(value.value, ast.Name) or value.value.id != "Annotated":
        return None
    arguments = value.slice.elts if isinstance(value.slice, ast.Tuple) else []
    depends = arguments[1] if len(arguments) >= 2 else None
    if (
        isinstance(depends, ast.Call)
        and isinstance(depends.func, ast.Name)
        and depends.func.id == "Depends"
        and depends.args
        and isinstance(depends.args[0], ast.Name)
    ):
        return target.id, depends.args[0].id
    return None


# What the wiring files declare, read from the repository being checked.
@dataclass(slots=True)
class WiringFacts:
    # Service key -> "module.Class" it is built from, or None when that is not a constructor call.
    services: dict[str, str | None] = field(default_factory=dict)

    # Getter name -> (service key it returns, line in dependencies.py).
    getters: dict[str, tuple[str, int]] = field(default_factory=dict)

    # Typed alias -> (getter it names, line in dependencies.py).
    aliases: dict[str, tuple[str, int]] = field(default_factory=dict)

    # Wiring files that could not be read or parsed.
    issues: list[EndpointWiringIssue] = field(default_factory=list)

    # The application-service classes, as "module.Class", an endpoint must reach only by alias.
    def service_classes(self) -> set[str]:
        return {
            target
            for target in self.services.values()
            if target is not None and target.startswith("project.application.")
        }

    # The service key an alias resolves to, or None where the chain breaks.
    def alias_service_key(self, alias: str) -> str | None:
        getter = self.getters.get(self.aliases[alias][0])
        return getter[0] if getter is not None and getter[0] in self.services else None


def read_wiring_facts(repo_root: Path) -> WiringFacts:
    facts = WiringFacts()
    for relative in SERVICE_REGISTRY_PATHS:
        tree, issue = _parse(repo_root / relative)
        facts.issues.extend([issue] if issue else [])
        facts.services.update(_services(tree) if tree else {})
    tree, issue = _parse(repo_root / DEPENDENCIES_RELATIVE_PATH)
    facts.issues.extend([issue] if issue else [])
    for node in tree.body if tree else []:
        if isinstance(node, ast.FunctionDef) and (key := _getter_service_key(node)) is not None:
            facts.getters[node.name] = (key, node.lineno)
        elif (alias := _alias_getter(node)) is not None:
            facts.aliases[alias[0]] = (alias[1], node.lineno)
    return facts


# Links that break whether or not a route uses them yet: an alias naming an undefined getter, a
# getter returning an unregistered key, and an imported endpoint module that does not exist.
def _chain_issues(repo_root: Path, facts: WiringFacts) -> list[EndpointWiringIssue]:
    dependencies = repo_root / DEPENDENCIES_RELATIVE_PATH
    issues: list[EndpointWiringIssue] = []
    for alias, (getter, line) in facts.aliases.items():
        if getter not in facts.getters:
            message = (
                f"Alias '{alias}' depends on '{getter}', which is not a getter dependencies.py "
                "defines in the _get_service(request, key, Type) shape."
            )
            issues.append(
                EndpointWiringIssue(dependencies, line, "endpoint.wiring_chain_broken", message)
            )
    for getter, (key, line) in facts.getters.items():
        if key not in facts.services:
            message = (
                f"Getter '{getter}' returns service key '{key}', which no registry dict in "
                f"{' or '.join(path.name for path in SERVICE_REGISTRY_PATHS)} carries."
            )
            issues.append(
                EndpointWiringIssue(dependencies, line, "endpoint.wiring_chain_broken", message)
            )
    registration = repo_root / REGISTRATION_RELATIVE_PATH
    tree, _ = _parse(registration)
    for node in ast.walk(tree) if tree else []:
        if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith(
            _ENDPOINTS_PACKAGE
        ):
            continue
        module_path = repo_root / Path(*str(node.module).split(".")).with_suffix(".py")
        if not module_path.exists():
            message = (
                f"Imports {node.module}, and {module_path.relative_to(repo_root)} does not exist."
            )
            issues.append(
                EndpointWiringIssue(
                    registration, node.lineno, "endpoint.router_module_missing", message
                )
            )
    return issues


# Extract APIRouter variable names declared in an endpoint module with their line numbers.
# Returns: Router variable names mapped to their 1-based assignment lines.
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


# Collect fully qualified router names that router_registration.py actually includes on the app.
# repo_root: Repository root containing project/infrastructure/api/router_registration.py.
# Returns: Dotted "module.router_name" entries reachable through include_router calls.
def collect_registered_router_qualnames(repo_root: Path) -> set[str]:
    registration_path = repo_root / REGISTRATION_RELATIVE_PATH
    try:
        source = registration_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(registration_path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        # An unreadable registration module is reported by other rules and
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


# Report which known APIRouter variables a function is bound to as a route handler.
# Returns: Router variable names whose HTTP-method decorator wraps this function.
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


# Yield route-handler parameters with their aligned default values.
# Returns: Parameters paired with optional defaults.
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


# Report whether a default-value expression is a FastAPI Depends call.
# Returns: True when the node is a Depends(...) call.
def _is_depends_call(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id == "Depends"
    if isinstance(node.func, ast.Attribute):
        return node.func.attr == "Depends"
    return False


# Return the shared remediation playbook for a stable endpoint-wiring rule ID.
# Returns: Remediation metadata or None when the rule is unknown.
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


# Convert an endpoint-wiring issue into a JSON-serializable remediation payload.
# repo_root: Repository root used for relative-path rendering.
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


# Validate a single endpoint module against the endpoint-facing wiring contract.
# path: Absolute endpoint-module path.
def validate_endpoint_module(
    path: Path,
    repo_root: Path,
    facts: WiringFacts,
    registered_routers: set[str] | None = None,
) -> list[EndpointWiringIssue]:
    tree, unreadable = _parse(path)
    if tree is None:
        return [unreadable] if unreadable else []
    import_map = _build_import_map(tree)
    router_definitions = _router_definitions(tree)
    routers = set(router_definitions)
    service_modules = facts.service_classes()
    service_types = {module.rsplit(".", 1)[-1] for module in service_modules}
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
            if annotation_name in facts.aliases:
                if facts.alias_service_key(annotation_name) is None:
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

    # A router carrying handlers but never included on the app produces a
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


# Validate the wiring files and every endpoint module in the repository.
def collect_endpoint_wiring_issues(repo_root: Path) -> list[EndpointWiringIssue]:
    facts = read_wiring_facts(repo_root)
    registered_routers = collect_registered_router_qualnames(repo_root)
    issues = [*facts.issues, *_chain_issues(repo_root, facts)]
    for path in sorted(repo_root.rglob("*.py")):
        relative_path = path.relative_to(repo_root)
        if not _is_endpoint_module(relative_path):
            continue
        issues.extend(validate_endpoint_module(path, repo_root, facts, registered_routers))
    return issues


# Run endpoint-wiring validation from the repository root and return a process exit code.
# Returns: Zero when validation succeeds and non-zero otherwise.
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


# Script entrypoint for the endpoint-wiring validator.
if __name__ == "__main__":
    raise SystemExit(main())
