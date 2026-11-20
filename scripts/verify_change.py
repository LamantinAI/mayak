# FILE: scripts/verify_change.py
# SUMMARY: Run the strict acceptance checks appropriate to the complete working diff.

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

from scripts.validate_endpoint_wiring import (
    _is_endpoint_module,
    _route_handler_routers,
    _router_definitions,
)

CONTAINER_INPUTS = {
    ".dockerignore",
    ".env.sample",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.postgres.yml",
    "entrypoint.sh",
    "pyproject.toml",
    "uv.lock",
}
WIRING_INPUTS = {
    "project/core/service_registration.py",
    "project/core/composition_root.py",
    "project/infrastructure/api/dependencies.py",
    "project/infrastructure/api/router_registration.py",
}


def _git(*arguments: str) -> str:
    return subprocess.check_output(
        ("git", *arguments),
        text=True,
        stderr=subprocess.DEVNULL,
        env=os.environ | {"GIT_OPTIONAL_LOCKS": "0"},
    )


def _working_diff(base: str) -> tuple[str, set[str]]:
    commit = _git("rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").strip()
    start = _git("merge-base", "HEAD", commit).strip()
    paths = set(_git("diff", "--no-renames", "--name-only", "-z", start, "--").split("\0"))
    paths.update(_git("ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    paths.discard("")
    return start, paths


def needs_e2e(base: str) -> bool:
    _, paths = _working_diff(base)
    return bool(paths & (CONTAINER_INPUTS | WIRING_INPUTS)) or any(
        path.startswith(
            ("alembic/", "project/infrastructure/api/", "project/launcher/", "tests/functional/")
        )
        for path in paths
    )


def _handlers(source: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(source)
    routers = set(_router_definitions(tree))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _route_handler_routers(node, routers)
    }


def unexercised_handlers(base: str, report: Path) -> list[str]:
    """Import-time coverage does not show that a changed route ever handled a call."""
    start, paths = _working_diff(base)
    hits = {
        item.get("filename", "").removeprefix("project/"): {
            int(line.get("number", "0"))
            for line in item.findall("./lines/line")
            if int(line.get("hits", "0")) > 0
        }
        for item in ElementTree.parse(report).findall(".//class")
    }
    missing = []
    for name in sorted(paths):
        path = Path(name)
        if not _is_endpoint_module(path) or not path.is_file():
            continue
        try:
            before = _handlers(_git("show", f"{start}:{name}"))
        except subprocess.CalledProcessError:
            # New file, including a new filename after a rename.
            before = {}
        for handler in _handlers(path.read_text(encoding="utf-8")).values():
            old = before.get(handler.name)
            if old is not None and ast.dump(old) == ast.dump(handler):
                continue
            body = handler.body[1:] if ast.get_docstring(handler, clean=False) else handler.body
            covered = hits.get(name.removeprefix("project/"), set())
            end = handler.end_lineno or handler.lineno
            if not body or not any(body[0].lineno <= line <= end for line in covered):
                missing.append(f"{name}:{handler.lineno} {handler.name}")

    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--e2e", action="store_true")
    parser.add_argument("--coverage-handlers", type=Path)
    args = parser.parse_args()

    if args.coverage_handlers:
        missing = unexercised_handlers(args.base, args.coverage_handlers)
        for handler in missing:
            print(f"coverage.route_unexercised: {handler}. Run this handler in an HTTP test.")
        return int(bool(missing))

    try:
        run_e2e = args.e2e or needs_e2e(args.base)
    except subprocess.CalledProcessError:
        print("verify: BLOCKED — no usable Git diff base", file=sys.stderr)
        return 1

    env = os.environ | {
        "STRICT_GENERATED": "1",
        "STRICT_RUFF": "1",
        "MAYAK_REQUIRE_MIGRATION_DB": "1",
    }
    steps = ["quality-gates", "diff-coverage-report"]
    if run_e2e:
        steps.append("test-e2e")

    for index, step in enumerate(steps):
        status = subprocess.call(("make", "--no-print-directory", step), env=env)
        print(f"verify: {step}: {'PASS' if status == 0 else 'FAIL'}", flush=True)
        if status:
            for later in steps[index + 1 :]:
                print(f"verify: {later}: BLOCKED by {step}")
            return status
    if not run_e2e:
        print("verify: test-e2e: SKIPPED (no endpoint, wiring, migration or container changes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
