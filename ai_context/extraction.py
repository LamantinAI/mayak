# SUMMARY: AST-based extraction helpers for AI-facing repository context artifacts.

from __future__ import annotations

import ast
from pathlib import Path

from ai_context.constants import ROOT_DIR
from ai_context.errors import ContextBuildError, ContextIssue


def load_python_tree(path: Path) -> ast.AST:
    source = path.read_text(encoding="utf-8")
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError as error:
        try:
            display_path = path.relative_to(ROOT_DIR).as_posix()
        except ValueError:
            display_path = path.as_posix()
        raise ContextBuildError(
            ContextIssue(
                issue_type="syntax_error",
                path=path,
                line=error.lineno or 1,
                message=(f"SyntaxError while parsing {display_path}: {error.msg}"),
                recommended_next_command="uv run python scripts/generate_ai_context.py --check --json",
            )
        ) from error


def literal_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def build_import_map(tree: ast.AST) -> dict[str, str]:
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


def collect_named_dependencies(call: ast.Call) -> list[str]:
    dependencies: list[str] = []
    for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
        if not isinstance(argument, ast.Name):
            continue
        if argument.id not in dependencies:
            dependencies.append(argument.id)
    return dependencies


def call_target_metadata(
    call: ast.Call,
    import_map: dict[str, str],
) -> dict[str, object]:
    constructor_name: str | None = None
    constructor_module: str | None = None

    if isinstance(call.func, ast.Name):
        constructor_name = call.func.id
        constructor_module = import_map.get(constructor_name)
    elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        base_module = import_map.get(call.func.value.id, call.func.value.id)
        constructor_name = call.func.attr
        constructor_module = f"{base_module}.{constructor_name}"

    return {
        "class": constructor_name,
        "module": constructor_module,
        "constructor_dependencies": collect_named_dependencies(call),
    }


def service_entry_metadata(
    construction_kind: str,
    metadata: dict[str, object],
) -> dict[str, object]:
    class_name = metadata.get("class")
    module_name = metadata.get("module")
    entry = {
        "class": class_name,
        "module": module_name,
        "constructor_dependencies": metadata.get("constructor_dependencies", []),
        "construction_kind": construction_kind,
    }
    if class_name and module_name:
        entry["resolution_status"] = "resolved"
        entry["confidence"] = "high"
        entry["source"] = "ast_exact"
        return entry

    entry["resolution_status"] = "partial"
    entry["confidence"] = "low"
    entry["source"] = "ast_heuristic"
    entry["reason"] = (
        "Service wiring uses an opaque expression or value that cannot be resolved to a constructor target."
    )
    return entry


def assignment_targets_variable(
    node: ast.Assign | ast.AnnAssign,
    variable_name: str,
) -> bool:
    if isinstance(node, ast.Assign):
        return any(
            isinstance(target, ast.Name) and target.id == variable_name for target in node.targets
        )
    return isinstance(node.target, ast.Name) and node.target.id == variable_name


def looks_like_a_service_registry(node: ast.Dict) -> bool:
    # Every key a string and every value something that can BE a service: a name bound elsewhere,
    # a constructor call, or `None` for a service the project turned off. Without this, any
    # function in the file returning any mapping — request headers, an error body, a kwargs
    # bundle — became service entries in docs/ai_context_map.json, and nothing would have gone
    # red, because that map is generated and so agrees with itself.
    if not node.keys:
        return False
    for key_node, value_node in zip(node.keys, node.values):
        if literal_str(key_node) is None:
            return False
        if isinstance(value_node, (ast.Name, ast.Call)):
            continue
        if isinstance(value_node, ast.Constant) and value_node.value is None:
            continue
        return False
    return True


