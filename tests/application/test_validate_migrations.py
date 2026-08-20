# FILE: tests/application/test_validate_migrations.py
# SUMMARY: Unit tests for the Alembic migration validation quality gate.

import subprocess
import sys
from pathlib import Path

import pytest

from project.infrastructure.persistence.orm_models import Base
from scripts.validate_migrations import (
    ALEMBIC_INI_PATH,
    ROOT_DIR,
    _build_commands,
    collect_migration_issues,
    get_migrations_rule_playbook,
    main,
)


# CLASS: tests.application.test_validate_migrations.TestValidateMigrations
# SUMMARY: Verify the migration quality gate executes Alembic upgrade and metadata checks in order.
class TestValidateMigrations:
    # FUNCTION: test_metadata_contains_tables_expected_by_migrations
    # SUMMARY: Ensure the ORM's table registry is exactly the one the migrations create.
    # NOTE: This pair names the shipped reference_tasks table, so deleting the reference vertical
    # turns both red — by design, and .agents/skills/add-vertical's removal list points here. Update the
    # ledger below in the same commit as the ORM model; that is the whole job.
    @pytest.mark.unit
    def test_metadata_contains_tables_expected_by_migrations(self) -> None:
        # **LOGIC_STEP**: Exact equality, not issubset. A subset check stays green when a vertical
        # adds a table and forgets its migration — the one thing this assertion exists to catch.
        assert set(Base.metadata.tables) == {"reference_tasks"}

    # FUNCTION: test_metadata_contains_reference_task_index
    # SUMMARY: Ensure metadata includes exactly the reference task indexes the migrations create.
    @pytest.mark.unit
    def test_metadata_contains_reference_task_index(self) -> None:
        reference_tasks = Base.metadata.tables["reference_tasks"]
        index_names = {index.name for index in reference_tasks.indexes}

        assert index_names == {"ix_reference_tasks_status_created_at"}

    # FUNCTION: test_build_commands_returns_upgrade_then_check
    # SUMMARY: Ensure the quality gate plans both Alembic steps with the repository config file.
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

    # FUNCTION: test_main_skips_when_database_unreachable
    # SUMMARY: Ensure the quality gate returns success without running commands when the database is unreachable.
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
        # **LOGIC_STEP**: Pin the toggle so this test keeps exercising the unreachable path even
        # in a checkout whose .env declares no relational store — otherwise it would silently
        # start asserting the migrations.database_disabled branch instead.
        monkeypatch.setenv("POSTGRES_ENABLED", "true")

        exit_code = main()
        output = capsys.readouterr().out

        assert exit_code == 0
        assert "Database is not reachable" in output

    # FUNCTION: test_main_fails_when_database_unreachable_in_ci
    # SUMMARY: Ensure CI cannot silently skip the gate, which is how broken revisions reached main.
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
        assert "requires it" in output

    # FUNCTION: test_ci_skip_can_be_opted_back_in
    # SUMMARY: Ensure a deliberate exception remains available without editing the validator.
    @pytest.mark.unit
    def test_ci_skip_can_be_opted_back_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: False)
        monkeypatch.setenv("CI", "true")
        monkeypatch.setenv("MIGRATIONS_ALLOW_SKIP", "1")
        monkeypatch.setenv("POSTGRES_ENABLED", "true")

        assert main() == 0

    # FUNCTION: test_require_database_flag_fails_without_ci
    # SUMMARY: Ensure the explicit flag makes the gate strict outside CI too.
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

    # FUNCTION: test_ci_workflow_provisions_postgres_for_quality_gates
    # SUMMARY: Ensure the job that runs the gate actually has a database, so the strict mode can pass.
    @pytest.mark.unit
    def test_ci_workflow_provisions_postgres_for_quality_gates(self) -> None:
        workflow = (ROOT_DIR / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        quality_gates_job = workflow.split("  quality-gates:", 1)[1].split("\n  secret-scan:", 1)[0]

        assert "services:" in quality_gates_job
        assert "postgres:" in quality_gates_job
        assert "POSTGRES_HOST:" in quality_gates_job

    # FUNCTION: test_main_runs_upgrade_and_check
    # SUMMARY: Ensure the quality gate executes both commands in order and reports success.
    @pytest.mark.unit
    def test_main_runs_upgrade_and_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)

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

    # FUNCTION: test_main_returns_subprocess_exit_code
    # SUMMARY: Ensure Alembic check failures surface their exit status and a migration remediation hint.
    @pytest.mark.unit
    def test_main_returns_subprocess_exit_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)

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

    # FUNCTION: test_main_reports_upgrade_head_remediation
    # SUMMARY: Ensure Alembic upgrade failures point the caller at migration-chain or database-state remediation.
    @pytest.mark.unit
    def test_main_reports_upgrade_head_remediation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)

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


# CLASS: tests.application.test_validate_migrations.TestRulePlaybook
# SUMMARY: Tests for migration rule_id playbooks and structured issue collection.
class TestRulePlaybook:
    # FUNCTION: test_known_rule_returns_dict
    # SUMMARY: Known rule_id returns a dict with the expected keys.
    @pytest.mark.unit
    def test_known_rule_returns_dict(self) -> None:
        playbook = get_migrations_rule_playbook("migrations.head_drift")
        assert isinstance(playbook, dict)
        assert "meaning" in playbook
        assert "next_checks" in playbook

    # FUNCTION: test_unknown_rule_returns_none
    # SUMMARY: Unknown rule_id returns None.
    @pytest.mark.unit
    def test_unknown_rule_returns_none(self) -> None:
        assert get_migrations_rule_playbook("migrations.bogus") is None

    # FUNCTION: test_unreachable_db_emits_skip_issue
    # SUMMARY: When DB is unreachable, collect_migration_issues returns one informational issue.
    @pytest.mark.unit
    def test_unreachable_db_emits_skip_issue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: False)
        # **LOGIC_STEP**: This test is about the "unreachable" branch specifically, so it must
        # pin both switches that route around it: CI turns the skip into a hard failure, and
        # POSTGRES_ENABLED=false diverts to the "not used by this project" branch entirely.
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("POSTGRES_ENABLED", "true")
        issues = collect_migration_issues(ROOT_DIR)
        assert len(issues) == 1
        assert issues[0].rule_id == "migrations.database_unreachable"
        assert issues[0].returncode == 0  # informational, not a hard failure

    # FUNCTION: test_check_failure_emits_head_drift
    # SUMMARY: A failing `alembic check` step is reported as migrations.head_drift rule_id.
    @pytest.mark.unit
    def test_check_failure_emits_head_drift(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)
        # **LOGIC_STEP**: Make upgrade-head succeed and `check` fail.
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

    # FUNCTION: test_check_failure_captures_alembic_stderr_into_issue
    # SUMMARY: Regression guard: when Alembic fails with a real stderr (e.g. ProgrammingError), MigrationIssue.stderr must contain the captured output and message must hint at it. Closes the bug where the JSON consumer only saw "check failed with exit code N".
    @pytest.mark.unit
    def test_check_failure_captures_alembic_stderr_into_issue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.validate_migrations as validate_migrations

        monkeypatch.setattr(validate_migrations, "_is_database_reachable", lambda: True)
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
