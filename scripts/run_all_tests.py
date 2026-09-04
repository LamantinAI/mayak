#!/usr/bin/env python3
# FILE: run_all_tests.py
# SUMMARY: Canonical test runner for AI agents and developers that executes the project's local and functional test suites through one entrypoint.

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

# ATTRIBUTE: ROOT_DIR (Path)
# SUMMARY: Absolute repository root directory derived from the script location.
ROOT_DIR = Path(__file__).resolve().parent.parent

# ATTRIBUTE: FUNCTIONAL_DIR (Path)
# SUMMARY: Directory containing the Docker-based functional test suite.
FUNCTIONAL_DIR = ROOT_DIR / "tests" / "functional"

# ATTRIBUTE: FUNCTIONAL_ENV_SAMPLE (Path)
# SUMMARY: Example environment file copied when the functional test suite has no local env file yet.
FUNCTIONAL_ENV_SAMPLE = FUNCTIONAL_DIR / ".env.sample"

# ATTRIBUTE: FUNCTIONAL_ENV_FILE (Path)
# SUMMARY: Active environment file consumed by the functional Docker Compose suite.
FUNCTIONAL_ENV_FILE = FUNCTIONAL_DIR / ".env"


# DATACLASS: run_all_tests.TestStep
# SUMMARY: Immutable command step executed by the canonical test runner.
@dataclass(frozen=True)
class TestStep:
    # ATTRIBUTE: name (str)
    # SUMMARY: Human-readable label shown before executing the step.
    name: str

    # ATTRIBUTE: command (tuple[str, ...])
    # SUMMARY: Shell-free command argv executed for the step.
    command: tuple[str, ...]

    # ATTRIBUTE: cwd (Path)
    # SUMMARY: Working directory used when executing the step.
    cwd: Path


# FUNCTION: _parse_args
# SUMMARY: Parse CLI arguments controlling which test suites are executed.
# INPUT: argv (Sequence[str] | None): Optional CLI argument list used by tests or the default process argv.
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


# FUNCTION: _build_test_steps
# SUMMARY: Build the ordered list of test runner steps for the requested execution mode.
# INPUT: skip_functional (bool): Whether the functional Docker-based suite should be skipped.
# INPUT: functional_only (bool): Whether only the functional Docker-based suite should be executed.
# OUTPUT: (list[TestStep]): Ordered runnable test steps.
def _build_test_steps(
    skip_functional: bool,
    functional_only: bool,
) -> list[TestStep]:
    steps: list[TestStep] = []

    if not functional_only:
        steps.append(
            TestStep(
                name="local-suites",
                command=(
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/application",
                    "tests/infrastructure",
                    "tests/integration",
                    "-q",
                ),
                cwd=ROOT_DIR,
            )
        )

    if not skip_functional:
        steps.append(
            TestStep(
                name="functional",
                command=(
                    "docker",
                    "compose",
                    "up",
                    "--build",
                    "--abort-on-container-exit",
                    "--exit-code-from",
                    "tests",
                ),
                cwd=FUNCTIONAL_DIR,
            )
        )

    return steps


# FUNCTION: _emit
# SUMMARY: Write a progress line for the current runner stage to standard output.
def _emit(message: str) -> None:
    sys.stdout.write(f"[run_all_tests] {message}\n")
    sys.stdout.flush()


# FUNCTION: env_sample_drift
# SUMMARY: Report the sample keys a local env file is missing or answers differently.
# INPUT: sample (Mapping[str, str | None]): Keys and defaults the committed sample declares.
# INPUT: current (Mapping[str, str | None]): Keys the local, git-ignored env file carries.
# OUTPUT: (list[str]): Sorted sample keys absent from `current` or holding a different value.
# NOTE: Asymmetric on purpose. A key the local file ADDS is a deliberate override and is not
# drift; a key the sample gained, or a default it changed, is — the local file is generated once
# and then never compared again, so it keeps answering with last month's value.
def env_sample_drift(
    sample: Mapping[str, str | None], current: Mapping[str, str | None]
) -> list[str]:
    return sorted(
        key for key, value in sample.items() if key not in current or current[key] != value
    )


