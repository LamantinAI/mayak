# FILE: tests/application/test_validate_script_paths.py
# SUMMARY: Unit tests for the validate_script_paths harness validator.

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_script_paths import (
    _is_path_shaped,
    collect_script_path_issues,
    get_script_paths_rule_playbook,
)


# CLASS: tests.application.test_validate_script_paths.TestPathShape
# SUMMARY: Verify the path-shaped detection regex.
class TestPathShape:
    @pytest.mark.unit
    def test_recognizes_tests_path(self) -> None:
        assert _is_path_shaped("tests/application/test_x.py") is True

    @pytest.mark.unit
    def test_recognizes_project_subpath(self) -> None:
        assert _is_path_shaped("project/core/composition_root.py") is True

    @pytest.mark.unit
    def test_recognizes_dot_github_path(self) -> None:
        assert _is_path_shaped(".github/workflows/ci.yml") is True

    @pytest.mark.unit
    def test_rejects_non_repo_prefix(self) -> None:
        assert _is_path_shaped("/usr/local/bin/python") is False

    @pytest.mark.unit
    def test_rejects_url(self) -> None:
        assert _is_path_shaped("https://example.com/tests/foo") is False

    @pytest.mark.unit
    def test_rejects_empty_string(self) -> None:
        assert _is_path_shaped("") is False

    @pytest.mark.unit
    def test_rejects_allowlisted_prefix_literal(self) -> None:
        # `project/core/config` is a path PREFIX used by validate_runtime_ownership —
        # it is intentionally allowlisted because it is not a real file (the slash is
        # part of a startswith match, not a directory separator).
        assert _is_path_shaped("project/core/config") is False

    @pytest.mark.unit
    def test_real_domain_ports_is_path_shaped(self) -> None:
        # `project/domain/ports.py` was previously allowlisted as a forward-looking
        # reference. The docs-drift bundle ships it as a real file, so it should be
        # recognized as a path-shaped literal (and resolve cleanly on disk in the
        # happy-path test below).
        assert _is_path_shaped("project/domain/ports.py") is True


# CLASS: tests.application.test_validate_script_paths.TestHappyPath
# SUMMARY: Verify the live repo passes against its own scripts/.
class TestHappyPath:
    @pytest.mark.unit
    def test_real_scripts_pass(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        issues = collect_script_path_issues(repo_root)
        assert issues == [], f"Expected zero issues, got: {[i.message for i in issues]}"


# CLASS: tests.application.test_validate_script_paths.TestBrokenReferenceDetection
# SUMMARY: Synthetic scripts/ with a broken path-literal triggers an issue.
class TestBrokenReferenceDetection:
    @pytest.mark.unit
    def test_broken_reference_is_detected(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        bad_script = scripts_dir / "fake_validator.py"
        bad_script.write_text(
            'PATH = "tests/this_dir_does_not_exist/foo.py"\n',
            encoding="utf-8",
        )
        issues = collect_script_path_issues(tmp_path, scripts_dir=scripts_dir)
        assert len(issues) == 1
        assert issues[0].rule_id == "script_paths.broken_reference"
        assert issues[0].literal == "tests/this_dir_does_not_exist/foo.py"
        assert issues[0].severity == "error"

    @pytest.mark.unit
    def test_existing_path_is_not_flagged(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        # Create a real target so the literal resolves.
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "real.py").write_text("# placeholder", encoding="utf-8")
        good_script = scripts_dir / "fake_validator.py"
        good_script.write_text(
            'PATH = "tests/real.py"\n',
            encoding="utf-8",
        )
        issues = collect_script_path_issues(tmp_path, scripts_dir=scripts_dir)
        assert issues == []

    @pytest.mark.unit
    def test_non_path_string_is_ignored(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        bad_script = scripts_dir / "fake_validator.py"
        # URL and arbitrary message — neither should trigger.
        bad_script.write_text(
            'URL = "https://tests/example/foo"\nMSG = "Run pytest tests/integration to verify"\n',
            encoding="utf-8",
        )
        issues = collect_script_path_issues(tmp_path, scripts_dir=scripts_dir)
        # First literal is a URL (rejected by prefix check); second contains spaces.
        assert issues == []


# CLASS: tests.application.test_validate_script_paths.TestRulePlaybook
# SUMMARY: Tests for get_script_paths_rule_playbook helper.
class TestRulePlaybook:
    @pytest.mark.unit
    def test_known_rule_returns_dict(self) -> None:
        playbook = get_script_paths_rule_playbook("script_paths.broken_reference")
        assert isinstance(playbook, dict)
        assert "meaning" in playbook
        assert "next_checks" in playbook

    @pytest.mark.unit
    def test_unknown_rule_returns_none(self) -> None:
        assert get_script_paths_rule_playbook("script_paths.bogus") is None
