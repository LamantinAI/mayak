#!/usr/bin/env python3
# FILE: validate_runtime_ownership.py
# SUMMARY: Repository utility that enforces ownership boundaries for shared runtime resources, env access, and app.state service wiring.

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path

from ai_context.validator_contract import build_validator_issue_payload


ROOT_DIR = Path(__file__).resolve().parent.parent

_ENV_ALLOWLIST_PREFIXES = (
    "tests/",
    "project/core/config",
)
_ENV_ALLOWLIST_PATHS = {
    "project/common/sampling.py",
    "project/core/logging/config.py",
    # Entry point: must set ORT_LOGGING_LEVEL before any transitive
    # onnxruntime/fastembed import so the cpuid_info warning is suppressed.
    "project/launcher/main.py",
}
_APP_STATE_SERVICES_ALLOWED_PATHS = {
    "project/core/composition_root.py",
}
_RESOURCE_ALLOWED_PATHS = {
    # Shared async-PG connection pool — actively used today.
    "AsyncConnectionPool": {"project/core/composition_root.py"},
    # Preventive: any future httpx async client must go through composition_root so it
    # participates in lifespan cleanup. No usage today — still gated.
    "AsyncClient": {"project/core/composition_root.py"},
    # The synchronous sibling, and the one that actually hurts: `httpx.Client()` inside an async
    # handler opens a fresh TCP+TLS connection per request AND blocks the event loop. ruff sees
    # one of its two call shapes: ASYNC212 flags `with httpx.Client() as c: c.get(...)`, and
    # nothing flags the direct `httpx.Client().post(...)` chain — measured with a probe file.
    # This entry is what covers the second shape, and it also catches the construction itself
    # rather than the call, which is the part that leaks a connection pool per request.
    #
    # Matching is by the last segment of the dotted name, as it is for every entry here: a local
    # class named Client, or another library's Client, would be flagged too. No such name exists
    # in project/ or tests/ today; when one appears, the fix is an allowlist path, not a rename.
    "Client": {"project/core/composition_root.py"},
    # Preventive: aiohttp ClientSession would also be a shared long-lived resource.
    "ClientSession": {"project/core/composition_root.py"},
    # Preventive: SQLAlchemy async engines own connection pools and must be created once.
    "create_async_engine": {"project/core/composition_root.py"},
}

