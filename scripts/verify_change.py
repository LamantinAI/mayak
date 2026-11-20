# FILE: scripts/verify_change.py
# SUMMARY: Run the strict acceptance checks appropriate to the complete working diff.

from __future__ import annotations

import argparse
import os
import subprocess
import sys

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
    return subprocess.check_output(("git", *arguments), text=True)


def needs_e2e(base: str) -> bool:
    commit = _git("rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").strip()
    start = _git("merge-base", "HEAD", commit).strip()
    paths = set(_git("diff", "--no-renames", "--name-only", "-z", start, "--").split("\0"))
    paths.update(_git("ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    return bool(paths & (CONTAINER_INPUTS | WIRING_INPUTS)) or any(
        path.startswith(
            ("alembic/", "project/infrastructure/api/", "project/launcher/", "tests/functional/")
        )
        for path in paths
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--e2e", action="store_true")
    args = parser.parse_args()

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
