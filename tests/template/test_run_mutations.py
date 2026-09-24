# FILE: tests/template/test_run_mutations.py
# SUMMARY: The mutation runner measures the vertical's tests, not the template's checks of its files.

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from scripts import run_mutations


# A tier's pytest must not collect tests/template: the manifest test there fails on any byte a
# defect changes, and the run on 2026-09-24 credited it with every catch.
@pytest.mark.unit
def test_a_tier_runs_pytest_without_the_template_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "-x")
    seen: dict[str, Any] = {}

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        seen.update(options["env"])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(run_mutations.subprocess, "run", run)

    result = run_mutations.run_tier([sys.executable, "-c", "pass"])

    assert result.state == "green"
    assert seen["PYTEST_ADDOPTS"].split() == ["-x", "--ignore=tests/template"]