_RUNTIME_OWNERSHIP_RULE_PLAYBOOKS = {
    "runtime_ownership.env_access_restricted": {
        "meaning": (
            "Direct os.getenv or os.environ access escaped the approved configuration zones."
        ),
        "read_first": [
            "CLAUDE.md",
            "project/core/config.py",
            "docs/architecture_rules.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_runtime_ownership.py",
        "likely_fix_shape": (
            "Move the env read into project.core.config* or an explicit allowlist module and "
            "flow the value through typed settings instead of reading it in runtime logic."
        ),
        "next_checks": [
            "uv run python scripts/validate_runtime_ownership.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once validate_runtime_ownership.py passes and the env access lives in "
            "an approved configuration path."
        ),
    },
    "runtime_ownership.app_state_services_write_restricted": {
        "meaning": (
            "A module outside the canonical runtime wiring path wrote to app.state.services."
        ),
        "read_first": [
            "CLAUDE.md",
            "project/core/composition_root.py",
            "docs/architecture_rules.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_runtime_ownership.py",
        "likely_fix_shape": (
            "Move the app.state.services assignment back into the composition root or test "
            "fixture path instead of mutating it in runtime leaf modules."
        ),
        "next_checks": [
            "uv run python scripts/validate_runtime_ownership.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once app.state.services is assigned only in the canonical wiring path "
            "or tests."
        ),
    },
    "runtime_ownership.shared_resource_creation_restricted": {
        "meaning": (
            "A shared long-lived runtime resource was created outside the canonical wiring or "
            "lifecycle path."
        ),
        "read_first": [
            "CLAUDE.md",
            "project/core/composition_root.py",
            "project/core/lifecycle.py",
            "docs/architecture_rules.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_runtime_ownership.py",
        "likely_fix_shape": (
            "Create the shared resource in the composition root or lifecycle manager and inject "
            "it through the existing wiring flow instead of constructing it in endpoints or services."
        ),
        "next_checks": [
            "uv run python scripts/validate_runtime_ownership.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop widening once shared resource construction occurs only in canonical runtime "
            "ownership files or tests."
        ),
    },
    "runtime_ownership.syntax_error": {
        "meaning": (
            "A Python source file under runtime-ownership scan could not be parsed because it "
            "contains a SyntaxError. Surfaced as a structured issue so the agent loop sees a "
            "normal rule_id payload."
        ),
        "suggested_fix": (
            "Open the file at the reported line and repair the syntax. Other runtime-ownership "
            "rules apply only after parsing succeeds."
        ),
        "read_first": [
            "the file referenced by the issue",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_runtime_ownership.py",
        "likely_fix_shape": ("Restore valid Python syntax on the reported line."),
        "next_checks": [
            "uv run python scripts/validate_runtime_ownership.py",
        ],
        "stop_widening_condition": ("Stop widening once the file parses cleanly."),
    },
    "runtime_ownership.read_error": {
        "meaning": (
            "A Python source file could not be decoded as UTF-8 by validate_runtime_ownership.py."
        ),
        "suggested_fix": ("Re-save the file as UTF-8."),
        "read_first": [
            "the file referenced by the issue",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_runtime_ownership.py",
        "likely_fix_shape": ("Restore valid UTF-8 encoding."),
        "next_checks": [
            "uv run python scripts/validate_runtime_ownership.py",
        ],
        "stop_widening_condition": ("Stop widening once the file is readable."),
    },
}


@dataclass(slots=True)
class RuntimeOwnershipIssue:
    path: Path
    line: int
    rule_id: str
    message: str
    category: str = "runtime_ownership"


def get_runtime_ownership_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _RUNTIME_OWNERSHIP_RULE_PLAYBOOKS.get(rule_id)
    if playbook is None:
        return None
    return {
        "meaning": playbook["meaning"],
        "read_first": list(playbook["read_first"]),
        "smallest_command_to_rerun": playbook["smallest_command_to_rerun"],
        "likely_fix_shape": playbook["likely_fix_shape"],
        "next_checks": list(playbook["next_checks"]),
        "stop_widening_condition": playbook["stop_widening_condition"],
    }


# FUNCTION: _issue_to_payload
# SUMMARY: Convert a RuntimeOwnershipIssue into a stable JSON-serializable payload, routing suggested_fix/read_first/next_commands/stop_widening_condition through the shared rule playbook instead of the previous inline main() dict literal that dropped read_first, next_commands, and stop_widening_condition entirely.
# INPUT: repo_root (Path): Repository root used for relative-path rendering.
def _issue_to_payload(issue: RuntimeOwnershipIssue, repo_root: Path) -> dict[str, object]:
    playbook = get_runtime_ownership_rule_playbook(issue.rule_id)
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category=issue.category,
        file=issue.path.relative_to(repo_root).as_posix(),
        line=issue.line,
        message=issue.message,
        playbook=playbook,
    )


def _is_python_source(path: Path) -> bool:
    return path.suffix == ".py" and "__pycache__" not in path.parts


def _repo_path(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _is_test_path(repo_path: str) -> bool:
    return repo_path.startswith("tests/")


def _is_env_access_allowed(repo_path: str) -> bool:
    if repo_path in _ENV_ALLOWLIST_PATHS:
        return True
    return any(repo_path.startswith(prefix) for prefix in _ENV_ALLOWLIST_PREFIXES)


def _is_app_state_services_target(node: ast.AST) -> bool:
    if not isinstance(node, ast.Attribute) or node.attr != "services":
        return False
    state_node = node.value
    return isinstance(state_node, ast.Attribute) and state_node.attr == "state"


def _iter_assignment_targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, ast.AnnAssign):
        return [node.target]
    if isinstance(node, ast.AugAssign):
        return [node.target]
    return []


# FUNCTION: _import_bindings
# SUMMARY: Map each local name a module binds to the dotted path it was imported from.
# INPUT: tree (ast.AST): Parsed module.
# OUTPUT: (dict[str, str]): Local name to dotted origin, e.g. {"_env": "os.environ"}.
def _import_bindings(tree: ast.AST) -> dict[str, str]:
    # **LOGIC_STEP**: Without this map the rules matched the literal spellings `os.getenv` and
    # `os.environ` and nothing else, so `from os import getenv` — ordinary style, not an evasion —
    # silently disabled env-access enforcement for the whole file. The other two validators
    # (architecture, endpoint wiring) already build such a map; this one did not.
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # **LOGIC_STEP**: `import os.path` binds the top package `os`, while
                # `import os.path as p` binds `p` to the full dotted path.
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    bindings[root] = root
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                bindings[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bindings


# FUNCTION: _dotted_path
# SUMMARY: Resolve an expression back to the dotted path of the module member it reads.
# INPUT: node (ast.AST): Expression to resolve.
# INPUT: bindings (dict[str, str]): Import map from _import_bindings.
# OUTPUT: (str | None): Dotted path, or None when the expression is not an imported reference.
def _dotted_path(node: ast.AST, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted_path(node.value, bindings)
        return f"{base}.{node.attr}" if base else None
    return None


# FUNCTION: _resource_name_from_call
# SUMMARY: Report which guarded shared resource a call constructs, following import aliases.
# OUTPUT: (str | None): Resource name from the allowlist, or None.
def _resource_name_from_call(node: ast.Call, bindings: dict[str, str]) -> str | None:
    dotted = _dotted_path(node.func, bindings)
    if dotted is not None:
        candidate = dotted.rsplit(".", 1)[-1]
        if candidate in _RESOURCE_ALLOWED_PATHS:
            return candidate
    func = node.func
    if isinstance(func, ast.Name) and func.id in _RESOURCE_ALLOWED_PATHS:
        return func.id
    return None


# FUNCTION: _env_access_kind
# SUMMARY: Classify an expression that reads process environment, whatever it was imported as.
# OUTPUT: (str | None): "os.getenv" or "os.environ" for the message, or None.
def _env_access_kind(node: ast.AST, bindings: dict[str, str]) -> str | None:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Subscript):
        target = target.value
    dotted = _dotted_path(target, bindings)
    if dotted is None:
        return None
    if dotted == "os.getenv":
        return "os.getenv"
    if dotted == "os.environ" or dotted.startswith("os.environ."):
        return "os.environ"
    return None


def collect_runtime_ownership_issues(repo_root: Path) -> list[RuntimeOwnershipIssue]:
    issues: list[RuntimeOwnershipIssue] = []
    scan_roots = [repo_root / "project", repo_root / "tests"]
    for scan_root in scan_roots:
        if not scan_root.exists():
            continue
        for path in sorted(scan_root.rglob("*.py")):
            if not _is_python_source(path):
                continue
            repo_path = _repo_path(repo_root, path)
            # **LOGIC_STEP**: Read & parse under guarded exceptions so a broken file
            # surfaces as a structured RuntimeOwnershipIssue rather than a raw traceback.
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                issues.append(
                    RuntimeOwnershipIssue(
                        path=path,
                        line=1,
                        rule_id="runtime_ownership.read_error",
                        message=f"UnicodeDecodeError while reading source: {error.reason}",
                    )
                )
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError as error:
                issues.append(
                    RuntimeOwnershipIssue(
                        path=path,
                        line=error.lineno or 1,
                        rule_id="runtime_ownership.syntax_error",
                        message=f"SyntaxError while parsing source: {error.msg}",
                    )
                )
                continue

            bindings = _import_bindings(tree)

            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    for target in _iter_assignment_targets(node):
                        if (
                            _is_app_state_services_target(target)
                            and repo_path not in _APP_STATE_SERVICES_ALLOWED_PATHS
                            and not _is_test_path(repo_path)
                        ):
                            issues.append(
                                RuntimeOwnershipIssue(
                                    path=path,
                                    line=node.lineno,
                                    rule_id="runtime_ownership.app_state_services_write_restricted",
                                    message=(
                                        "app.state.services may be assigned only in "
                                        "project/core/composition_root.py or tests."
                                    ),
                                )
                            )

                if isinstance(node, (ast.Call, ast.Attribute, ast.Subscript)):
                    env_kind = _env_access_kind(node, bindings)
                    if env_kind is not None and not _is_env_access_allowed(repo_path):
                        issues.append(
                            RuntimeOwnershipIssue(
                                path=path,
                                line=node.lineno,
                                rule_id="runtime_ownership.env_access_restricted",
                                message=(
                                    f"Direct {env_kind} access is restricted to configuration "
                                    "allowlist paths."
                                ),
                            )
                        )

                if isinstance(node, ast.Call):
                    resource_name = _resource_name_from_call(node, bindings)
                    if resource_name is None or _is_test_path(repo_path):
                        continue
                    if repo_path not in _RESOURCE_ALLOWED_PATHS[resource_name]:
                        issues.append(
                            RuntimeOwnershipIssue(
                                path=path,
                                line=node.lineno,
                                rule_id="runtime_ownership.shared_resource_creation_restricted",
                                message=(
                                    f"{resource_name} may be created only in "
                                    f"{', '.join(sorted(_RESOURCE_ALLOWED_PATHS[resource_name]))} or tests."
                                ),
                            )
                        )

    unique: dict[tuple[str, int, str], RuntimeOwnershipIssue] = {}
    for issue in issues:
        key = (_repo_path(repo_root, issue.path), issue.line, issue.rule_id)
        unique[key] = issue
    return sorted(
        unique.values(),
        key=lambda issue: (
            _repo_path(repo_root, issue.path),
            issue.line,
            issue.rule_id,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate shared runtime resource ownership, env access, and app.state.services writes."
        )
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured machine-readable diagnostics.",
    )
    args = parser.parse_args()

    issues = collect_runtime_ownership_issues(ROOT_DIR)
    if not issues:
        if args.json:
            print(json.dumps({"status": "ok", "issues": []}, ensure_ascii=True, indent=2))
        else:
            print("Runtime ownership validation passed.")
        return 0

    if args.json:
        payload = {
            "status": "error",
            "issues": [_issue_to_payload(issue, ROOT_DIR) for issue in issues],
        }
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        for issue in issues:
            print(f"{issue.path.relative_to(ROOT_DIR)}:{issue.line}: {issue.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