def returned_registry_dicts(tree: ast.AST) -> list[ast.Dict]:
    # Read what each function RETURNS, one function at a time. `services = {...}` is the kernel's
    # own spelling, not a rule the language enforces, so a builder returning the literal inline or
    # binding it to another name is equally legal — and used to extract to nothing.
    # Per function, not per module: two builders in one file may both call their local mapping
    # `registry`, and a single file-wide name -> literal map would let the second silently answer
    # for the first.
    literals: list[ast.Dict] = []
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bindings: dict[str, ast.Dict] = {}
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target: ast.AST = node.targets[0]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                target = node.target
                value = node.value
            else:
                continue
            if isinstance(target, ast.Name) and isinstance(value, ast.Dict):
                bindings[target.id] = value
        for node in ast.walk(scope):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            returned: ast.Dict | None = None
            if isinstance(node.value, ast.Dict):
                returned = node.value
            elif isinstance(node.value, ast.Name):
                returned = bindings.get(node.value.id)
            if returned is not None and looks_like_a_service_registry(returned):
                literals.append(returned)
    return literals


def extract_service_registry_entries(
    path: Path,
    root_dir: Path,
    category: str,
) -> dict[str, dict[str, object]]:
    tree = load_python_tree(path)
    import_map = build_import_map(tree)
    relative_path = path.relative_to(root_dir)
    binding_metadata: dict[str, dict[str, object]] = {}
    service_entries: dict[str, dict[str, object]] = {}

    for node in ast.walk(tree):
        value: ast.AST | None = None
        target_name: str | None = None

        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                target_name = target.id
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
            value = node.value

        if target_name is None or value is None:
            continue

        if isinstance(value, ast.Call):
            binding_metadata[target_name] = {
                "construction_kind": "bound_variable",
                "metadata": call_target_metadata(value, import_map),
            }
            continue

        binding_metadata[target_name] = {
            "construction_kind": "opaque_expression",
            "metadata": {
                "class": None,
                "module": None,
                "constructor_dependencies": [],
            },
        }

    # Two ways in, and the first is the kernel's own: a variable named `services`, which
    # composition_root.py assigns in one function and reads in another. The second is whatever a
    # builder returns, for projects that spell it differently — see returned_registry_dicts.
    registry_dicts: list[ast.Dict] = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and assignment_targets_variable(node, "services")
        and isinstance(node.value, ast.Dict)
    ]
    for returned in returned_registry_dicts(tree):
        # `not in` on AST nodes is identity, which is what is wanted: the same literal reached
        # twice — assigned to `services`, then returned — must be walked once.
        if returned not in registry_dicts:
            registry_dicts.append(returned)

    for registry_dict in registry_dicts:
        for key_node, value_node in zip(registry_dict.keys, registry_dict.values):
            service_key = literal_str(key_node)
            if service_key is None:
                continue
            entry: dict[str, object] = {
                "category": category,
                "source_file": str(relative_path),
            }
            if isinstance(value_node, ast.Name):
                entry["factory_variable"] = value_node.id
                variable_metadata = binding_metadata.get(
                    value_node.id,
                    {
                        "construction_kind": "opaque_expression",
                        "metadata": {
                            "class": None,
                            "module": None,
                            "constructor_dependencies": [],
                        },
                    },
                )
                entry.update(
                    service_entry_metadata(
                        str(variable_metadata["construction_kind"]),
                        dict(variable_metadata["metadata"]),
                    )
                )
            elif isinstance(value_node, ast.Call):
                entry.update(
                    service_entry_metadata(
                        "direct_call",
                        call_target_metadata(value_node, import_map),
                    )
                )
            else:
                entry.update(
                    service_entry_metadata(
                        "opaque_expression",
                        {
                            "class": None,
                            "module": None,
                            "constructor_dependencies": [],
                        },
                    )
                )
            service_entries[service_key] = entry

    return dict(sorted(service_entries.items()))


def extract_router_modules(router_registration_path: Path) -> list[str]:
    tree = load_python_tree(router_registration_path)
    modules: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module is None:
            continue
        if not node.module.startswith("project.infrastructure.api.endpoints."):
            continue
        modules.append(node.module.rsplit(".", 1)[-1])
    return sorted(dict.fromkeys(modules))


