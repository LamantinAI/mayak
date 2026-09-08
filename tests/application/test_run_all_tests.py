# FILE: tests/application/test_run_all_tests.py
# SUMMARY: Unit tests for the canonical all-tests runner script used by AI agents and developers.

from pathlib import Path

import pytest

from scripts.run_all_tests import (
    COVERAGE_FLOOR_PERCENT,
    FUNCTIONAL_ENV_FILE,
    FUNCTIONAL_ENV_SAMPLE,
    ROOT_DIR,
    _build_test_steps,
    _ensure_functional_env,
    _parse_args,
    _parse_env_file,
    env_sample_drift,
)


# CLASS: tests.application.test_run_all_tests.TestRunAllTests
# SUMMARY: Verify CLI parsing, step planning, and functional env bootstrap behavior for the canonical test runner.
class TestRunAllTests:
    # FUNCTION: test_parse_args_defaults_to_full_suite
    # SUMMARY: Ensure the runner defaults to local plus functional suites.
    # OUTPUT: (None): None.
    def test_parse_args_defaults_to_full_suite(self) -> None:
        args = _parse_args([])

        assert args.skip_functional is False
        assert args.functional_only is False

    # FUNCTION: test_parse_args_supports_skip_functional
    # SUMMARY: Ensure the runner can skip functional tests for quick local loops.
    # OUTPUT: (None): None.
    def test_parse_args_supports_skip_functional(self) -> None:
        args = _parse_args(["--skip-functional"])

        assert args.skip_functional is True
        assert args.functional_only is False

    # FUNCTION: test_parse_args_supports_functional_only
    # SUMMARY: Ensure the runner can execute only the functional Docker-based suite.
    # OUTPUT: (None): None.
    def test_parse_args_supports_functional_only(self) -> None:
        args = _parse_args(["--functional-only"])

        assert args.skip_functional is False
        assert args.functional_only is True

    # FUNCTION: test_build_test_steps_defaults_to_local_and_functional
    # SUMMARY: Ensure the default plan includes both local and functional suites in order.
    # OUTPUT: (None): None.
    def test_build_test_steps_defaults_to_local_and_functional(self) -> None:
        steps = _build_test_steps(skip_functional=False, functional_only=False)

        assert [step.name for step in steps] == [
            "local-suites",
            "functional",
        ]
        assert steps[0].cwd == ROOT_DIR
        assert steps[1].cwd == ROOT_DIR / "tests" / "functional"

    # FUNCTION: test_build_test_steps_can_skip_functional
    # SUMMARY: Ensure quick local mode omits the Docker-based functional suite.
    # OUTPUT: (None): None.
    def test_build_test_steps_can_skip_functional(self) -> None:
        steps = _build_test_steps(skip_functional=True, functional_only=False)

        assert [step.name for step in steps] == ["local-suites"]

    # FUNCTION: test_local_step_runs_every_non_functional_suite
    # SUMMARY: A new suite directory under tests/ must reach the local step, not be silently skipped.
    # OUTPUT: (None): None.
    def test_local_step_runs_every_non_functional_suite(self) -> None:
        # **LOGIC_STEP**: tests/integration shipped for months without being listed here, so no
        # local gate ever ran it. Discovering the directories instead of repeating the list is
        # what makes the next added suite fail loudly rather than sit unexecuted.
        steps = _build_test_steps(skip_functional=True, functional_only=False)
        argv = steps[0].command

        suites = sorted(
            path.name
            for path in (ROOT_DIR / "tests").iterdir()
            if path.is_dir() and path.name != "functional" and any(path.glob("test_*.py"))
        )

        assert suites, "no suite directories found under tests/ — this discovery is broken"
        for suite in suites:
            assert f"tests/{suite}" in argv, f"tests/{suite} is never run by the local step"

    # FUNCTION: test_build_test_steps_can_run_only_functional
    # SUMMARY: Ensure functional-only mode omits the local unit and integration suite.
    # OUTPUT: (None): None.
    def test_build_test_steps_can_run_only_functional(self) -> None:
        steps = _build_test_steps(skip_functional=False, functional_only=True)

        assert [step.name for step in steps] == ["functional"]

    # FUNCTION: test_ensure_functional_env_copies_missing_env_file
    # SUMMARY: Ensure the runner bootstraps the functional env file from the sample when needed.
    # INPUT: monkeypatch (pytest.MonkeyPatch): Fixture used to redirect module-level paths to a temp directory.
    # INPUT: tmp_path (Path): Temporary directory used as an isolated functional test folder.
    # OUTPUT: (None): None.
    def test_ensure_functional_env_copies_missing_env_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        functional_dir = tmp_path / "functional"
        functional_dir.mkdir()
        sample = functional_dir / ".env.sample"
        sample.write_text("AGENT_LLM_MODE=mock\n", encoding="utf-8")
        env_file = functional_dir / ".env"

        monkeypatch.setattr("scripts.run_all_tests.FUNCTIONAL_ENV_SAMPLE", sample)
        monkeypatch.setattr("scripts.run_all_tests.FUNCTIONAL_ENV_FILE", env_file)

        _ensure_functional_env()

        assert env_file.read_text(encoding="utf-8") == sample.read_text(encoding="utf-8")

    # FUNCTION: test_env_sample_drift_reports_a_changed_default_and_a_new_key
    # SUMMARY: Ensure both shapes of sample drift are named: a value that moved, a key that appeared.
    # OUTPUT: (None): None.
    def test_env_sample_drift_reports_a_changed_default_and_a_new_key(self) -> None:
        sample = {"AGENT_LLM_MODE": "mock", "APP_PORT": "8000", "NEW_KEY": "value"}
        current = {"AGENT_LLM_MODE": "live", "APP_PORT": "8000"}

        assert env_sample_drift(sample, current) == ["AGENT_LLM_MODE", "NEW_KEY"]

    # FUNCTION: test_env_sample_drift_ignores_a_local_only_key
    # SUMMARY: Ensure a key the local file adds on its own is treated as an override, not as drift.
    # OUTPUT: (None): None.
    def test_env_sample_drift_ignores_a_local_only_key(self) -> None:
        sample = {"AGENT_LLM_MODE": "mock"}
        current = {"AGENT_LLM_MODE": "mock", "POSTGRES_PORT": "15432"}

        assert env_sample_drift(sample, current) == []

    # FUNCTION: test_ensure_functional_env_warns_when_an_existing_env_file_is_stale
    # SUMMARY: Ensure the drift is named before Docker starts, not discovered inside a container.
    # INPUT: monkeypatch (pytest.MonkeyPatch): Fixture used to redirect module-level paths.
    # INPUT: tmp_path (Path): Temporary directory used as an isolated functional test folder.
    # INPUT: capsys (pytest.CaptureFixture[str]): Fixture capturing the runner's progress lines.
    # OUTPUT: (None): None.
    # **LOGIC_STEP**: The bootstrap copies the sample once and then returns early forever after.
    # Without this test the comparison could exist and never be called, which is the state this
    # runner was in: a sample that gained a key left the local file answering with the old one.
    def test_ensure_functional_env_warns_when_an_existing_env_file_is_stale(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        functional_dir = tmp_path / "functional"
        functional_dir.mkdir()
        sample = functional_dir / ".env.sample"
        sample.write_text("AGENT_LLM_MODE=mock\nNEW_KEY=value\n", encoding="utf-8")
        env_file = functional_dir / ".env"
        env_file.write_text("AGENT_LLM_MODE=live\n", encoding="utf-8")

        monkeypatch.setattr("scripts.run_all_tests.FUNCTIONAL_ENV_SAMPLE", sample)
        monkeypatch.setattr("scripts.run_all_tests.FUNCTIONAL_ENV_FILE", env_file)

        _ensure_functional_env()

        warning = capsys.readouterr().out
        assert "AGENT_LLM_MODE" in warning
        assert "NEW_KEY" in warning
        # **LOGIC_STEP**: A warning, not a failure — and the file it warns about is left alone.
        assert env_file.read_text(encoding="utf-8") == "AGENT_LLM_MODE=live\n"

    # FUNCTION: test_parse_env_file_reads_the_conventions_a_local_file_actually_uses
    # SUMMARY: Ensure `export`, inline comments, quotes and values containing `=` all read right.
    # INPUT: tmp_path (Path): Temporary directory holding the env file under test.
    # OUTPUT: (None): None.
    # **LOGIC_STEP**: Each line here is a shape that, read naively, reports drift on a file that
    # matches the sample: the key would be "export FOO", the value would carry its own comment,
    # and a URL would be cut at its first `=`.
    def test_parse_env_file_reads_the_conventions_a_local_file_actually_uses(
        self, tmp_path: Path
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "export AGENT_LLM_MODE=mock\n"
            "APP_PORT=8000  # the port the compose stack publishes\n"
            'POSTGRES_PASSWORD="pa#ss"\n'
            "DATABASE_URL=postgres://user:pw@db:5432/app?sslmode=disable\n",
            encoding="utf-8",
        )

        assert _parse_env_file(env_file) == {
            "AGENT_LLM_MODE": "mock",
            "APP_PORT": "8000",
            "POSTGRES_PASSWORD": "pa#ss",
            "DATABASE_URL": "postgres://user:pw@db:5432/app?sslmode=disable",
        }

    # FUNCTION: test_an_exported_key_is_not_reported_as_drift
    # SUMMARY: Ensure a local file written for `source` matches a sample written without `export`.
    # INPUT: tmp_path (Path): Temporary directory holding both files.
    # OUTPUT: (None): None.
    def test_an_exported_key_is_not_reported_as_drift(self, tmp_path: Path) -> None:
        sample = tmp_path / ".env.sample"
        sample.write_text("AGENT_LLM_MODE=mock\n", encoding="utf-8")
        local = tmp_path / ".env"
        local.write_text("export AGENT_LLM_MODE=mock\n", encoding="utf-8")

        assert env_sample_drift(_parse_env_file(sample), _parse_env_file(local)) == []

    # FUNCTION: test_functional_env_paths_point_to_repository_suite
    # SUMMARY: Ensure exported path constants target the repository functional suite by default.
    # OUTPUT: (None): None.
    def test_functional_env_paths_point_to_repository_suite(self) -> None:
        assert FUNCTIONAL_ENV_SAMPLE == ROOT_DIR / "tests" / "functional" / ".env.sample"
        assert FUNCTIONAL_ENV_FILE == ROOT_DIR / "tests" / "functional" / ".env"


# CLASS: tests.application.test_run_all_tests.TestCoverageFloorAppliesToTheFullRunOnly
# SUMMARY: Verify the total-coverage floor is asked for by the full run and by nothing else.
# NOTE: The floor used to sit in pytest.ini's `addopts`, which pytest applies to every invocation.
# `uv run pytest tests/one_file.py` reported every test passing and then exited 1 on total
# coverage, because three tests cannot cover project/. A red exit that says nothing about the
# tests that ran teaches the reader to stop reading red exits, so the floor now belongs to the one
# run that can honestly carry it.
class TestCoverageFloorAppliesToTheFullRunOnly:
    # FUNCTION: test_pytest_ini_does_not_impose_a_total_on_every_invocation
    # SUMMARY: Verify no coverage floor reaches a narrow run through pytest.ini's addopts.
    # OUTPUT: (None): None.
    @pytest.mark.unit
    def test_pytest_ini_does_not_impose_a_total_on_every_invocation(self) -> None:
        addopts = [
            line
            for line in (ROOT_DIR / "pytest.ini").read_text(encoding="utf-8").splitlines()
            if line.startswith("addopts")
        ]

        assert addopts, "pytest.ini has no addopts line — the assertion below would pass vacuously"
        assert "--cov-fail-under" not in addopts[0]

    # FUNCTION: test_the_full_local_run_asks_for_the_floor_itself
    # SUMMARY: Verify the local-suites step carries the floor the addopts line no longer does.
    # OUTPUT: (None): None.
    @pytest.mark.unit
    def test_the_full_local_run_asks_for_the_floor_itself(self) -> None:
        local = next(
            step for step in _build_test_steps(skip_functional=True, functional_only=False)
        )

        # **LOGIC_STEP**: The number is written out here rather than interpolated from the constant
        # the command is built from. Interpolating it moves both sides together — lowering the
        # floor to 0 would have kept this green while switching the protection off, which is the
        # same tautology `test.sql_constant_round_trip` exists to forbid one directory away.
        assert "--cov-fail-under=60" in local.command
        assert COVERAGE_FLOOR_PERCENT == 60
