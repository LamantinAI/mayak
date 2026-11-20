# FILE: tests/template/test_agent_onboarding.py
# SUMMARY: Verify bootstrap refusal and E2E selection from a real Git diff.

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.bootstrap_project import bootstrap
from scripts.verify_change import needs_e2e


def _repo(tmp_path: Path) -> None:
    subprocess.run(("git", "init", "-q"), cwd=tmp_path, check=True)
    subprocess.run(
        (
            "git",
            "-c",
            "user.name=Probe",
            "-c",
            "user.email=probe@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "initial",
        ),
        cwd=tmp_path,
        check=True,
    )


def test_bootstrap_refuses_the_canonical_origin_before_reading_metadata(tmp_path: Path) -> None:
    _repo(tmp_path)
    subprocess.run(
        ("git", "remote", "add", "origin", "git@github.com:LamantinAI/mayak.git"),
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(ValueError, match="origin is the Mayak template"):
        bootstrap(tmp_path, name="Room Book", domain="Bookings", database="none", uv="uv")

    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / "docs").exists()


def test_e2e_selection_includes_untracked_routes_but_ignores_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "README.md").write_text("An example\n")
    scope = tmp_path / "project" / "infrastructure" / "api" / "endpoints"
    scope.mkdir(parents=True)

    assert not needs_e2e("HEAD")
    (scope / "preview.py").write_text("example = 1\n")
    assert needs_e2e("HEAD")