def extract_dependency_registry(
    dependencies_path: Path,
) -> dict[str, dict[str, dict[str, object]]]:
    tree = load_python_tree(dependencies_path)
    import_map = build_import_map(tree)
    getters: dict[str, dict[str, object]] = {}
    aliases: dict[str, dict[str, object]] = {}

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        # Pattern 1: return _get_service(request, "key", ServiceType)
        for statement in node.body:
            if not isinstance(statement, ast.Return):
                continue
            if not isinstance(statement.value, ast.Call):
                continue
            if not isinstance(statement.value.func, ast.Name):
                continue
            if statement.value.func.id != "_get_service":
                continue
            if len(statement.value.args) < 3:
                continue
            service_key = literal_str(statement.value.args[1])
            service_type_node = statement.value.args[2]
            if service_key is None or not isinstance(service_type_node, ast.Name):
                continue
            getters[node.name] = {
                "service_key": service_key,
                "service_type": service_type_node.id,
                "service_module": import_map.get(service_type_node.id, ""),
                "resolution_status": "resolved",
                "confidence": "high",
                "source": "ast_exact",
            }
        # Pattern 2: nullable getter that does services.get("key") and returns it.
        # Must have an explicit return-type annotation of the form `T | None` so we
        # can recover the service type without scanning the body.
        if node.name in getters:
            continue
        if not isinstance(node.returns, ast.BinOp) or not isinstance(node.returns.op, ast.BitOr):
            continue
        left = node.returns.left
        right = node.returns.right
        none_node = next(
            (n for n in (left, right) if isinstance(n, ast.Constant) and n.value is None),
            None,
        )
        type_node = next(
            (n for n in (left, right) if isinstance(n, ast.Name)),
            None,
        )
        if none_node is None or type_node is None:
            continue
        service_key = None
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "get"
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == "services"
                and sub.args
            ):
                key_literal = literal_str(sub.args[0])
                if key_literal:
                    service_key = key_literal
                    break
        if service_key is None:
            continue
        getters[node.name] = {
            "service_key": service_key,
            "service_type": type_node.id,
            "service_module": import_map.get(type_node.id, ""),
            "resolution_status": "resolved",
            "confidence": "high",
            "source": "ast_exact",
        }

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        alias_name = node.targets[0].id
        value = node.value
        if not isinstance(value, ast.Subscript):
            continue
        if not isinstance(value.value, ast.Name) or value.value.id != "Annotated":
            continue
        annotated_args = value.slice.elts if isinstance(value.slice, ast.Tuple) else [value.slice]
        if len(annotated_args) < 2:
            continue
        service_type_node = annotated_args[0]
        depends_call = annotated_args[1]
        # Unwrap nullable type aliases: `T | None` → `T`
        if isinstance(service_type_node, ast.BinOp) and isinstance(service_type_node.op, ast.BitOr):
            for branch in (service_type_node.left, service_type_node.right):
                if isinstance(branch, ast.Name):
                    service_type_node = branch
                    break
        if not isinstance(service_type_node, ast.Name):
            continue
        if not isinstance(depends_call, ast.Call):
            continue
        if not isinstance(depends_call.func, ast.Name) or depends_call.func.id != "Depends":
            continue
        if not depends_call.args or not isinstance(depends_call.args[0], ast.Name):
            continue
        getter_name = depends_call.args[0].id
        aliases[alias_name] = {
            "getter": getter_name,
            "service_type": service_type_node.id,
            "service_key": getters.get(getter_name, {}).get("service_key", ""),
            "service_module": getters.get(getter_name, {}).get(
                "service_module",
                import_map.get(service_type_node.id, ""),
            ),
            "resolution_status": "resolved",
            "confidence": "high",
            "source": "ast_exact",
        }

    return {
        "aliases": dict(sorted(aliases.items())),
        "getters": dict(sorted(getters.items())),
    }


