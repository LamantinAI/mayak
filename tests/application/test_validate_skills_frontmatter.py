# FILE: tests/application/test_validate_skills_frontmatter.py
# SUMMARY: Unit tests for the skills frontmatter validator.

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_skills_frontmatter import (
    _parse_frontmatter,
    _validate_file,
    get_skills_frontmatter_rule_playbook,
)


# CLASS: tests.application.test_validate_skills_frontmatter.TestParseFrontmatter
# SUMMARY: Verify YAML frontmatter parsing handles various formats correctly.
class TestParseFrontmatter:
    # FUNCTION: test_parse_inline_list
    # SUMMARY: Verify inline list syntax [a, b] is parsed correctly.
    @pytest.mark.unit
    def test_parse_inline_list(self) -> None:
        text = "---\ntriggers: [endpoint, api]\n---\n# Title"
        result = _parse_frontmatter(text)

        assert result is not None
        assert result["triggers"] == ["endpoint", "api"]

    # FUNCTION: test_parse_block_list
    # SUMMARY: Verify block list syntax with - items is parsed correctly.
    @pytest.mark.unit
    def test_parse_block_list(self) -> None:
        text = "---\nminimal_read_set:\n  - ARCHITECTURE.md\n  - project/core/config.py\n---\n"
        result = _parse_frontmatter(text)

        assert result is not None
        assert result["minimal_read_set"] == [
            "ARCHITECTURE.md",
            "project/core/config.py",
        ]

    # FUNCTION: test_parse_scalar_value
    # SUMMARY: Verify scalar key: value is parsed correctly.
    @pytest.mark.unit
    def test_parse_scalar_value(self) -> None:
        text = "---\nname: my-skill\ndescription: A test skill\n---\n"
        result = _parse_frontmatter(text)

        assert result is not None
        assert result["name"] == "my-skill"
        assert result["description"] == "A test skill"

    # FUNCTION: test_parse_no_frontmatter
    # SUMMARY: Verify None is returned when no frontmatter exists.
    @pytest.mark.unit
    def test_parse_no_frontmatter(self) -> None:
        text = "# Just a title\nSome content."
        result = _parse_frontmatter(text)

        assert result is None


