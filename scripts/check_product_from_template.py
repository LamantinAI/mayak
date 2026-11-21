#!/usr/bin/env python3
# FILE: check_product_from_template.py
# SUMMARY: Build a throwaway project from this checkout the way a new service is made, and check it.
# Copies the checkout into a fresh Git repository, runs the documented bootstrap, and counts the
# inherited Python. With --gates and --e2e it also verifies the resulting product. Template CI
# exercises both database profiles. A descendant has no use for this script, so extraction removes it.

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

ROOT_DIR = Path(__file__).resolve().parent.parent

# The ceiling on non-empty Python lines a project inherits: the post-bench2 plan's 19-21 thousand,
# reached once the navigation maps and their query tool left the product (step 7). Raised to 22 000
# by the owner on 2026-09-28: the product stood at 20 983, and the fixes bench3 asked for (the
# layer validator, provider errors, the 500's status, the sample's update conflict) add code a
# project inherits. bench3 found no link between this count and what an agent pays; it is a brake on
# the diff, not a goal.
DEFAULT_LINE_LIMIT = 22_000

PROBE_NAME = "Harbor Probe"
PROBE_DOMAIN = "Berth bookings for a small marina — a throwaway project made to check the template."


# Git's own variables left out. Inside a hook git exports GIT_DIR and GIT_INDEX_FILE, and a git
# command run with them in another directory acts on the repository that ran the hook: measured on
# 2026-09-24, the pre-commit hook's test made a copy, committed it into the outer repository,
# re-ran the hook there, recursed eighteen levels and left core.bare=true behind.
def hermetic_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _run(command: Sequence[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        command, cwd=cwd, env=env or hermetic_env(), capture_output=True, text=True
    )
    if result.returncode != 0:
        tail = (result.stdout + result.stderr).strip().splitlines()[-40:]
        raise SystemExit(f"{' '.join(command)} failed in {cwd}:\n" + "\n".join(tail))


def copy_checkout(source: Path, target: Path) -> None:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=source,
        env=hermetic_env(),
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    for name in filter(None, listed.split("\0")):
        origin = source / name
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if origin.is_symlink():
            destination.symlink_to(os.readlink(origin))
        elif origin.is_file():
            shutil.copy2(origin, destination)
    _run(["git", "init", "-q"], target)
    # No automatic maintenance in the copy. After a commit git may pack the loose objects in a
    # detached process, and whoever copies or deletes the directory next races it: git 2.55 in CI
    # did so with ~300 objects, and a test copying the checkout failed on .git/objects/<xx>.
    _run(["git", "config", "maintenance.auto", "false"], target)
    _run(["git", "config", "gc.auto", "0"], target)
    _run(["git", "add", "-A"], target)
    _run(
        [
            "git",
            "-c",
            "user.name=probe",
            "-c",
            "user.email=probe@example.invalid",
            "commit",
            "-qm",
            "made from the template",
        ],
        target,
    )


# The two places a gate compares the identity: the project context and the settings default that
# tests/application/test_config.py holds equal to it. The other four places in
# .agents/skills/initialize-project are prose or need a re-lock, and no gate reads them.
def replace_identity(root: Path) -> None:
    context_path = root / "docs/project_context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context.update(is_template=False, project_name=PROBE_NAME, domain=PROBE_DOMAIN)
    context_path.write_text(
        json.dumps(context, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    settings_path = root / "project/core/config_settings_core.py"
    settings = settings_path.read_text(encoding="utf-8")
    renamed, count = re.subn(r'default="Mayak"', f'default="{PROBE_NAME}"', settings)
    if count != 1:
        raise SystemExit('project/core/config_settings_core.py no longer has one default="Mayak"')
    settings_path.write_text(renamed, encoding="utf-8")


def count_python_lines(root: Path) -> int:
    total = 0
    for path in root.rglob("*.py"):
        if ".venv" in path.parts or "node_modules" in path.parts:
            continue
        total += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return total


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gates", action="store_true", help="run make quality-gates in the project"
    )
    parser.add_argument("--e2e", action="store_true", help="run make test-e2e in the project")
    parser.add_argument("--limit", type=int, default=DEFAULT_LINE_LIMIT)
    parser.add_argument("--db", choices=("postgres", "none"), default="none")
    parser.add_argument("--keep", action="store_true", help="leave the project directory behind")
    args = parser.parse_args([] if argv is None else argv)

    workspace = Path(tempfile.mkdtemp(prefix="mayak-product-"))
    project = workspace / "product"
    project.mkdir()
    try:
        copy_checkout(ROOT_DIR, project)
        before = count_python_lines(project)
        env = {
            k: v
            for k, v in hermetic_env().items()
            if k not in {"VIRTUAL_ENV", "TEST_DATABASE_URL"} and not k.startswith("POSTGRES_")
        }
        env |= {"STRICT_RUFF": "1", "STRICT_GENERATED": "1"}
        _run(
            ["make", "bootstrap", f"NAME={PROBE_NAME}", f"DOMAIN={PROBE_DOMAIN}", f"DB={args.db}"],
            project,
            env,
        )
        leftovers = subprocess.run(
            [
                "git",
                "grep",
                "-lE",
                r"^\s*(from|import) project\.[a-z_.]*reference_task",
                "--",
                "project",
                "tests",
            ],
            cwd=project,
            env=hermetic_env(),
            capture_output=True,
            text=True,
        ).stdout.split()
        if leftovers:
            print("FAIL: these files still import the reference vertical: " + ", ".join(leftovers))
            return 1
        after = count_python_lines(project)
        print(f"non-empty lines of Python: template {before}, project {after} (limit {args.limit})")
        if after > args.limit:
            print(
                f"FAIL: a project made from this template carries {after} lines, over {args.limit}"
            )
            return 1
        if args.gates:
            _run(["make", "quality-gates"], project, env)
            print(f"make quality-gates: PASS on DB={args.db}")
        if args.e2e:
            _run(["make", "test-e2e"], project, env)
            print(f"make test-e2e: PASS on DB={args.db}")
        return 0
    finally:
        if args.keep:
            print(f"project left at {project}")
        else:
            if args.db == "postgres":
                for target in ("test-db-down", "db-down-worktree"):
                    subprocess.run(
                        ["make", target], cwd=project, env=hermetic_env(), capture_output=True
                    )
            shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
