# FILE: tests/application/test_trace_file_gate.py
# SUMMARY: Verify the NDJSON trace file is switched on by its own setting and never by debug mode.

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from project.core.config_settings_observability import ObservabilitySettings

# ATTRIBUTE: LAUNCHER_SOURCE (Path)
# SUMMARY: The module holding the gate. Read as text because the gate lives inside main()'s body
# and cannot be called without starting a server.
LAUNCHER_SOURCE = Path(__file__).resolve().parents[2] / "project" / "launcher" / "main.py"

# ATTRIBUTE: LOG_PATH_FACTORY (str)
# SUMMARY: The call that only ever runs behind the gate — used to find the gate in the syntax tree.
LOG_PATH_FACTORY = "create_run_log_path"

# ATTRIBUTE: GATE_ATTRIBUTE (str)
# SUMMARY: The settings attribute the gate must test.
GATE_ATTRIBUTE = "full_trace_enabled"


# FUNCTION: _guard_of_the_log_file_branch
# SUMMARY: Locate the `if` statement whose body creates the run log, and return its condition.
# OUTPUT: (ast.expr): The condition expression guarding log-file creation.
def _guard_of_the_log_file_branch() -> ast.expr:
    tree = ast.parse(LAUNCHER_SOURCE.read_text(encoding="utf-8"))
    guards = [
        node.test
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        for statement in node.body
        for inner in ast.walk(statement)
        if isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Name)
        and inner.func.id == LOG_PATH_FACTORY
    ]
    assert len(guards) == 1, (
        f"expected exactly one branch calling {LOG_PATH_FACTORY}, found {len(guards)}"
    )
    return guards[0]


# CLASS: tests.application.test_trace_file_gate.TestTheSettingItself
# SUMMARY: Verify the flag is off unless somebody asks for it.
class TestTheSettingItself:
    # FUNCTION: test_absent_environment_means_off
    # SUMMARY: Verify a deployment that says nothing gets no trace file.
    # **LOGIC_STEP**: `_env_file=None` alongside delenv. pydantic-settings reads the process
    # environment *and* the .env on disk, so clearing only the former left the developer's own
    # file in play — and this test went red on the machine of anyone who had actually switched
    # the trace on to look at something.
    @pytest.mark.unit
    def test_absent_environment_means_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ENABLE_FULL_TRACE", raising=False)

        settings = ObservabilitySettings(_env_file=None)  # type: ignore[call-arg]

        assert settings.full_trace_enabled is False

    # FUNCTION: test_the_environment_variable_turns_it_on
    # SUMMARY: Verify the documented variable is the one actually read.
    @pytest.mark.unit
    @pytest.mark.parametrize("raw", ["1", "true", "True", "yes", "on"])
    def test_the_environment_variable_turns_it_on(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        monkeypatch.setenv("ENABLE_FULL_TRACE", raw)

        assert ObservabilitySettings().full_trace_enabled is True


# CLASS: tests.application.test_trace_file_gate.TestTheGateInTheLauncher
# SUMMARY: Verify the branch that creates the log file is bound to the observability setting alone.
class TestTheGateInTheLauncher:
    # FUNCTION: test_the_log_file_branch_is_guarded_by_the_observability_setting
    # SUMMARY: Verify the guard reads full_trace_enabled.
    # **LOGIC_STEP**: Matched in the syntax tree rather than by substring. A substring search finds
    # the words anywhere in the module — including in the comment that explains the gate — so it
    # would stay green if the `if` itself were rewritten to test something else entirely.
    @pytest.mark.unit
    def test_the_log_file_branch_is_guarded_by_the_observability_setting(self) -> None:
        guard = _guard_of_the_log_file_branch()

        attributes = {node.attr for node in ast.walk(guard) if isinstance(node, ast.Attribute)}
        assert GATE_ATTRIBUTE in attributes

    # FUNCTION: test_debug_mode_does_not_open_the_trace_file
    # SUMMARY: Verify the two switches stay orthogonal — debug ergonomics must not start writing files.
    # **LOGIC_STEP**: The pairing is what the separation is for: an eval stand wants full traces
    # without reload and verbose stdout, and a developer wants those without a growing NDJSON file
    # on disk. Re-coupling them is the regression this catches.
    @pytest.mark.unit
    def test_debug_mode_does_not_open_the_trace_file(self) -> None:
        guard = _guard_of_the_log_file_branch()

        attributes = {node.attr for node in ast.walk(guard) if isinstance(node, ast.Attribute)}
        assert "debug" not in attributes
