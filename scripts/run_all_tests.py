#!/usr/bin/env python3
# FILE: run_all_tests.py
# Canonical test runner for AI agents and developers that executes the project's local and functional test suites through one entrypoint.

from __future__ import annotations

import argparse
import os
import shutil
import zlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

# Absolute repository root directory derived from the script location.
ROOT_DIR = Path(__file__).resolve().parent.parent

# Directory containing the Docker-based functional test suite.
FUNCTIONAL_DIR = ROOT_DIR / "tests" / "functional"

# Example environment file copied when the functional test suite has no local env file yet.
FUNCTIONAL_ENV_SAMPLE = FUNCTIONAL_DIR / ".env.sample"

# Active environment file consumed by the functional Docker Compose suite.
FUNCTIONAL_ENV_FILE = FUNCTIONAL_DIR / ".env"

# Total-coverage floor the full local run is held to, and the only place it is written.
# It lives here rather than in pytest.ini because a floor in `addopts` is applied to every
# pytest invocation, narrow ones included, where a total is meaningless.
COVERAGE_FLOOR_PERCENT = 60


# Compose otherwise names every checkout's stack 'functional'; teardown can remove another's.
def functional_compose_project() -> str:
    digest = zlib.crc32(str(ROOT_DIR).encode("utf-8"))
    return f"functional-{digest}"


# Shell POSTGRES_* values override Compose's functional env file and can break its login.
def functional_env() -> dict[str, str]:
    from scripts.validate_migrations import postgres_is_enabled

    env = {key: value for key, value in os.environ.items() if not key.startswith("POSTGRES_")}
    env["POSTGRES_ENABLED"] = "true" if postgres_is_enabled() else "false"
    return env


def _functional_compose(env: Mapping[str, str]) -> tuple[str, ...]:
    prefix = ("docker", "compose", "-p", functional_compose_project(), "-f", "docker-compose.yml")
    if env["POSTGRES_ENABLED"] == "true":
        return (*prefix, "-f", "docker-compose.postgres.yml")
    return prefix


# Commands are kept as argv so no shell quoting is needed.
@dataclass(frozen=True)
class TestStep:
    # Human-readable label shown before executing the step.
    name: str

    # Shell-free command argv executed for the step.
    command: tuple[str, ...]

    # Working directory used when executing the step.
    cwd: Path

    # Environment of the step's process; None inherits this one.
    env: Mapping[str, str] | None = None


# Allow direct calls from template tests with explicit arguments.
def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Mayak test suites through one canonical entrypoint."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--skip-functional",
        action="store_true",
        help="Run only the local unit/integration suite and skip Docker-based functional tests.",
    )
    mode_group.add_argument(
        "--functional-only",
        action="store_true",
        help="Run only the Docker-based functional suite.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


# Put local pytest before the image check when both suites are selected.
def _build_test_steps(
    skip_functional: bool,
    functional_only: bool,
) -> list[TestStep]:
    steps: list[TestStep] = []

    if not functional_only:
        steps.append(
            TestStep(
                name="local-suites",
                # No test paths: pytest collects from pytest.ini's `testpaths = tests`, minus its
                # `--ignore=tests/functional`, so a suite directory added under tests/ runs without
                # editing this list. Until 1.3.2 the directories were named here, and a project's
                # new tests/domain passed verify while a failing test sat in it uncollected: the
                # template's check of the list lived in tests/template, which a project does not
                # keep. The db tier still runs in this fast suite — it starts its own PostgreSQL
                # (tests/db/stack.py) and its conftest deselects it when POSTGRES_ENABLED=false.
                command=(
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    # A global floor belongs on the full run. Narrow pytest runs shouldn't inherit it.
                    "--cov=project",
                    "--cov-report=xml",
                    f"--cov-fail-under={COVERAGE_FLOOR_PERCENT}",
                ),
                cwd=ROOT_DIR,
                # Verify makes the real migration gate strict. Tests must still be able to
                # exercise the intentional skip behavior without inheriting that policy.
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key != "MAYAK_REQUIRE_MIGRATION_DB"
                },
            )
        )

    if not skip_functional:
        env = functional_env()
        steps.append(
            TestStep(
                name="functional",
                command=(
                    *_functional_compose(env),
                    "up",
                    "--build",
                    "--abort-on-container-exit",
                    "--exit-code-from",
                    "tests",
                ),
                cwd=FUNCTIONAL_DIR,
                env=env,
            )
        )

    return steps


