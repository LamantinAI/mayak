# FILE: tests/application/test_db_tier_contract.py
# SUMMARY: Verify the db tier fails loudly without its database, and stands aside only when the project declares none.
# NOTE: Two regressions nothing else in the suite would show. A tier that skipped itself on an
# unreachable database would turn `make test` green on every machine without Docker, and SQL would
# be checked again only in CI. And CI's POSTGRES_ENABLED=false job has Docker, so a tier that ignored
# the flag would start a container there and pass — hiding that a project without a database had
# started to need one. Each check runs pytest on tests/db in a subprocess pointed at a port nothing
# listens on, so no container is started and neither takes more than about a second.

import os
import subprocess
import sys
from pathlib import Path

import pytest

# ATTRIBUTE: _ROOT (Path)
# SUMMARY: Repository root, where the subprocess pytest runs.
_ROOT = Path(__file__).resolve().parents[2]

# ATTRIBUTE: _NOWHERE (str)
# SUMMARY: A database URL whose port refuses every connection; its name passes the tier's suffix guard.
_NOWHERE = "postgresql://nobody:nothing@127.0.0.1:1/nowhere_test"  # allow-secret: a port nothing listens on


# FUNCTION: _run_tier
# SUMMARY: Run pytest on part of the db tier with TEST_DATABASE_URL pointing nowhere.
# INPUT: target (str): The tests/db path to run.
# INPUT: postgres_enabled (str): The project's POSTGRES_ENABLED for the subprocess.
# OUTPUT: (subprocess.CompletedProcess[str]): The finished run, output captured.
def _run_tier(target: str, postgres_enabled: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "--no-cov", "-p", "no:cacheprovider"],
        cwd=_ROOT,
        env=os.environ | {"TEST_DATABASE_URL": _NOWHERE, "POSTGRES_ENABLED": postgres_enabled},
        capture_output=True,
        text=True,
        timeout=120,
    )


# FUNCTION: test_an_unreachable_database_fails_the_tier_instead_of_skipping_it
# SUMMARY: Verify a missing database is an error naming the address, never a skip.
@pytest.mark.unit
def test_an_unreachable_database_fails_the_tier_instead_of_skipping_it() -> None:
    done = _run_tier("tests/db/test_migrations_match_models.py", postgres_enabled="true")

    assert done.returncode == 1, done.stdout[-1500:]
    assert "cannot reach PostgreSQL at 127.0.0.1:1" in done.stdout
    assert "1 error" in done.stdout and "skipped" not in done.stdout


# FUNCTION: test_a_project_without_a_database_deselects_the_tier_and_says_so
# SUMMARY: Verify POSTGRES_ENABLED=false runs none of the tier and prints why.
@pytest.mark.unit
def test_a_project_without_a_database_deselects_the_tier_and_says_so() -> None:
    done = _run_tier("tests/db", postgres_enabled="false")

    # **LOGIC_STEP**: 5 is pytest's "no tests ran": every test here was deselected. Inside `make
    # test` the rest of the suite runs alongside, so the exit code there is the suite's own.
    assert done.returncode == 5, done.stdout[-1500:]
    assert "not run — POSTGRES_ENABLED=false declares no database" in done.stdout
    assert "error" not in done.stdout.lower()