def extract_env_prefixes(root_dir: Path) -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for path in sorted((root_dir / "project" / "core").glob("config*.py")):
        tree = load_python_tree(path)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if not isinstance(statement, ast.Assign):
                    continue
                if not any(
                    isinstance(target, ast.Name) and target.id == "model_config"
                    for target in statement.targets
                ):
                    continue
                call = statement.value
                if not isinstance(call, ast.Call):
                    continue
                for keyword in call.keywords:
                    if keyword.arg != "env_prefix":
                        continue
                    if isinstance(keyword.value, ast.Constant) and isinstance(
                        keyword.value.value, str
                    ):
                        prefixes[node.name] = keyword.value.value
    return dict(sorted(prefixes.items()))


def extract_route_inventory(
    root_dir: Path,
    router_registration_path: Path,
    dependency_registry: dict[str, dict[str, dict[str, object]]],
) -> dict[str, dict[str, object]]:
    alias_registry = dependency_registry["aliases"]
    route_inventory: dict[str, dict[str, object]] = {}

    for module_name in extract_router_modules(router_registration_path):
        path = root_dir / "project" / "infrastructure" / "api" / "endpoints" / f"{module_name}.py"
        tree = load_python_tree(path)
        relative_path = path.relative_to(root_dir)
        routers: dict[str, dict[str, object]] = {}

        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            if not isinstance(node.value.func, ast.Name) or node.value.func.id != "APIRouter":
                continue
            prefix = ""
            tags: list[str] = []
            for keyword in node.value.keywords:
                if keyword.arg == "prefix":
                    prefix = literal_str(keyword.value) or ""
                elif keyword.arg == "tags" and isinstance(keyword.value, ast.List):
                    tags = [
                        item.value
                        for item in keyword.value.elts
                        if isinstance(item, ast.Constant) and isinstance(item.value, str)
                    ]
            routers[node.targets[0].id] = {
                "prefix": prefix,
                "tags": tags,
                "endpoints": [],
            }

        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if not isinstance(decorator.func, ast.Attribute):
                    continue
                if not isinstance(decorator.func.value, ast.Name):
                    continue
                router_name = decorator.func.value.id
                if router_name not in routers:
                    continue
                method_name = decorator.func.attr.lower()
                if method_name not in {"get", "post", "put", "patch", "delete"}:
                    continue
                route_path = literal_str(decorator.args[0]) if decorator.args else None
                if route_path is None:
                    continue
                dependencies: list[dict[str, object]] = []
                for argument in node.args.args:
                    if not isinstance(argument.annotation, ast.Name):
                        continue
                    alias_name = argument.annotation.id
                    alias_metadata = alias_registry.get(alias_name)
                    if alias_metadata is None:
                        continue
                    dependencies.append(
                        {
                            "parameter": argument.arg,
                            "alias": alias_name,
                            "getter": alias_metadata["getter"],
                            "service_key": alias_metadata["service_key"],
                            "resolution_status": "resolved",
                            "confidence": "high",
                            "source": "ast_exact",
                        }
                    )
                prefix = str(routers[router_name]["prefix"])
                full_path = (
                    prefix
                    if route_path == "/"
                    else (
                        f"{prefix.rstrip('/')}/{route_path.lstrip('/')}" if prefix else route_path
                    )
                )
                if route_path == "/" and prefix and not prefix.endswith("/"):
                    full_path = f"{prefix}/"
                routers[router_name]["endpoints"].append(
                    {
                        "handler": node.name,
                        "method": method_name.upper(),
                        "path": route_path,
                        "full_path": full_path,
                        "dependencies": dependencies,
                        "resolution_status": "resolved",
                        "confidence": "high",
                        "source": "ast_exact",
                    }
                )

        if not routers:
            continue
        router_name, router_metadata = next(iter(sorted(routers.items())))
        route_inventory[module_name] = {
            "file": str(relative_path),
            "router": router_name,
            "prefix": router_metadata["prefix"],
            "tags": router_metadata["tags"],
            "endpoints": router_metadata["endpoints"],
            "resolution_status": "resolved",
            "confidence": "high",
            "source": "ast_exact",
        }

    return dict(sorted(route_inventory.items()))
