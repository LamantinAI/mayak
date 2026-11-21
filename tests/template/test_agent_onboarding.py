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


def test_import_coverage_does_not_count_as_running_a_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.verify_change import unexercised_handlers

    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    filename = "infrastructure/api/endpoints/preview.py"
    route = tmp_path / "project" / filename
    route.parent.mkdir(parents=True)
    route.write_text(
        "router = APIRouter()\n\n@router.get('/preview')\nasync def preview():\n    return 'ok'\n"
    )
    report = tmp_path / "coverage.xml"
    xml = (
        f'<coverage><packages><classes><class filename="{filename}">'
        '<lines><line number="1" hits="1"/><line number="4" hits="1"/>'
        '<line number="5" hits="{body_hits}"/></lines></class></classes></packages></coverage>'
    )
    report.write_text(xml.format(body_hits=0))
    assert unexercised_handlers(report) == (1, [f"project/{filename}:4 preview"])

    report.write_text(xml.format(body_hits=1))
    assert unexercised_handlers(report) == (1, [])


def test_bootstrap_does_not_forward_an_ambient_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    from typing import Any

    _repo(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "project_context.json").write_text(
        json.dumps(
            {
                "project_name": "Room Book",
                "domain": "Bookings",
                "database": "postgres",
                "is_template": False,
                "template": {},
            }
        )
    )
    monkeypatch.setenv("POSTGRES_HOST", "external.example.invalid")
    monkeypatch.setenv("POSTGRES_PORT", "30001")
    monkeypatch.setenv("MAKEFLAGS", "-- POSTGRES_HOST=external.example.invalid")
    run = subprocess.run
    startup_envs: list[dict[str, str]] = []

    def checked_run(command: tuple[str, ...], **kwargs: Any) -> Any:
        if command[0] == "git":
            return run(command, **kwargs)
        if command[1] in {"db-up-worktree", "smoke-local"}:
            startup_envs.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", checked_run)
    bootstrap(tmp_path, name="Room Book", domain="Bookings", database="postgres", uv="uv")

    assert len(startup_envs) == 2
    for env in startup_envs:
        assert env["POSTGRES_ENABLED"] == "true"
        assert not {"POSTGRES_HOST", "POSTGRES_PORT", "MAKEFLAGS"} & env.keys()


def test_verify_detects_a_business_logic_only_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    service = Path("project/application/quote.py")
    service.parent.mkdir(parents=True)
    service.write_text("shipping = 500\n")
    assert needs_e2e("HEAD")


def test_verify_refuses_a_staged_endpoint_even_if_working_copy_matches_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.verify_change import _working_diff

    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    endpoint = Path("project/infrastructure/api/endpoints/quote.py")
    endpoint.parent.mkdir(parents=True)
    endpoint.write_text("status = 200\n")
    subprocess.run(("git", "add", str(endpoint)), check=True)
    subprocess.run(
        (
            "git",
            "-c",
            "user.name=Probe",
            "-c",
            "user.email=probe@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ),
        check=True,
    )
    endpoint.write_text("status = 201\n")
    subprocess.run(("git", "add", str(endpoint)), check=True)
    endpoint.write_text("status = 200\n")

    with pytest.raises(ValueError, match="index and working tree disagree"):
        _working_diff("HEAD")


def test_empty_diff_reports_inapplicable_instead_of_coverage_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.verify_change import _coverage

    assert _coverage("HEAD", set(), tmp_path / "coverage.xml", "uv", "diff-cover") == 0
    result = capsys.readouterr().out
    assert "N/A (no paths changed)" in result
    assert "PASS" not in result


def test_clean_project_without_integration_base_runs_routes_and_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import verify_change

    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["verify_change", "--diff-cover", "diff-cover"])
    calls: list[str] = []

    def fake_call(command: tuple[str, ...], **kwargs: object) -> int:
        calls.append(command[-1])
        return 0

    monkeypatch.setattr(verify_change.subprocess, "call", fake_call)
    monkeypatch.setattr(verify_change, "unexercised_handlers", lambda report: (2, []))

    assert verify_change.main() == 0
    assert calls == ["quality-gates", "test-e2e"]
    output = capsys.readouterr().out
    assert "routes: PASS (2 of 2" in output
    assert "diff-coverage: NOT MEASURED" in output
    assert output.splitlines()[-1] == "verify: PASS — project only; no change was measured"


def test_git_default_ref_wins_over_origin_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.verify_change import _resolve_base

    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    subprocess.run(("git", "update-ref", "refs/remotes/origin/main", "HEAD"), check=True)
    subprocess.run(("git", "update-ref", "refs/remotes/origin/trunk", "HEAD"), check=True)
    subprocess.run(
        ("git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"),
        check=True,
    )
    assert _resolve_base("") == "origin/trunk"
    assert _resolve_base("HEAD") == "HEAD"


@pytest.mark.parametrize("state", ["clean", "unstaged", "staged", "untracked"])
def test_verify_reports_the_current_commit_and_actual_uncommitted_paths(
    state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import verify_change

    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    tracked = tmp_path / "tracked.py"
    tracked.write_text("value = 1\n")
    subprocess.run(("git", "add", str(tracked)), check=True)
    subprocess.run(
        (
            "git",
            "-c",
            "user.name=Probe",
            "-c",
            "user.email=probe@example.invalid",
            "commit",
            "-qm",
            "tracked",
        ),
        check=True,
    )
    (tmp_path / ".git" / "info" / "exclude").write_text("coverage.xml\n")
    (tmp_path / "coverage.xml").write_text("ignored")
    if state in {"unstaged", "staged"}:
        tracked.write_text("value = 2\n")
        if state == "staged":
            subprocess.run(("git", "add", str(tracked)), check=True)
    if state == "untracked":
        (tmp_path / "new.py").write_text("value = 3\n")

    head = subprocess.check_output(("git", "rev-parse", "--short=12", "HEAD"), text=True).strip()
    monkeypatch.setattr(
        "sys.argv",
        ["verify_change", "--base", "HEAD^", "--diff-cover", "diff-cover", "--coverage-only"],
    )
    monkeypatch.setattr(verify_change, "_check_routes", lambda report: 0)
    monkeypatch.setattr(verify_change, "_coverage", lambda *args: 0)

    assert verify_change.main() == 0
    change = capsys.readouterr().out.splitlines()[0]
    assert f"...{head};" in change
    assert change.endswith(f"{int(state != 'clean')} uncommitted")
