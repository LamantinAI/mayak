# FILE: tests/template/test_validate_migrations.py
# SUMMARY: Unit tests for the Alembic migration validation quality gate.

import os
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest

from scripts.validate_migrations import (
    ALEMBIC_INI_PATH,
    ROOT_DIR,
    _build_commands,
    _revision_heads,
    _revision_ids,
    collect_migration_issues,
    get_migrations_rule_playbook,
    main,
)


# A password with `@ % : /` is common from a generated cloud secret; ConfigParser reads `%` as
# interpolation unless env.py escapes it first (alembic/env.py). Copied to tmp_path so the
# process never touches this checkout's own .env.
@pytest.mark.unit
def test_encoded_password_survives_alembic_offline(tmp_path: Path) -> None:
    for name in ("project", "alembic", "alembic.ini", "pyproject.toml"):
        source = ROOT_DIR / name
        copy = shutil.copytree if source.is_dir() else shutil.copy2
        copy(source, tmp_path / name)
    env = dict(os.environ, POSTGRES_PASSWORD="p@ss%w0rd:x/y", POSTGRES_HOST="localhost")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


class TestValidateMigrations:
    @pytest.mark.unit
    def test_build_commands_returns_upgrade_then_check(self) -> None:
        commands = _build_commands()

        assert [command.name for command in commands] == ["upgrade-head", "check"]
        assert commands[0].argv == (
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_INI_PATH),
            "upgrade",
            "head",
        )
        assert commands[1].argv == (
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_INI_PATH),
            "check",
        )

    @pytest.mark.unit
    def test_main_skips_when_database_unreachable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("MIGRATIONS_ALLOW_SKIP", raising=False)
        # Pin the toggle so this test keeps exercising the unreachable path even
        # in a checkout whose .env declares no relational store — otherwise it would silently
        # start asserting the migrations.database_disabled branch instead.
        monkeypatch.setenv("POSTGRES_ENABLED", "true")

        exit_code = main()
        output = capsys.readouterr().out

        assert exit_code == 0
        assert "MIGRATIONS NOT VERIFIED" in output

    # This is the trap for a real finding: the old skip message read
    # like every other passing line in a `make quality-gates` run, so a migration that dropped a
    # column instead of renaming it cleared every local gate on both projects the audit built from
    # this template. The banner text has to be present on the skip path and absent on the path
    # where alembic actually ran — either half missing reopens exactly that gap.
    @pytest.mark.unit
    def test_not_verified_banner_appears_only_when_the_gate_actually_skipped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("MIGRATIONS_ALLOW_SKIP", raising=False)
        monkeypatch.setenv("POSTGRES_ENABLED", "true")

        # Skipped: database unreachable, nothing verified.
        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: False)
        skip_exit_code = main()
        skip_output = capsys.readouterr().out

        # Verified: database reachable, both alembic steps actually ran and passed.
        # The commands are recorded, not merely swallowed. Asserting only the exit
        # code and the absent banner left this green when `_build_commands()` returned nothing at
        # all — a run that verifies neither `upgrade head` nor `check` and reports a clean pass,
        # which is the very state the banner exists to make visible.
        commands: list[list[str]] = []

        def _record(argv: list[str], *args: object, **kwargs: object) -> None:
            commands.append(list(argv))

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)
        monkeypatch.setattr(validate_migrations, "_stamped_revision_ids", lambda: set())
        monkeypatch.setattr(subprocess, "run", _record)
        verified_exit_code = main()
        verified_output = capsys.readouterr().out

        assert skip_exit_code == 0
        assert verified_exit_code == 0
        assert "MIGRATIONS NOT VERIFIED" in skip_output
        assert "MIGRATIONS NOT VERIFIED" not in verified_output
        alembic_steps = [argv for argv in commands if "alembic" in " ".join(argv)]
        assert any("upgrade" in " ".join(argv) for argv in alembic_steps), commands
        assert any("check" in " ".join(argv) for argv in alembic_steps), commands

    # Ensure CI cannot silently skip the gate, which is how broken revisions reached main.
    @pytest.mark.unit
    def test_main_fails_when_database_unreachable_in_ci(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: False)
        monkeypatch.setenv("CI", "true")
        monkeypatch.delenv("MIGRATIONS_ALLOW_SKIP", raising=False)
        monkeypatch.setenv("POSTGRES_ENABLED", "true")

        exit_code = main()
        output = capsys.readouterr().out

        assert exit_code == 1
        assert "migrations.database_required" in output

    @pytest.mark.unit
    def test_ci_skip_can_be_opted_back_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: False)
        monkeypatch.setenv("CI", "true")
        monkeypatch.setenv("MIGRATIONS_ALLOW_SKIP", "1")
        monkeypatch.setenv("POSTGRES_ENABLED", "true")

        assert main() == 0

    @pytest.mark.unit
    def test_require_database_flag_fails_without_ci(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("MIGRATIONS_ALLOW_SKIP", raising=False)
        monkeypatch.setenv("POSTGRES_ENABLED", "true")

        assert main(["--require-database"]) == 1

    # Ensure the job that runs the gate actually has a database, so the strict mode can pass.
    @pytest.mark.unit
    def test_ci_workflow_provisions_postgres_for_quality_gates(self) -> None:
        workflow = (ROOT_DIR / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        quality_gates_job = workflow.split("  quality-gates:", 1)[1].split("\n  secret-scan:", 1)[0]

        assert "services:" in quality_gates_job
        assert "postgres:" in quality_gates_job
        assert "POSTGRES_HOST:" in quality_gates_job

    @pytest.mark.unit
    def test_main_runs_upgrade_and_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)
        # Reachable is forced, so the stamp reader would otherwise open a real
        # connection to whatever .env names — a unit test reaching a developer's database.
        monkeypatch.setattr(validate_migrations, "_stamped_revision_ids", lambda: set())

        recorded_calls: list[tuple[tuple[str, ...], str]] = []

        def fake_run(
            argv: tuple[str, ...],
            *,
            cwd: Path,
            check: bool,
            capture_output: bool = False,
            text: bool = False,
        ) -> None:
            assert check is True
            assert capture_output is True
            assert text is True
            recorded_calls.append((argv, str(cwd)))

        monkeypatch.setattr(subprocess, "run", fake_run)

        exit_code = main()

        assert exit_code == 0
        assert recorded_calls == [
            (_build_commands()[0].argv, str(ROOT_DIR)),
            (_build_commands()[1].argv, str(ROOT_DIR)),
        ]

    @pytest.mark.unit
    def test_main_returns_subprocess_exit_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)
        # Reachable is forced, so the stamp reader would otherwise open a real
        # connection to whatever .env names — a unit test reaching a developer's database.
        monkeypatch.setattr(validate_migrations, "_stamped_revision_ids", lambda: set())

        command_iter = iter(
            [
                None,
                subprocess.CalledProcessError(
                    returncode=2,
                    cmd=_build_commands()[1].argv,
                ),
            ]
        )

        def fake_run(
            argv: tuple[str, ...],
            *,
            cwd: Path,
            check: bool,
            capture_output: bool = False,
            text: bool = False,
        ) -> None:
            assert cwd == ROOT_DIR
            assert check is True
            assert capture_output is True
            assert text is True
            outcome = next(command_iter)
            if isinstance(outcome, subprocess.CalledProcessError):
                raise outcome

        monkeypatch.setattr(subprocess, "run", fake_run)

        exit_code = main()
        output = capsys.readouterr().out

        assert exit_code == 2
        assert "check failed with exit code 2" in output
        assert "make autogenerate-migration MSG=describe_change" in output

    @pytest.mark.unit
    def test_main_reports_upgrade_head_remediation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)
        # Reachable is forced, so the stamp reader would otherwise open a real
        # connection to whatever .env names — a unit test reaching a developer's database.
        monkeypatch.setattr(validate_migrations, "_stamped_revision_ids", lambda: set())

        def fake_run(
            argv: tuple[str, ...],
            *,
            cwd: Path,
            check: bool,
            capture_output: bool = False,
            text: bool = False,
        ) -> None:
            assert cwd == ROOT_DIR
            assert check is True
            assert capture_output is True
            assert text is True
            raise subprocess.CalledProcessError(
                returncode=3,
                cmd=_build_commands()[0].argv,
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        exit_code = main()
        output = capsys.readouterr().out

        assert exit_code == 3
        assert "upgrade-head failed with exit code 3" in output
        assert "Fix the broken migration or local database state" in output


# Tests for migration rule_id playbooks and structured issue collection.
class TestRulePlaybook:
    @pytest.mark.unit
    def test_known_rule_returns_dict(self) -> None:
        playbook = get_migrations_rule_playbook("migrations.head_drift")
        assert isinstance(playbook, dict)
        assert "meaning" in playbook
        assert "next_checks" in playbook

    @pytest.mark.unit
    def test_unknown_rule_returns_none(self) -> None:
        assert get_migrations_rule_playbook("migrations.bogus") is None

    @pytest.mark.unit
    def test_unreachable_db_emits_skip_issue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: False)
        # This test is about the "unreachable" branch specifically, so it must
        # pin both switches that route around it: CI turns the skip into a hard failure, and
        # POSTGRES_ENABLED=false diverts to the "not used by this project" branch entirely.
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("POSTGRES_ENABLED", "true")
        issues = collect_migration_issues(ROOT_DIR)
        assert len(issues) == 1
        assert issues[0].rule_id == "migrations.database_unreachable"
        assert issues[0].returncode == 0  # informational, not a hard failure

    @pytest.mark.unit
    def test_check_failure_emits_head_drift(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)
        # Reachable is forced, so the stamp reader would otherwise open a real
        # connection to whatever .env names — a unit test reaching a developer's database.
        monkeypatch.setattr(validate_migrations, "_stamped_revision_ids", lambda: set())
        # Make upgrade-head succeed and `check` fail.
        upgrade_argv = _build_commands()[0].argv
        check_argv = _build_commands()[1].argv

        def fake_run(
            argv: tuple[str, ...],
            *,
            cwd: Path,
            check: bool,
            capture_output: bool = False,
            text: bool = False,
        ) -> None:
            del cwd, check, capture_output, text
            if tuple(argv) == tuple(upgrade_argv):
                return None
            raise subprocess.CalledProcessError(returncode=2, cmd=check_argv)

        monkeypatch.setattr(subprocess, "run", fake_run)

        issues = collect_migration_issues(ROOT_DIR)
        assert len(issues) == 1
        assert issues[0].rule_id == "migrations.head_drift"
        assert issues[0].command_name == "check"

    # Regression guard: when Alembic fails with a real stderr (e.g. ProgrammingError), MigrationIssue.stderr must contain the captured output and message must hint at it. Closes the bug where the JSON consumer only saw "check failed with exit code N".
    @pytest.mark.unit
    def test_check_failure_captures_alembic_stderr_into_issue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)
        # Reachable is forced, so the stamp reader would otherwise open a real
        # connection to whatever .env names — a unit test reaching a developer's database.
        monkeypatch.setattr(validate_migrations, "_stamped_revision_ids", lambda: set())
        upgrade_argv = _build_commands()[0].argv
        check_argv = _build_commands()[1].argv
        alembic_stderr = (
            "ERROR [alembic.util.messaging] Target database is not up to date.\n"
            "sqlalchemy.exc.ProgrammingError: column 'user_email' already exists\n"
        )

        def fake_run(
            argv: tuple[str, ...],
            *,
            cwd: Path,
            check: bool,
            capture_output: bool = False,
            text: bool = False,
        ) -> None:
            del cwd, check
            assert capture_output is True
            assert text is True
            if tuple(argv) == tuple(upgrade_argv):
                return None
            raise subprocess.CalledProcessError(
                returncode=2,
                cmd=check_argv,
                stderr=alembic_stderr,
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        issues = collect_migration_issues(ROOT_DIR)

        assert len(issues) == 1
        assert issues[0].stderr is not None
        assert "ProgrammingError" in issues[0].stderr
        assert "alembic stderr" in issues[0].message
        assert "ProgrammingError" in issues[0].message


# chain: revision id → down_revision, in any order.
# Returns the script directory to hand to _revision_heads.
def _script_directory(root: Path, chain: dict[str, str | None]) -> Path:
    versions = root / "alembic" / "versions"
    versions.mkdir(parents=True)
    for revision, down_revision in chain.items():
        (versions / f"{revision}.py").write_text(
            f"revision = {revision!r}\ndown_revision = {down_revision!r}\n\n\n"
            "def upgrade() -> None:\n    pass\n\n\ndef downgrade() -> None:\n    pass\n",
            encoding="utf-8",
        )
    return root / "alembic"


# Verify a fork or a dangling down_revision turns the gate red before the database skip.
# The comment on database_skip_is_allowed names "a second alembic head or a dangling
# down_revision" as what the CI backstop exists for — and both used to pass a checkout
# without Postgres green, because every alembic command sat behind the reachability check. Neither
# defect needs a database to see: ScriptDirectory reads the files. Measured: two revisions sharing
# a down_revision, `make quality-gates` exit 0, "migration validation skipped".
class TestRevisionGraphIsCheckedWithoutADatabase:
    @pytest.mark.unit
    def test_the_shipped_revisions_form_one_chain(self) -> None:
        # The real directory, no monkeypatch: this is the read every local gate
        # now makes, and it must work with no .env and no database in the environment.
        assert len(_revision_heads()) == 1

    @pytest.mark.unit
    def test_a_project_without_a_script_directory_has_no_graph_to_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(
            validate_migrations, "_revision_heads", partial(_revision_heads, tmp_path / "gone")
        )
        monkeypatch.setenv("POSTGRES_ENABLED", "false")

        issues = collect_migration_issues(ROOT_DIR)

        assert [issue.rule_id for issue in issues] == ["migrations.database_disabled"]

    @pytest.mark.unit
    def test_a_fork_fails_the_gate_where_it_used_to_skip(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        script_dir = _script_directory(tmp_path, {"aaa": None, "bbb": "aaa", "ccc": "aaa"})
        monkeypatch.setattr(
            validate_migrations, "_revision_heads", partial(_revision_heads, script_dir)
        )
        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("MIGRATIONS_ALLOW_SKIP", raising=False)
        monkeypatch.setenv("POSTGRES_ENABLED", "true")

        exit_code = main()
        output = capsys.readouterr().out

        assert exit_code == 1
        assert "2 heads (bbb, ccc)" in output

    # Verify POSTGRES_ENABLED=false does not hide a fork — the files are wrong either way.
    @pytest.mark.unit
    def test_a_fork_is_reported_even_where_postgres_is_disabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        script_dir = _script_directory(tmp_path, {"aaa": None, "bbb": "aaa", "ccc": "aaa"})
        monkeypatch.setattr(
            validate_migrations, "_revision_heads", partial(_revision_heads, script_dir)
        )
        monkeypatch.setenv("POSTGRES_ENABLED", "false")

        issues = collect_migration_issues(ROOT_DIR)

        assert [issue.rule_id for issue in issues] == ["migrations.multiple_heads"]
        assert issues[0].severity == "error"

    # Verify a down_revision naming no revision is an error, not an alembic traceback.
    @pytest.mark.unit
    def test_a_dangling_down_revision_is_reported(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        script_dir = _script_directory(tmp_path, {"aaa": None, "bbb": "nope"})
        monkeypatch.setattr(
            validate_migrations, "_revision_heads", partial(_revision_heads, script_dir)
        )
        monkeypatch.setenv("POSTGRES_ENABLED", "true")

        # Alembic warns before it raises; the warning is part of the behaviour
        # under test, not noise to let through to the suite's summary.
        with pytest.warns(UserWarning, match="is not present"):
            issues = collect_migration_issues(ROOT_DIR)

        assert [issue.rule_id for issue in issues] == ["migrations.broken_revision_graph"]
        assert "nope" in issues[0].message

    @pytest.mark.unit
    def test_one_chain_still_reaches_the_database_skip(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        script_dir = _script_directory(tmp_path, {"aaa": None, "bbb": "aaa"})
        monkeypatch.setattr(
            validate_migrations, "_revision_heads", partial(_revision_heads, script_dir)
        )
        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("POSTGRES_ENABLED", "true")

        issues = collect_migration_issues(ROOT_DIR)

        assert [issue.rule_id for issue in issues] == ["migrations.database_unreachable"]

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "rule_id", ["migrations.multiple_heads", "migrations.broken_revision_graph"]
    )
    def test_each_offline_rule_has_a_playbook(self, rule_id: str) -> None:
        playbook = get_migrations_rule_playbook(rule_id)

        assert playbook is not None
        read_first = playbook["read_first"]
        assert isinstance(read_first, list)
        assert "alembic/versions/" in read_first


# Verify a database stamped with a revision this branch lacks is named as such, before
# alembic is asked a question it can only answer opaquely.
# Measured in a project built from this template: fifteen worktrees against one
# PostgreSQL container. An agent on one branch ran `alembic upgrade head`; a worktree on a branch
# without that revision then met `Can't locate revision identified by 'af0035d05498'` — reproduced
# verbatim — which arrived as migrations.upgrade_failure, whose playbook offered to reset the
# database. On a database more than one checkout can reach, that drops the schema another branch
# migrated, and it does not fix this at all: the stamp comes back the moment the other branch runs.
class TestADatabaseAheadOfThisBranch:
    @pytest.mark.unit
    def test_a_stamped_revision_this_branch_lacks_fails_the_gate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        script_dir = _script_directory(tmp_path, {"aaa": None, "bbb": "aaa"})
        monkeypatch.setattr(
            validate_migrations, "_revision_ids", partial(_revision_ids, script_dir)
        )
        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)
        monkeypatch.setattr(validate_migrations, "_stamped_revision_ids", lambda: {"zzz"})
        monkeypatch.setenv("POSTGRES_ENABLED", "true")

        def refuse(*args: object, **kwargs: object) -> None:
            raise AssertionError(
                "alembic must not be run once the stamp is already known to be foreign — its "
                "error is the one this rule exists to replace"
            )

        monkeypatch.setattr(subprocess, "run", refuse)

        issues = collect_migration_issues(ROOT_DIR)

        assert [issue.rule_id for issue in issues] == ["migrations.foreign_revision"]
        assert issues[0].severity == "error"
        assert "zzz" in issues[0].message

    @pytest.mark.unit
    def test_a_stamp_this_branch_does_have_is_quiet(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        script_dir = _script_directory(tmp_path, {"aaa": None, "bbb": "aaa"})
        monkeypatch.setattr(
            validate_migrations, "_revision_ids", partial(_revision_ids, script_dir)
        )
        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)
        monkeypatch.setattr(validate_migrations, "_stamped_revision_ids", lambda: {"bbb"})
        monkeypatch.setenv("POSTGRES_ENABLED", "true")
        ran: list[str] = []
        monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: ran.append(str(argv[-1])))

        issues = collect_migration_issues(ROOT_DIR)

        assert issues == []
        assert ran == ["head", "check"]

    # Verify an empty alembic_version reads as "fresh", not as a foreign revision.
    @pytest.mark.unit
    def test_a_database_nobody_has_migrated_yet_is_quiet(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        script_dir = _script_directory(tmp_path, {"aaa": None})
        monkeypatch.setattr(
            validate_migrations, "_revision_ids", partial(_revision_ids, script_dir)
        )
        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)
        monkeypatch.setattr(validate_migrations, "_stamped_revision_ids", lambda: set())
        monkeypatch.setenv("POSTGRES_ENABLED", "true")
        monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)

        issues = collect_migration_issues(ROOT_DIR)

        assert issues == []

    # The whole rule is an argument against one sentence. Reintroducing it
    # elsewhere in this file would undo the rule while leaving it green.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "rule_id",
        ["migrations.foreign_revision", "migrations.upgrade_failure"],
    )
    def test_no_migration_playbook_offers_to_recreate_the_database(self, rule_id: str) -> None:
        playbook = get_migrations_rule_playbook(rule_id)

        assert playbook is not None
        advice = " ".join(
            str(playbook[field]) for field in ("suggested_fix", "likely_fix_shape")
        ).lower()
        assert "reset the local db" not in advice
        assert "restore the database" not in advice

    # Verify the new playbook offers catching up and a database of one's own.
    @pytest.mark.unit
    def test_the_playbook_names_the_way_out(self) -> None:
        playbook = get_migrations_rule_playbook("migrations.foreign_revision")

        assert playbook is not None
        assert "make db-up-worktree" in str(playbook["suggested_fix"])
        assert "git fetch" in str(playbook["suggested_fix"])


