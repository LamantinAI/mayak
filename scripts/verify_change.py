# FILE: scripts/verify_change.py
# SUMMARY: Verify project health and report which parts of the change were measured.

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


def _resolve_base(explicit: str) -> str | None:
    if explicit:
        return explicit
    try:
        remote_head = _git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD").strip()
    except subprocess.CalledProcessError:
        remote_head = ""
    for ref in (remote_head, "origin/main", "origin/master"):
        if not ref:
            continue
        try:
            _git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
            return ref
        except subprocess.CalledProcessError:
            continue
    return None


def _working_diff(base: str) -> tuple[str, set[str], set[str]]:
    commit = _git("rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").strip()
    start = _git("merge-base", "HEAD", commit).strip()
    names = ("diff", "--no-renames", "--name-only")
    mixed = _paths(*names, "--cached", "HEAD") & _paths(*names)
    if mixed:
        raise ValueError(
            "index and working tree disagree on staged paths: " + ", ".join(sorted(mixed))
        )
    untracked = _paths("ls-files", "--others", "--exclude-standard")
    local = _paths(*names, "HEAD") | untracked
    return start, _paths(*names, start) | untracked, local


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


def unexercised_handlers(report: Path) -> tuple[int, list[str]]:
    """Report every host HTTP handler, including those inherited from earlier commits."""
    hits = {
        item.get("filename", "").removeprefix("project/"): {
            int(line.get("number", "0"))
            for line in item.findall("./lines/line")
            if int(line.get("hits", "0")) > 0
        }
        for item in ElementTree.parse(report).findall(".//class")
    }
    count = 0
    missing = []
    for path in sorted(Path("project/infrastructure/api/endpoints").rglob("*.py")):
        if not _is_endpoint_module(path):
            continue
        name = path.as_posix()
        for handler in _handlers(path.read_text(encoding="utf-8")).values():
            count += 1
            body = handler.body[1:] if ast.get_docstring(handler, clean=False) else handler.body
            covered = hits.get(name.removeprefix("project/"), set())
            end = handler.end_lineno or handler.lineno
            if not body or not any(body[0].lineno <= line <= end for line in covered):
                missing.append(f"{name}:{handler.lineno} {handler.name}")

    return count, missing


def _check_routes(report: Path) -> int:
    count, missing = unexercised_handlers(report)
    for handler in missing:
        print(
            f"coverage.route_unexercised: {handler}. Call it in a host HTTP test "
            "(tests/application or tests/db); the Docker image is measured separately."
        )
    result = "FAIL" if missing else "PASS"
    print(f"verify: routes: {result} ({count - len(missing)} of {count} handlers ran)")
    return int(bool(missing))


def _coverage(start: str, paths: set[str], report: Path, uv: str, tool: str) -> int:
    if not paths:
        print("verify: diff-coverage: N/A (no paths changed)")
        return 0
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
            print("verify: diff-coverage: FAIL")
            return status
        totals = json.loads(receipt.read_text(encoding="utf-8"))
        lines = totals["total_num_lines"]
    if lines:
        print(
            f"verify: diff-coverage: PASS ({totals['total_percent_covered']}% of {lines} measured lines)"
        )
    else:
        print(f"verify: diff-coverage: N/A ({len(paths)} changed paths, no measurable lines)")
    return 0


