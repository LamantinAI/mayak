# FILE: tests/application/test_validate_migrations.py
# SUMMARY: Unit tests for the Alembic migration validation quality gate.

import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest

from project.infrastructure.persistence.orm_models import Base
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
        assert "MIGRATIONS NOT VERIFIED" in output

    # FUNCTION: test_not_verified_banner_appears_only_when_the_gate_actually_skipped
    # SUMMARY: The banner marks an unverified skip and only an unverified skip — never a real pass.
    # **LOGIC_STEP**: This is the trap for a real finding: the old skip message read
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
        # **LOGIC_STEP**: The commands are recorded, not merely swallowed. Asserting only the exit
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
        # **LOGIC_STEP**: Reachable is forced, so the stamp reader would otherwise open a real
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
        # **LOGIC_STEP**: Reachable is forced, so the stamp reader would otherwise open a real
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
        # **LOGIC_STEP**: Reachable is forced, so the stamp reader would otherwise open a real
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
        # **LOGIC_STEP**: Reachable is forced, so the stamp reader would otherwise open a real
        # connection to whatever .env names — a unit test reaching a developer's database.
        monkeypatch.setattr(validate_migrations, "_stamped_revision_ids", lambda: set())
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
        # **LOGIC_STEP**: Reachable is forced, so the stamp reader would otherwise open a real
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


# FUNCTION: _script_directory
# SUMMARY: Write a throwaway Alembic script directory whose revisions form the given graph.
# INPUT: chain (dict[str, str | None]): revision id → down_revision, in any order.
# OUTPUT: (Path): The script directory to hand to _revision_heads.
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


# CLASS: tests.application.test_validate_migrations.TestRevisionGraphIsCheckedWithoutADatabase
# SUMMARY: Verify a fork or a dangling down_revision turns the gate red before the database skip.
# NOTE: The comment on database_skip_is_allowed names "a second alembic head or a dangling
# down_revision" as what the CI backstop exists for — and both used to pass a checkout
# without Postgres green, because every alembic command sat behind the reachability check. Neither
# defect needs a database to see: ScriptDirectory reads the files. Measured: two revisions sharing
# a down_revision, `make quality-gates` exit 0, "migration validation skipped".
class TestRevisionGraphIsCheckedWithoutADatabase:
    # FUNCTION: test_the_shipped_revisions_form_one_chain
    # SUMMARY: Verify the repository's own alembic/versions/ has exactly one head, read offline.
    @pytest.mark.unit
    def test_the_shipped_revisions_form_one_chain(self) -> None:
        # **LOGIC_STEP**: The real directory, no monkeypatch: this is the read every local gate
        # now makes, and it must work with no .env and no database in the environment.
        assert len(_revision_heads()) == 1

    # FUNCTION: test_a_project_without_a_script_directory_has_no_graph_to_check
    # SUMMARY: Verify a deleted alembic/ reads as "no revisions", so the database_disabled skip still follows.
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

    # FUNCTION: test_a_fork_fails_the_gate_where_it_used_to_skip
    # SUMMARY: Verify two heads make main() exit 1 with the database unreachable and CI unset.
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

    # FUNCTION: test_a_fork_is_reported_even_where_postgres_is_disabled
    # SUMMARY: Verify POSTGRES_ENABLED=false does not hide a fork — the files are wrong either way.
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

    # FUNCTION: test_a_dangling_down_revision_is_reported
    # SUMMARY: Verify a down_revision naming no revision is an error, not an alembic traceback.
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

        # **LOGIC_STEP**: Alembic warns before it raises; the warning is part of the behaviour
        # under test, not noise to let through to the suite's summary.
        with pytest.warns(UserWarning, match="is not present"):
            issues = collect_migration_issues(ROOT_DIR)

        assert [issue.rule_id for issue in issues] == ["migrations.broken_revision_graph"]
        assert "nope" in issues[0].message

    # FUNCTION: test_one_chain_still_reaches_the_database_skip
    # SUMMARY: Verify a healthy graph changes nothing: the unreachable-database skip follows as before.
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

    # FUNCTION: test_each_offline_rule_has_a_playbook
    # SUMMARY: Verify both new rule ids resolve to a playbook that points at the revision files.
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


# CLASS: tests.application.test_validate_migrations.TestADatabaseAheadOfThisBranch
# SUMMARY: Verify a database stamped with a revision this branch lacks is named as such, before
# alembic is asked a question it can only answer opaquely.
# NOTE: Measured in a project built from this template: fifteen worktrees against one
# PostgreSQL container. An agent on one branch ran `alembic upgrade head`; a worktree on a branch
# without that revision then met `Can't locate revision identified by 'af0035d05498'` — reproduced
# verbatim — which arrived as migrations.upgrade_failure, whose playbook offered to reset the
# database. On a database more than one checkout can reach, that drops the schema another branch
# migrated, and it does not fix this at all: the stamp comes back the moment the other branch runs.
class TestADatabaseAheadOfThisBranch:
    # FUNCTION: test_a_stamped_revision_this_branch_lacks_fails_the_gate
    # SUMMARY: Verify the case is reported without alembic ever being invoked.
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

    # FUNCTION: test_a_stamp_this_branch_does_have_is_quiet
    # SUMMARY: Verify the ordinary case still reaches alembic, in the order it always ran in.
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

    # FUNCTION: test_a_database_nobody_has_migrated_yet_is_quiet
    # SUMMARY: Verify an empty alembic_version reads as "fresh", not as a foreign revision.
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

    # FUNCTION: test_no_migration_playbook_offers_to_recreate_the_database
    # SUMMARY: Verify the advice this rule exists to replace is gone from every playbook here.
    # **LOGIC_STEP**: The whole rule is an argument against one sentence. Reintroducing it
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

    # FUNCTION: test_the_playbook_names_the_way_out
    # SUMMARY: Verify the new playbook offers catching up and a database of one's own.
    @pytest.mark.unit
    def test_the_playbook_names_the_way_out(self) -> None:
        playbook = get_migrations_rule_playbook("migrations.foreign_revision")

        assert playbook is not None
        assert "make db-up-worktree" in str(playbook["suggested_fix"])
        assert "git fetch" in str(playbook["suggested_fix"])