# Verify a strict run without a database names a move that can make it pass where it runs.
# Measured on 1.2.0 in the LotTrail and Shelfmark benchmarks: every session that worked with
# the database met this failure under `make verify`, and its message offered
# MIGRATIONS_ALLOW_SKIP=1, which that strict mode ignores, so following it failed again.
class TestAStrictRunWithoutADatabase:
    @pytest.fixture
    def checkout(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        import scripts.validate_migrations as validate_migrations

        for name in list(os.environ):
            if name.startswith("POSTGRES_") or name in {"CI", "MIGRATIONS_ALLOW_SKIP"}:
                monkeypatch.delenv(name)
        monkeypatch.setenv("POSTGRES_ENABLED", "true")
        monkeypatch.setenv("MAYAK_REQUIRE_MIGRATION_DB", "1")
        monkeypatch.setattr(validate_migrations, "ROOT_DIR", tmp_path)
        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: False)
        (tmp_path / ".env").write_text("POSTGRES_USER=probe_user\nPOSTGRES_PASSWORD=probe_secret\n")
        return tmp_path

    @pytest.mark.unit
    @pytest.mark.parametrize("allow_skip", [None, "1"])
    def test_a_local_checkout_is_told_to_start_its_own_database(
        self,
        checkout: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        allow_skip: str | None,
    ) -> None:
        if allow_skip is not None:
            monkeypatch.setenv("MIGRATIONS_ALLOW_SKIP", allow_skip)

        exit_code = main()
        output = capsys.readouterr().out

        assert exit_code == 1
        assert "migrations.database_required" in output
        assert "make db-up-worktree" in output
        assert "--require-database" in output
        assert "MIGRATIONS_ALLOW_SKIP" not in output
        assert "Inspect the failing Alembic" not in output
        assert "probe_user" not in output and "probe_secret" not in output

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "environment",
        [
            {"POSTGRES_HOST": "db.invalid"},
            {"CI": "true", "MAYAK_REQUIRE_MIGRATION_DB": ""},
        ],
    )
    def test_an_explicit_target_is_not_sent_to_the_worktree_database(
        self,
        checkout: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        environment: dict[str, str],
    ) -> None:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)

        exit_code = main()
        output = capsys.readouterr().out

        assert exit_code == 1
        assert "The target is set explicitly" in output
        assert "make db-up-worktree" not in output

    @pytest.mark.unit
    def test_a_checkout_without_settings_is_told_to_create_them_first(
        self, checkout: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (checkout / ".env").unlink()

        exit_code = main()
        output = capsys.readouterr().out

        assert exit_code == 1
        assert 0 <= output.index("make init-project") < output.index("make db-up-worktree")

    @pytest.mark.unit
    def test_json_and_playbook_agree_with_the_printed_advice(
        self, checkout: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import json

        exit_code = main(["--json"])
        payload = json.loads(capsys.readouterr().out)
        issue = payload["issues"][0]
        playbook = get_migrations_rule_playbook("migrations.database_required")

        assert exit_code == 1
        assert payload["status"] == "error"
        assert (issue["rule_id"], issue["returncode"]) == ("migrations.database_required", 1)
        assert "\n" not in issue["message"] and "make db-up-worktree" in issue["message"]
        assert playbook is not None
        # doctor_ai_context prints likely_fix_shape, not suggested_fix; both name the move.
        assert "make db-up-worktree" in str(playbook["suggested_fix"])
        assert "make db-up-worktree" in str(playbook["likely_fix_shape"])
        assert playbook["smallest_command_to_rerun"] == (
            "uv run python scripts/validate_migrations.py --require-database"
        )
        assert "docker compose" not in " ".join(str(value) for value in playbook.values())


class TestEffectivePostgresPreflightAndReachability:
    @pytest.fixture
    def clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in list(os.environ):
            if name.startswith("POSTGRES_") or name in {
                "CI",
                "MIGRATIONS_ALLOW_SKIP",
                "MAYAK_REQUIRE_MIGRATION_DB",
            }:
                monkeypatch.delenv(name)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "env_vars",
        [
            {
                "POSTGRES_HOST": "127.0.0.1",
                "POSTGRES_PORT": "55432",
                "POSTGRES_USER": "env_user",
                "POSTGRES_PASSWORD": "env_password",
                "POSTGRES_DB": "env_db",
            },
            {
                "POSTGRES_HOST": "127.0.0.1",
                "POSTGRES_PORT": "55432",
            },
        ],
    )
    def test_env_only_target_without_dotenv_probes_effective_dsn_and_skips_ddl(
        self,
        clean_env: None,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        env_vars: dict[str, str],
    ) -> None:
        import psycopg
        from scripts.validate_migrations import collect_migration_precheck_issues

        for key, value in env_vars.items():
            monkeypatch.setenv(key, value)

        seen_dsns: list[str] = []

        class _DummyConn:
            def __enter__(self) -> "_DummyConn":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def fake_connect(dsn: str, connect_timeout: int = 3) -> _DummyConn:
            seen_dsns.append(dsn)
            return _DummyConn()

        def forbid_subprocess(*args: object, **kwargs: object) -> None:
            raise AssertionError("precheck must stay read-only and never run Alembic")

        monkeypatch.setattr(psycopg, "connect", fake_connect)
        monkeypatch.setattr(subprocess, "run", forbid_subprocess)

        issues = collect_migration_precheck_issues(tmp_path, require_database=True)

        assert issues == []
        assert len(seen_dsns) == 1
        assert "127.0.0.1:55432" in seen_dsns[0]

    @pytest.mark.unit
    def test_ci_without_dotenv_uses_listening_default_database_and_advises_ci_target_when_down(
        self,
        clean_env: None,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import psycopg
        from scripts.validate_migrations import collect_migration_precheck_issues

        monkeypatch.setenv("CI", "true")
        seen_dsns: list[str] = []

        class _DummyConn:
            def __enter__(self) -> "_DummyConn":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def connect_ok(dsn: str, connect_timeout: int = 3) -> _DummyConn:
            seen_dsns.append(dsn)
            return _DummyConn()

        monkeypatch.setattr(psycopg, "connect", connect_ok)
        assert collect_migration_precheck_issues(tmp_path, require_database=True) == []
        assert len(seen_dsns) == 1
        assert "localhost:5432" in seen_dsns[0]

        def connect_fail(dsn: str, connect_timeout: int = 3) -> _DummyConn:
            raise OSError("connection refused")

        monkeypatch.setattr(psycopg, "connect", connect_fail)
        issues = collect_migration_precheck_issues(tmp_path, require_database=True)

        assert len(issues) == 1
        assert issues[0].rule_id == "migrations.database_required"
        assert "The target is set explicitly" in issues[0].message
        assert "make db-up-worktree" not in issues[0].message
        assert "make init-project" not in issues[0].message

    @pytest.mark.unit
    def test_non_loopback_dotenv_host_is_reported_as_explicit_target(
        self,
        clean_env: None,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import psycopg
        from scripts.validate_migrations import collect_migration_precheck_issues

        (tmp_path / ".env").write_text(
            "POSTGRES_HOST=db.invalid\nPOSTGRES_USER=u\nPOSTGRES_PASSWORD=p\n",
            encoding="utf-8",
        )

        def connect_fail(dsn: str, connect_timeout: int = 3) -> object:
            raise OSError("name resolution failed")

        monkeypatch.setattr(psycopg, "connect", connect_fail)
        issues = collect_migration_precheck_issues(tmp_path, require_database=True)

        assert len(issues) == 1
        assert issues[0].rule_id == "migrations.database_required"
        assert "The target is set explicitly" in issues[0].message
        assert "make db-up-worktree" not in issues[0].message

    @pytest.mark.unit
    @pytest.mark.parametrize("kind", ["bad_marker", "bad_env_port"])
    def test_invalid_postgres_configuration_fails_cleanly_without_leaking_secrets(
        self,
        clean_env: None,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
    ) -> None:
        from scripts.validate_migrations import collect_migration_precheck_issues

        monkeypatch.setenv("POSTGRES_ENABLED", "true")
        if kind == "bad_marker":
            (tmp_path / ".env").write_text(
                "POSTGRES_USER=probe_user\nPOSTGRES_PASSWORD=super_secret_pw\n",
                encoding="utf-8",
            )
            (tmp_path / ".mayak-postgres-port").write_text("99999\n", encoding="utf-8")
        else:
            monkeypatch.setenv("POSTGRES_PORT", "not-a-port")
            monkeypatch.setenv("POSTGRES_PASSWORD", "super_secret_pw")

        issues = collect_migration_precheck_issues(tmp_path, require_database=True)

        assert len(issues) == 1
        assert issues[0].rule_id == "migrations.database_required"
        assert "super_secret_pw" not in issues[0].message
        assert "not-a-port" not in issues[0].message
        if kind == "bad_marker":
            assert "make db-up-worktree" in issues[0].message
        else:
            assert "The target is set explicitly" in issues[0].message
            assert "make db-up-worktree" not in issues[0].message

    @pytest.mark.unit
    def test_full_validator_catches_late_db_drop_and_runs_alembic_once_when_up(
        self,
        clean_env: None,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import scripts.validate_migrations as validate_migrations
        from scripts.validate_migrations import collect_migration_precheck_issues

        monkeypatch.setattr(validate_migrations, "ROOT_DIR", tmp_path)
        (tmp_path / ".env").write_text("POSTGRES_USER=u\nPOSTGRES_PASSWORD=p\n", encoding="utf-8")
        monkeypatch.setattr(validate_migrations, "_revision_heads", lambda: ["rev1"])

        states = iter([True, False])
        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: next(states))

        assert collect_migration_precheck_issues(tmp_path, require_database=True) == []
        late_issues = collect_migration_issues(tmp_path, require_database=True)
        assert len(late_issues) == 1
        assert late_issues[0].rule_id == "migrations.database_required"

        # When reachable throughout, full validator executes upgrade-head and check once each.
        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)
        monkeypatch.setattr(validate_migrations, "_stamped_revision_ids", lambda: set())
        ran: list[tuple[str, ...]] = []

        def fake_run(argv: tuple[str, ...], **kwargs: object) -> None:
            ran.append(tuple(argv))

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert collect_migration_issues(tmp_path, require_database=True) == []
        assert [cmd[-1] for cmd in ran] == ["head", "check"]