def _preflight_database() -> int:
    """Read-only migration and PostgreSQL preflight before full verify runs expensive stages."""
    from scripts.validate_migrations import (
        collect_migration_precheck_issues,
        format_migration_issue_lines,
    )

    issues = collect_migration_precheck_issues(Path.cwd(), require_database=True)
    blocking = [issue for issue in issues if issue.severity == "error"]
    if not blocking:
        return 0
    for issue in blocking:
        for line in format_migration_issue_lines(issue):
            print(f"[validate_migrations] {line}", flush=True)
    print(f"verify: preflight: FAIL ({blocking[0].rule_id})", flush=True)
    print("verify: FAIL — migration preflight", flush=True)
    return max(issue.returncode for issue in blocking) or 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--e2e", action="store_true")
    parser.add_argument("--coverage-only", action="store_true")
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--diff-cover", required=True)
    args = parser.parse_args()
    try:
        head = _git("rev-parse", "--verify", "HEAD").strip()
    except subprocess.CalledProcessError:
        print("verify: BLOCKED (commit the initial project snapshot first)", file=sys.stderr)
        return 1
    base = _resolve_base(args.base)
    try:
        start, paths, local = _working_diff(base or head)
    except (subprocess.CalledProcessError, ValueError) as error:
        reason = str(error) if isinstance(error, ValueError) else f"unusable diff base {base!r}"
        print(f"verify: BLOCKED ({reason})", file=sys.stderr)
        return 1
    unknown_change = base is None and not paths
    if unknown_change:
        print(
            f"verify: change: HEAD {head[:12]}, clean tree with no integration branch; "
            "supply DIFF_COMPARE_BRANCH=<starting commit> to measure committed work",
            flush=True,
        )
    else:
        print(
            f"verify: change: {base or 'working tree'} ({start[:12]})...{head[:12]}"
            f"; {len(paths)} changed paths, {len(local)} uncommitted",
            flush=True,
        )
    # Without a base only the uncommitted paths are in scope, and a final line identical to a
    # full run's let an agent quote it for committed work nobody measured. The starting commit
    # is not guessed: a whole project left uncommitted on a snapshot and work already committed
    # look the same from here, so only the person handing off can name it.
    only_local = base is None and bool(paths)
    if base is None:
        scope = "project only (clean tree, no base)"
        if paths:
            scope = "uncommitted paths only (no base); commits are not measured"
    else:
        scope = f"change since {base} ({'explicit' if args.base else 'auto'} base)"
    print(f"verify: scope: {scope}", flush=True)
    report = Path("coverage.xml")
    env = os.environ | {
        "STRICT_GENERATED": "1",
        "STRICT_RUFF": "1",
        "MAYAK_REQUIRE_MIGRATION_DB": "1",
    }
    if not args.coverage_only:
        status = _preflight_database()
        if status:
            return status
        status = subprocess.call(("make", "--no-print-directory", "quality-gates"), env=env)
        print(f"verify: quality-gates: {'PASS' if status == 0 else 'FAIL'}", flush=True)
        if status:
            print("verify: FAIL — quality gates")
            return status
    status = _check_routes(report)
    if not status:
        if unknown_change:
            print("verify: diff-coverage: NOT MEASURED (no committed change in scope)")
        else:
            status = _coverage(start, paths, report, args.uv, args.diff_cover)
    if status:
        print("verify: FAIL — host route or diff coverage")
        return status
    if args.coverage_only:
        return 0
    if args.e2e or unknown_change or _needs_e2e(paths):
        status = subprocess.call(("make", "--no-print-directory", "test-e2e"), env=env)
        if status:
            print("verify: FAIL — Docker E2E")
            return status
        core = {"test_health_api.py", "test_migration_lock.py"}
        product = sorted(
            path.name
            for path in Path("tests/functional/src").glob("test_*.py")
            if path.name not in core
        )
        print(f"verify: test-e2e: PASS (product test files: {', '.join(product) or 'none'})")
    else:
        where = " in the uncommitted paths; commits were not examined" if only_local else ""
        print(
            f"verify: test-e2e: N/A (no runtime, migration, container or functional test changes{where})"
        )
    if only_local:
        print("verify: full handoff: make verify DIFF_COMPARE_BRANCH=<starting commit>")
        print("verify: PASS — project and uncommitted paths only; commits were not measured")
        return 0
    scope = "project only; no change was measured" if not paths else "project and change"
    print(f"verify: PASS — {scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