# FUNCTION: _parse_env_file
# SUMMARY: Read an env file into a mapping, tolerating comments, blanks, quotes and `export`.
# INPUT: path (Path): File to read; a missing file reads as empty.
# OUTPUT: (dict[str, str]): Key to value, with `export ` dropped and surrounding quotes stripped.
# NOTE: Hand-rolled rather than `dotenv_values` because this runner is the one script that must
# work before the project's environment is installed. The syntax it needs to understand is the
# syntax docker compose reads: KEY=value, `#` comments, optional quotes — plus two conventions
# that would otherwise be reported as drift on every run: a leading `export ` (so the file can be
# sourced by a shell) and a trailing comment after an unquoted value. `=` inside a value is kept,
# because `partition` splits on the first one only.
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
            # **LOGIC_STEP**: Only an unquoted value can carry a trailing comment; inside quotes
            # a `#` is part of the value, and a password is exactly where one turns up.
            value = value.split("#", 1)[0].strip()
        values[key] = value
    return values


# FUNCTION: _warn_on_functional_env_drift
# SUMMARY: Name the stale keys of tests/functional/.env before Docker Compose reads it.
# NOTE: A warning, not a failure: the local file is allowed to differ (a port taken by another
# stack, a password someone changed), and this runner has no way to tell a deliberate override
# from a forgotten one. What it can do is say which keys differ BEFORE the containers start, so a
# suite that fails two layers down inside Docker is not the first anyone hears of it.
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


# FUNCTION: _ensure_functional_env
# SUMMARY: Ensure the functional test suite has a concrete env file before Docker Compose starts.
# RAISES: FileNotFoundError: When the functional env sample file is missing.
def _ensure_functional_env() -> None:
    if FUNCTIONAL_ENV_FILE.exists():
        _warn_on_functional_env_drift()
        return
    if not FUNCTIONAL_ENV_SAMPLE.exists():
        raise FileNotFoundError(f"Functional env sample not found: {FUNCTIONAL_ENV_SAMPLE}")
    shutil.copyfile(FUNCTIONAL_ENV_SAMPLE, FUNCTIONAL_ENV_FILE)
    _emit(f"created functional env file at {FUNCTIONAL_ENV_FILE}")


# FUNCTION: _run_command
# SUMMARY: Execute one command step and fail fast when the subprocess returns a non-zero exit status.
# RAISES: subprocess.CalledProcessError: When the subprocess exits with a non-zero status.
def _run_command(step: TestStep) -> None:
    _emit(f"running {step.name}: {' '.join(step.command)}")
    subprocess.run(step.command, cwd=step.cwd, check=True)


# FUNCTION: _cleanup_functional_stack
# SUMMARY: Stop and remove the functional Docker Compose stack after the functional suite finishes.
def _cleanup_functional_stack() -> None:
    subprocess.run(
        ("docker", "compose", "down", "-v"),
        cwd=FUNCTIONAL_DIR,
        check=False,
    )


# FUNCTION: _show_functional_logs
# SUMMARY: Emit functional Docker Compose logs after a functional suite failure to aid agent debugging.
def _show_functional_logs() -> None:
    subprocess.run(
        ("docker", "compose", "logs"),
        cwd=FUNCTIONAL_DIR,
        check=False,
    )


# FUNCTION: _run_functional_step
# SUMMARY: Execute the functional Docker Compose suite with env bootstrap and guaranteed cleanup.
# RAISES: subprocess.CalledProcessError: When the functional suite fails.
def _run_functional_step(step: TestStep) -> None:
    _ensure_functional_env()
    try:
        _run_command(step)
    except subprocess.CalledProcessError:
        _show_functional_logs()
        raise
    finally:
        _cleanup_functional_stack()


# FUNCTION: main
# SUMMARY: Execute the requested set of Mayak test suites through the canonical runner.
# INPUT: argv (Sequence[str] | None): Optional CLI argument list used by tests or the default process argv.
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


# FUNCTION: __main__
# SUMMARY: Module entrypoint that executes the canonical test runner as a CLI script.
if __name__ == "__main__":
    raise SystemExit(main())
