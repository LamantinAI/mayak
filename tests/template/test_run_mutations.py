# FILE: tests/template/test_run_mutations.py
# SUMMARY: The mutation runner measures the vertical's tests, not the template's checks of its files.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts import run_mutations


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
