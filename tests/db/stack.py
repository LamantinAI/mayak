# FILE: tests/db/stack.py
# SUMMARY: Start, locate and stop the db tier's PostgreSQL for this checkout.
# One implementation, three callers: tests/db/conftest.py (so a bare `uv run pytest tests/db`
# brings the database up by itself), `make test-db-down`, and anyone who wants the URL. A database
# supplied from outside — CI's service container — is named by TEST_DATABASE_URL, and then nothing
# here starts or stops anything.
#
#   uv run python tests/db/stack.py up | url | down

from __future__ import annotations

import os
import subprocess
import sys
import zlib
from pathlib import Path

# Repository root; the digest of its absolute path keeps two checkouts apart.
ROOT_DIR = Path(__file__).resolve().parents[2]

# The tier's compose file, independent of the application's.
COMPOSE_FILE = Path(__file__).resolve().parent / "docker-compose.yml"

# Environment variable naming a database this module must use as is.
EXTERNAL_URL_ENV = "TEST_DATABASE_URL"

# Per-checkout number the compose project and the published port are derived from.
# Same idea as run_all_tests.functional_compose_project and the Makefile's db-up-worktree:
# two worktrees running `make test` at once must not share one database, because each drops and
# recreates it every session and empties its tables before every test. Ports fold into 30000-39999, clear of the Makefile's worktree
# databases (20000-29999) and of the application defaults.
_DIGEST = zlib.crc32(str(ROOT_DIR).encode("utf-8"))

PROJECT = f"test-db-{_DIGEST}"

# Host port this checkout's test database is published on (127.0.0.1 only).
PORT = _DIGEST % 10000 + 30000


def database_url() -> str:
    return (
        os.environ.get(EXTERNAL_URL_ENV)
        or (
            f"postgresql://mayak_test:mayak_test@127.0.0.1:{PORT}/mayak_test"  # allow-secret: throwaway test container
        )
    )


def _compose(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *arguments],
        capture_output=True,
        text=True,
        env=os.environ | {"TEST_POSTGRES_PORT": str(PORT)},
        timeout=180,
    )


# Raises RuntimeError when the container cannot be started; the message says why and what to do.
# `up --wait` on a running, healthy container returns in about a second, so this is called
# on every session rather than remembered — a stale "it was up last time" is how a suite ends up
# timing out against a container somebody stopped.
def ensure_up() -> str:
    if os.environ.get(EXTERNAL_URL_ENV):
        return database_url()
    try:
        done = _compose("up", "-d", "--wait", "test-db")
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(_cannot_start(str(exc))) from exc
    if done.returncode != 0:
        raise RuntimeError(_cannot_start(done.stderr.strip() or done.stdout.strip()))
    return database_url()


# Explain a failed start with the ways out, in the order people reach for them.
def _cannot_start(detail: str) -> str:
    return (
        "The db tier could not start its PostgreSQL "
        f"(docker compose -p {PROJECT}, port {PORT}):\n  {detail[-600:]}\n"
        "Start Docker and rerun; or point the tier at a PostgreSQL you control with "
        f"{EXTERNAL_URL_ENV}=postgresql://user:password@host:port/<name>_test (that database is "  # allow-secret: an example
        "dropped and recreated every session); or, if this project has no relational store, "
        "set POSTGRES_ENABLED=false in .env."
    )


# Remove this checkout's test database container; its data lived in tmpfs and goes with it.
def stop() -> int:
    return _compose("down", "-v", "--remove-orphans").returncode


# Command-line entry for `make test-db-down` and for printing the URL.
def main(argv: list[str]) -> int:
    command = argv[0] if argv else "url"
    if command == "up":
        print(ensure_up())
        return 0
    if command == "down":
        return stop()
    if command == "url":
        print(database_url())
        return 0
    print("usage: stack.py up | url | down", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
