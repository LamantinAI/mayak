# FILE: tests/template/test_agent_onboarding.py
# SUMMARY: Verify bootstrap refusal and E2E selection from a real Git diff.

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

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


def _existing_product(root: Path, database: str) -> None:
    import json

    _repo(root)
    docs = root / "docs"
    docs.mkdir()
    (docs / "project_context.json").write_text(
        json.dumps(
            {
                "project_name": "Room Book",
                "domain": "Bookings",
                "database": database,
                "is_template": False,
                "template": {},
            }
        )
    )


# Bootstrap's steps after the identity, recorded instead of run: the command and the environment
# each make received. `start` stands in for what `make db-up-worktree` does to the port marker.
def _record_startup(
    monkeypatch: pytest.MonkeyPatch, start: Callable[[Path], None]
) -> list[tuple[str, dict[str, str]]]:
    run = subprocess.run
    calls: list[tuple[str, dict[str, str]]] = []

    def recorded(command: tuple[str, ...], **kwargs: Any) -> Any:
        if command[0] == "git":
            return run(command, **kwargs)
        calls.append((command[1], dict(kwargs.get("env") or {})))
        if command[1] == "db-up-worktree":
            start(kwargs["cwd"])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", recorded)
    return calls


def _outer_shell_names_another_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "external.example.invalid")
    monkeypatch.setenv("POSTGRES_PORT", "30001")
    monkeypatch.setenv("POSTGRES_DB", "theirs")
    monkeypatch.setenv("MAKEFLAGS", "-- POSTGRES_HOST=external.example.invalid")


def _assign_port(port: str) -> Callable[[Path], None]:
    def start(root: Path) -> None:
        marker = root / ".mayak-postgres-port"
        replacement = root / ".mayak-postgres-port.new"
        replacement.write_text(port + "\n", encoding="utf-8")
        replacement.replace(marker)

    return start


# The shell's database and the parent make's overrides reach no step; the smoke step — the
# reachability probe, Alembic and the HTTP server under it — is handed exactly the loopback port
# the start just recorded. v1.3.1 handed it nothing, and the settings loader went back to the
# address .env named whenever that was not a host it knew as loopback.
def test_bootstrap_hands_the_smoke_step_the_port_the_start_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _existing_product(tmp_path, "postgres")
    (tmp_path / ".mayak-postgres-port").write_text("40001\n", encoding="utf-8")
    _outer_shell_names_another_database(monkeypatch)
    calls = _record_startup(monkeypatch, _assign_port("40123"))

    bootstrap(tmp_path, name="Room Book", domain="Bookings", database="postgres", uv="uv")

    (_, init), (_, start), (_, smoke) = calls
    assert [name for name, _ in calls] == ["init-project", "db-up-worktree", "smoke-local"]
    assert not {"POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "MAKEFLAGS"} & start.keys()
    assert start["POSTGRES_ENABLED"] == "true"
    postgres = {key: value for key, value in smoke.items() if key.startswith("POSTGRES_")}
    assert postgres == {
        "POSTGRES_ENABLED": "true",
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "40123",
    }
    assert "MAKEFLAGS" not in smoke


def _record_nothing(root: Path) -> None:
    pass


def _record_garbage(root: Path) -> None:
    _assign_port("5432; drop")(root)


def _record_port_zero(root: Path) -> None:
    _assign_port("0")(root)


# A start that left no fresh, valid port stops bootstrap before anything migrates: the port a
# previous start recorded may belong to a database that is gone or to another one.
@pytest.mark.parametrize(
    ("start", "reason"),
    [
        (_record_nothing, "did not record a port"),
        (_record_garbage, "invalid port"),
        (_record_port_zero, "invalid port"),
    ],
    ids=["stale-marker", "not-a-number", "port-zero"],
)
def test_bootstrap_refuses_a_port_the_start_did_not_confirm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    start: Callable[[Path], None],
    reason: str,
) -> None:
    _existing_product(tmp_path, "postgres")
    (tmp_path / ".mayak-postgres-port").write_text("40001\n", encoding="utf-8")
    _outer_shell_names_another_database(monkeypatch)
    calls = _record_startup(monkeypatch, start)

    with pytest.raises(ValueError, match=reason):
        bootstrap(tmp_path, name="Room Book", domain="Bookings", database="postgres", uv="uv")

    assert [name for name, _ in calls] == ["init-project", "db-up-worktree"]


def test_bootstrap_stops_when_the_database_does_not_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _existing_product(tmp_path, "postgres")

    def fail(root: Path) -> None:
        raise subprocess.CalledProcessError(2, ("make", "db-up-worktree"))

    calls = _record_startup(monkeypatch, fail)

    with pytest.raises(subprocess.CalledProcessError):
        bootstrap(tmp_path, name="Room Book", domain="Bookings", database="postgres", uv="uv")

    assert [name for name, _ in calls] == ["init-project", "db-up-worktree"]


