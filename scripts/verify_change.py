# FILE: scripts/verify_change.py
# SUMMARY: Run the strict acceptance checks appropriate to the complete working diff.

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
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
    "alembic.ini",
    "docker-compose.yml",
    "docker-compose.postgres.yml",
    "entrypoint.sh",
    "pyproject.toml",
    "uv.lock",
}


def _git(*arguments: str) -> str:
    return subprocess.check_output(
        ("git", *arguments),
        text=True,
        stderr=subprocess.DEVNULL,
        env=os.environ | {"GIT_OPTIONAL_LOCKS": "0"},
    )


def _paths(*arguments: str) -> set[str]:
    return set(_git(*arguments, "-z", "--").split("\0")) - {""}


def _working_diff(base: str) -> tuple[str, set[str]]:
    commit = _git("rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").strip()
    start = _git("merge-base", "HEAD", commit).strip()
    names = ("diff", "--no-renames", "--name-only")
    mixed = _paths(*names, "--cached", "HEAD") & _paths(*names)
    if mixed:
        raise ValueError(
            "index and working tree disagree on staged paths: " + ", ".join(sorted(mixed))
        )
    return start, _paths(*names, start) | _paths("ls-files", "--others", "--exclude-standard")


def _needs_e2e(paths: set[str]) -> bool:
    return bool(paths & CONTAINER_INPUTS) or any(
        path.startswith(("alembic/", "project/", "tests/functional/")) for path in paths
    )


def needs_e2e(base: str) -> bool:
    return _needs_e2e(_working_diff(base)[1])


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


def _coverage(start: str, paths: set[str], report: Path, uv: str, tool: str) -> int:
    if not paths:
        print(
            "verify: diff-coverage-report: SKIPPED (empty diff; supply a base for a committed change)"
        )
        return 0
    missing = unexercised_handlers(start, report)
    for handler in missing:
        print(f"coverage.route_unexercised: {handler}. Run this handler in an HTTP test.")
    if missing:
        return 1
    with TemporaryDirectory(prefix="mayak-coverage-") as directory:
        receipt = Path(directory) / "diff.json"
        status = subprocess.call(
            (
                uv,
                "run",
                "--with",
                tool,
                "diff-cover",
                str(report),
                "--compare-branch",
                start,
                "--include-untracked",
                "--fail-under",
                "80",
                "--format",
                f"json:{receipt}",
            ),
            env=os.environ | {"GIT_OPTIONAL_LOCKS": "0"},
        )
        if status:
            print("verify: diff-coverage-report: FAIL")
            return status
        lines = json.loads(receipt.read_text(encoding="utf-8"))["total_num_lines"]
    if lines:
        print(f"verify: diff-coverage-report: PASS ({lines} measured lines)")
    else:
        print("verify: diff-coverage-report: SKIPPED (no measurable lines)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--e2e", action="store_true")
    parser.add_argument("--coverage-only", action="store_true")
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--diff-cover", required=True)
    args = parser.parse_args()
    try:
        start, paths = _working_diff(args.base)
    except (subprocess.CalledProcessError, ValueError) as error:
        reason = str(error) if isinstance(error, ValueError) else "no usable Git diff base"
        print(f"verify: BLOCKED ({reason})", file=sys.stderr)
        return 1
    print(f"verify: base {args.base} -> {start}; {len(paths)} changed paths", flush=True)
    report = Path("coverage.xml")
    if args.coverage_only:
        return _coverage(start, paths, report, args.uv, args.diff_cover)

    env = os.environ | {
        "STRICT_GENERATED": "1",
        "STRICT_RUFF": "1",
        "MAYAK_REQUIRE_MIGRATION_DB": "1",
    }
    status = subprocess.call(("make", "--no-print-directory", "quality-gates"), env=env)
    print(f"verify: quality-gates: {'PASS' if status == 0 else 'FAIL'}", flush=True)
    if status == 0:
        status = _coverage(start, paths, report, args.uv, args.diff_cover)
    if status:
        print("verify: remaining checks: BLOCKED by an earlier failure")
        return status
    if not (args.e2e or _needs_e2e(paths)):
        print(
            "verify: test-e2e: SKIPPED (no runtime, migration, container or functional test changes)"
        )
        return 0
    status = subprocess.call(("make", "--no-print-directory", "test-e2e"), env=env)
    print(f"verify: test-e2e: {'PASS' if status == 0 else 'FAIL'}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
