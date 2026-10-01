# FILE: tests/template/test_run_mutations.py
# SUMMARY: The mutation runner measures the vertical's tests, not the template's checks of its files.

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import run_mutations


# A merge upstream of the vertical can rewrite the anchor text an entry mutates (PR #24 did, to
# health.py) without breaking anything the fast gate runs — run_mutations.py is not on the CI
# critical path. Catching a stranded anchor here, in the suite CI does run, is what makes that
# visible instead of silently turning "not measuring" the next time the catalogue actually runs.
@pytest.mark.unit
def test_catalogue_anchors_each_match_once() -> None:
    catalogue = json.loads(run_mutations.DEFAULT_CATALOGUE.read_text(encoding="utf-8"))
    entries = catalogue["mutations"] if isinstance(catalogue, dict) else catalogue
    for entry in entries:
        source = (run_mutations.ROOT_DIR / entry["file"]).read_text(encoding="utf-8")
        assert source.count(entry["find"]) == 1, entry["id"]


# The manifest test in tests/template fails on any byte a defect changes, and on 2026-09-24 a run
# credited it with every catch. A tier's pytest gets tests/template on its command line, as
# run_all_tests.py passes it, and must still not run a test from there.
@pytest.mark.unit
def test_a_tier_runs_no_test_from_tests_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for directory, body in (("app", "pass"), ("template", "assert False")):
        (tmp_path / "tests" / directory).mkdir(parents=True)
        (tmp_path / "tests" / directory / f"test_{directory}.py").write_text(
            f"def test_it() -> None:\n    {body}\n", encoding="utf-8"
        )
    monkeypatch.setattr(run_mutations, "ROOT_DIR", tmp_path)
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)

    result = run_mutations.run_tier(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/app",
            "tests/template",
        ]
    )

    assert (result.state, result.failed) == ("green", [])


# One tier of a fake repository: green on the clean code, and on the mutated code whatever the
# scenario says. It counts its runs, so the third — the final control — can be the one that fails.
_TIER = """
import pathlib, sys, time
root, scenario = pathlib.Path(sys.argv[1]), sys.argv[2]
runs = root / "runs"
count = int(runs.read_text()) + 1 if runs.exists() else 1
runs.write_text(str(count))
mutated = "broken" in (root / "code.py").read_text()
if scenario == "final_control_red" and count == 3:
    mutated, scenario = True, "assertion"
if not mutated:
    sys.exit(0)
if scenario in ("assertion", "final_control_red"):
    print("FAILED tests/test_code.py::test_it - assert 1 == 2")
    sys.exit(1)
if scenario == "setup_error":
    print("ERROR tests/test_code.py::test_it - psycopg.OperationalError: connection refused")
    sys.exit(1)
if scenario == "failed_and_error":
    print("FAILED tests/test_code.py::test_it - assert 1 == 2")
    print("ERROR tests/test_db.py::test_row - psycopg.OperationalError: connection refused")
    sys.exit(1)
if scenario == "infrastructure":
    print("Cannot connect to the Docker daemon at unix:///var/run/docker.sock")
    sys.exit(2)
time.sleep(30)
"""


# Until 2026-09-28 any red tier was a catch — a fixture that could not reach its database, a
# Docker daemon that was down — and --record wrote the baseline before reading the final control.
# Only a test body that failed is a catch; everything else is inconclusive, exits 2 and leaves the
# baseline byte for byte, the one file a later run is judged against.
@pytest.mark.unit
@pytest.mark.parametrize(
    ("scenario", "exit_code"),
    [
        ("assertion", 0),
        ("setup_error", 2),
        ("failed_and_error", 2),
        ("infrastructure", 2),
        ("timeout", 2),
        ("final_control_red", 2),
    ],
)
def test_only_a_failed_test_is_a_catch_and_only_a_whole_run_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str, exit_code: int
) -> None:
    # Under the pre-commit hook git exports GIT_DIR and GIT_INDEX_FILE, and every git call below —
    # the test's and the runner's — would then commit into the repository being committed.
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(name)
    (tmp_path / "code.py").write_text("ok = True\n", encoding="utf-8")
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "-c"]
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run([*git, "core.hooksPath=/dev/null", "add", "code.py"], check=True)
    subprocess.run([*git, "core.hooksPath=/dev/null", "commit", "-qm", "c"], check=True)
    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text(
        json.dumps(
            {
                "subject": "fake",
                "tiers": {"fast": [sys.executable, "-c", _TIER, str(tmp_path), scenario]},
                "mutations": [
                    {"id": "d", "file": "code.py", "find": "ok = True", "replace": "broken"}
                ],
                "baseline": {"commit": "old", "results": {"d": {"verdict": "caught:fast"}}},
            }
        ),
        encoding="utf-8",
    )
    recorded = catalogue.read_bytes()
    monkeypatch.setattr(run_mutations, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(run_mutations, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(run_mutations, "TIER_TIMEOUT_SECONDS", 2)

    code = run_mutations.main(["--catalogue", str(catalogue), "--record"])

    assert code == exit_code
    if exit_code == 0:
        written = json.loads(catalogue.read_text(encoding="utf-8"))["baseline"]["results"]["d"]
        assert written == {"verdict": "caught:fast", "caught_by": {"fast": ["test_it"]}}
    else:
        assert catalogue.read_bytes() == recorded