def test_bootstrap_without_a_database_starts_none_and_names_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _existing_product(tmp_path, "none")
    _outer_shell_names_another_database(monkeypatch)
    calls = _record_startup(monkeypatch, _record_nothing)

    bootstrap(tmp_path, name="Room Book", domain="Bookings", database="none", uv="uv")

    assert [name for name, _ in calls] == ["init-project", "smoke-local"]
    postgres = {key: value for key, value in calls[1][1].items() if key.startswith("POSTGRES_")}
    assert postgres == {"POSTGRES_ENABLED": "false"}


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
    assert "verify: scope: project only (clean tree, no base)" in output.splitlines()
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


def _commit_all(message: str) -> str:
    subprocess.run(("git", "add", "-A"), check=True)
    identity = ("-c", "user.name=Probe", "-c", "user.email=probe@example.invalid")
    subprocess.run(("git", *identity, "commit", "-qm", message), check=True)
    return subprocess.check_output(("git", "rev-parse", "HEAD"), text=True).strip()


def _verify(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *arguments: str
) -> tuple[list[str], list[str]]:
    from scripts import verify_change

    calls: list[str] = []

    def fake_call(command: tuple[str, ...], **kwargs: object) -> int:
        calls.append(command[-1])
        return 0

    monkeypatch.setattr("sys.argv", ["verify_change", "--diff-cover", "diff-cover", *arguments])
    monkeypatch.setattr(verify_change.subprocess, "call", fake_call)
    monkeypatch.setattr(verify_change, "unexercised_handlers", lambda report: (2, []))
    monkeypatch.setattr(verify_change, "_coverage", lambda *args: 0)
    assert verify_change.main() == 0
    return calls, capsys.readouterr().out.splitlines()


# Measured on 1.2.0: a checkout without an integration branch whose code change was already
# committed reported only its uncommitted README.md, built no image, and still ended with
# `verify: PASS — project and change`, the line AGENTS.md tells an agent to quote at handoff.
@pytest.fixture
def committed_then_prose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git" / "info" / "exclude").write_text("coverage.xml\n")
    start = subprocess.check_output(("git", "rev-parse", "HEAD"), text=True).strip()
    Path("project").mkdir()
    Path("project/a.py").write_text("value = 1\n")
    _commit_all("code")
    Path("README.md").write_text("prose\n")
    return start


def test_verify_without_a_base_says_commits_were_not_measured(
    committed_then_prose: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls, lines = _verify(monkeypatch, capsys)

    assert calls == ["quality-gates"]
    assert "verify: scope: uncommitted paths only (no base); commits are not measured" in lines
    assert "commits were not examined" in next(line for line in lines if "test-e2e" in line)
    assert "verify: full handoff: make verify DIFF_COMPARE_BRANCH=<starting commit>" in lines
    assert (
        lines[-1] == "verify: PASS — project and uncommitted paths only; commits were not measured"
    )
    assert not any("project and change" in line for line in lines)


@pytest.mark.parametrize("source", ["explicit", "auto"])
def test_verify_with_a_base_measures_the_committed_change(
    source: str,
    committed_then_prose: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if source == "auto":
        subprocess.run(("git", "update-ref", "refs/remotes/origin/main", committed_then_prose))
    arguments = ("--base", committed_then_prose) if source == "explicit" else ()
    calls, lines = _verify(monkeypatch, capsys, *arguments)

    assert calls == ["quality-gates", "test-e2e"]
    assert lines[0].endswith("2 changed paths, 1 uncommitted")
    base = committed_then_prose if source == "explicit" else "origin/main"
    assert f"verify: scope: change since {base} ({source} base)" in lines
    assert lines[-1] == "verify: PASS — project and change"


def test_verify_with_a_base_on_a_clean_tree_measures_the_commits(
    committed_then_prose: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    Path("README.md").unlink()
    calls, lines = _verify(monkeypatch, capsys, "--base", committed_then_prose)

    assert calls == ["quality-gates", "test-e2e"]
    assert lines[0].endswith("1 changed paths, 0 uncommitted")
    assert lines[-1] == "verify: PASS — project and change"


def test_verify_without_a_base_still_builds_for_an_uncommitted_code_change(
    committed_then_prose: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    Path("project/a.py").write_text("value = 2\n")
    calls, lines = _verify(monkeypatch, capsys)

    assert calls == ["quality-gates", "test-e2e"]
    assert (
        lines[-1] == "verify: PASS — project and uncommitted paths only; commits were not measured"
    )


def test_verify_coverage_only_reports_its_scope(
    committed_then_prose: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls, lines = _verify(monkeypatch, capsys, "--coverage-only")

    assert calls == []
    assert lines[1] == "verify: scope: uncommitted paths only (no base); commits are not measured"