# Write a progress line for the current runner stage to standard output.
def _emit(message: str) -> None:
    sys.stdout.write(f"[run_all_tests] {message}\n")
    sys.stdout.flush()


# Warn when sample keys change; allow extra local keys as deliberate overrides.
def env_sample_drift(
    sample: Mapping[str, str | None], current: Mapping[str, str | None]
) -> list[str]:
    return sorted(
        key for key, value in sample.items() if key not in current or current[key] != value
    )


# Parse Compose's simple env syntax without requiring dotenv before dependencies are installed.
def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if value[:1] in {'"', "'"} and value[-1:] == value[:1] and len(value) > 1:
            value = value[1:-1]
        else:
            # Only an unquoted value can carry a trailing comment; inside quotes
            # a `#` is part of the value, and a password is exactly where one turns up.
            value = value.split("#", 1)[0].strip()
        values[key] = value
    return values


# Report drift before the stack starts while allowing deliberate local overrides.
def _warn_on_functional_env_drift() -> None:
    drifted = env_sample_drift(
        _parse_env_file(FUNCTIONAL_ENV_SAMPLE), _parse_env_file(FUNCTIONAL_ENV_FILE)
    )
    if not drifted:
        return
    _emit(
        f"warning: {FUNCTIONAL_ENV_FILE} differs from {FUNCTIONAL_ENV_SAMPLE.name} "
        f"for: {', '.join(drifted)} — delete it to regenerate, or update it by hand"
    )


# Ensure the functional test suite has a concrete env file before Docker Compose starts.
# FileNotFoundError: When the functional env sample file is missing.
def _ensure_functional_env() -> None:
    if FUNCTIONAL_ENV_FILE.exists():
        _warn_on_functional_env_drift()
        return
    if not FUNCTIONAL_ENV_SAMPLE.exists():
        raise FileNotFoundError(f"Functional env sample not found: {FUNCTIONAL_ENV_SAMPLE}")
    shutil.copyfile(FUNCTIONAL_ENV_SAMPLE, FUNCTIONAL_ENV_FILE)
    _emit(f"created functional env file at {FUNCTIONAL_ENV_FILE}")


# Execute one command step and fail fast when the subprocess returns a non-zero exit status.
# subprocess.CalledProcessError: When the subprocess exits with a non-zero status.
def _run_command(step: TestStep) -> None:
    _emit(f"running {step.name}: {' '.join(step.command)}")
    subprocess.run(step.command, cwd=step.cwd, env=step.env, check=True)


def _run_functional_step(step: TestStep) -> None:
    _ensure_functional_env()
    assert step.env is not None
    compose = _functional_compose(step.env)
    try:
        _run_command(step)
    except subprocess.CalledProcessError:
        subprocess.run((*compose, "logs"), cwd=FUNCTIONAL_DIR, env=step.env, check=False)
        raise
    finally:
        subprocess.run(
            (*compose, "down", "-v", "--remove-orphans"),
            cwd=FUNCTIONAL_DIR,
            env=step.env,
            check=False,
        )


# Execute the requested set of Mayak test suites through the canonical runner.
# argv: Optional CLI argument list used by tests or the default process argv.
def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    steps = _build_test_steps(
        skip_functional=args.skip_functional,
        functional_only=args.functional_only,
    )

    if not steps:
        raise ValueError("At least one test suite must be selected")

    for step in steps:
        if step.name == "functional":
            _run_functional_step(step)
        else:
            _run_command(step)

    _emit("all requested test suites passed")
    return 0


# Module entrypoint that executes the canonical test runner as a CLI script.
if __name__ == "__main__":
    raise SystemExit(main())