# CLASS: tests.application.test_validate_skills_frontmatter.TestValidateFile
# SUMMARY: Verify file validation catches missing and invalid frontmatter fields.
class TestValidateFile:
    # FUNCTION: test_missing_frontmatter_reports_issue
    # SUMMARY: Verify files without frontmatter are flagged.
    @pytest.mark.unit
    def test_missing_frontmatter_reports_issue(self, tmp_path: Path) -> None:
        skill = tmp_path / "SKILL.md"
        skill.write_text("# No frontmatter\n")

        issues = _validate_file(skill, is_skill=True)

        assert len(issues) == 1
        assert issues[0].field == "frontmatter"

    # FUNCTION: test_missing_triggers_reports_issue
    # SUMMARY: Verify missing triggers field is flagged.
    @pytest.mark.unit
    def test_missing_triggers_reports_issue(self, tmp_path: Path) -> None:
        skill = tmp_path / "SKILL.md"
        skill.write_text("---\nname: test\ndescription: test\nvalidation_command: make test\n---\n")

        issues = _validate_file(skill, is_skill=True)

        assert any(i.field == "triggers" for i in issues)

    # FUNCTION: test_nonexistent_minimal_read_set_path_reports_issue
    # SUMMARY: Verify nonexistent paths in minimal_read_set are flagged.
    @pytest.mark.unit
    def test_nonexistent_minimal_read_set_path_reports_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("scripts.validate_skills_frontmatter.ROOT_DIR", tmp_path)
        skill = tmp_path / "SKILL.md"
        skill.write_text(
            "---\nname: test\ndescription: test\ntriggers: [test]\n"
            "minimal_read_set:\n  - nonexistent/file.py\nvalidation_command: make test\n---\n"
        )

        issues = _validate_file(skill, is_skill=True)

        assert any(i.field == "minimal_read_set" and "nonexistent" in i.message for i in issues)

    # FUNCTION: test_valid_skill_has_no_issues
    # SUMMARY: Verify a well-formed skill file produces no issues.
    @pytest.mark.unit
    def test_valid_skill_has_no_issues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("scripts.validate_skills_frontmatter.ROOT_DIR", tmp_path)
        (tmp_path / "ARCHITECTURE.md").write_text("# Arch\n")
        skill = tmp_path / "SKILL.md"
        skill.write_text(
            "---\nname: test\ndescription: A test skill\ntriggers: [test, example]\n"
            "minimal_read_set:\n  - ARCHITECTURE.md\nvalidation_command: make quality-gates\n---\n# Title\n"
        )

        issues = _validate_file(skill, is_skill=True)

        assert issues == []

    # FUNCTION: test_invalid_validation_command_reports_issue
    # SUMMARY: Verify validation_command with wrong prefix is flagged.
    @pytest.mark.unit
    def test_invalid_validation_command_reports_issue(self, tmp_path: Path) -> None:
        skill = tmp_path / "cmd.md"
        skill.write_text("---\ntriggers: [test]\nvalidation_command: echo hello\n---\n")

        issues = _validate_file(skill, is_skill=False)

        assert any(i.field == "validation_command" for i in issues)

    # FUNCTION: test_invalid_validation_command_carries_rule_id
    # SUMMARY: Verify the issue records the invalid_value rule_id.
    @pytest.mark.unit
    def test_invalid_validation_command_carries_rule_id(self, tmp_path: Path) -> None:
        skill = tmp_path / "cmd.md"
        skill.write_text("---\ntriggers: [test]\nvalidation_command: echo hello\n---\n")

        issues = _validate_file(skill, is_skill=False)

        assert any(i.rule_id == "skills_frontmatter.invalid_value" for i in issues)

    # FUNCTION: test_missing_field_carries_rule_id
    # SUMMARY: Verify a missing required field surfaces the missing_field rule_id.
    @pytest.mark.unit
    def test_missing_field_carries_rule_id(self, tmp_path: Path) -> None:
        skill = tmp_path / "SKILL.md"
        skill.write_text("---\ntriggers: [x]\n---\n")

        issues = _validate_file(skill, is_skill=True)

        assert any(i.rule_id == "skills_frontmatter.missing_field" for i in issues)

    # FUNCTION: test_missing_frontmatter_carries_rule_id
    # SUMMARY: Verify a file without frontmatter raises the missing_frontmatter rule_id.
    @pytest.mark.unit
    def test_missing_frontmatter_carries_rule_id(self, tmp_path: Path) -> None:
        skill = tmp_path / "SKILL.md"
        skill.write_text("# Body only, no frontmatter\n")

        issues = _validate_file(skill, is_skill=True)

        assert any(i.rule_id == "skills_frontmatter.missing_frontmatter" for i in issues)


# CLASS: tests.application.test_validate_skills_frontmatter.TestRulePlaybook
# SUMMARY: Tests for get_skills_frontmatter_rule_playbook helper.
class TestRulePlaybook:
    # FUNCTION: test_known_rule_returns_dict
    # SUMMARY: Known rule_id returns a dict with the expected keys.
    @pytest.mark.unit
    def test_known_rule_returns_dict(self) -> None:
        playbook = get_skills_frontmatter_rule_playbook("skills_frontmatter.missing_field")
        assert isinstance(playbook, dict)
        assert "meaning" in playbook

    # FUNCTION: test_unknown_rule_returns_none
    # SUMMARY: Unknown rule_id returns None.
    @pytest.mark.unit
    def test_unknown_rule_returns_none(self) -> None:
        assert get_skills_frontmatter_rule_playbook("skills_frontmatter.bogus") is None
