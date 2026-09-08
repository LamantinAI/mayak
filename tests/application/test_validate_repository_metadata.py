# FILE: tests/application/test_validate_repository_metadata.py
# SUMMARY: Unit tests for the merged repository-metadata validator (skills/commands frontmatter,
# scripts/*.py path-shaped literals, docs/project_context.json schema + cross-references).
# NOTE: Merged 2026-09 (audit item P9) from test_validate_skills_frontmatter.py,
# test_validate_script_paths.py and test_validate_project_context.py, alongside the source merge in
# scripts/validate_repository_metadata.py. TestAStatusIsCheckedAgainstTheWiring and
# test_the_new_rule_has_a_playbook are dropped, not carried over: they tested
# project_context.vertical_status_contradicts_wiring, which the merge removed on purpose —
# scripts/validate_endpoint_wiring.py is the validator that actually checks wiring.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.validate_repository_metadata import (
    TEMPLATE_DOMAIN,
    TEMPLATE_PROJECT_NAME,
    _is_path_shaped,
    _parse_frontmatter,
    _validate_file,
    collect_project_context_issues,
    collect_script_path_issues,
    get_repository_metadata_rule_playbook,
)


# =========================================================================================
# Skills / commands frontmatter
# =========================================================================================


class TestParseFrontmatter:
    @pytest.mark.unit
    def test_parse_inline_list(self) -> None:
        result = _parse_frontmatter("---\ntriggers: [endpoint, api]\n---\n# Title")
        assert result is not None
        assert result["triggers"] == ["endpoint", "api"]

    @pytest.mark.unit
    def test_parse_block_list(self) -> None:
        text = "---\nminimal_read_set:\n  - ARCHITECTURE.md\n  - project/core/config.py\n---\n"
        result = _parse_frontmatter(text)
        assert result is not None
        assert result["minimal_read_set"] == ["ARCHITECTURE.md", "project/core/config.py"]

    @pytest.mark.unit
    def test_parse_scalar_value(self) -> None:
        result = _parse_frontmatter("---\nname: my-skill\ndescription: A test skill\n---\n")
        assert result is not None
        assert result["name"] == "my-skill"
        assert result["description"] == "A test skill"

    @pytest.mark.unit
    def test_parse_no_frontmatter(self) -> None:
        assert _parse_frontmatter("# Just a title\nSome content.") is None


class TestValidateFile:
    @pytest.mark.unit
    def test_missing_frontmatter_reports_issue(self, tmp_path: Path) -> None:
        skill = tmp_path / "SKILL.md"
        skill.write_text("# No frontmatter\n")
        issues = _validate_file(skill, is_skill=True)
        assert len(issues) == 1
        assert issues[0].field == "frontmatter"

    @pytest.mark.unit
    def test_missing_triggers_reports_issue(self, tmp_path: Path) -> None:
        skill = tmp_path / "SKILL.md"
        skill.write_text("---\nname: test\ndescription: test\nvalidation_command: make test\n---\n")
        issues = _validate_file(skill, is_skill=True)
        assert any(i.field == "triggers" for i in issues)

    @pytest.mark.unit
    def test_nonexistent_minimal_read_set_path_reports_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("scripts.validate_repository_metadata.ROOT_DIR", tmp_path)
        skill = tmp_path / "SKILL.md"
        skill.write_text(
            "---\nname: test\ndescription: test\ntriggers: [test]\n"
            "minimal_read_set:\n  - nonexistent/file.py\nvalidation_command: make test\n---\n"
        )
        issues = _validate_file(skill, is_skill=True)
        assert any(i.field == "minimal_read_set" and "nonexistent" in i.message for i in issues)

    @pytest.mark.unit
    def test_valid_skill_has_no_issues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("scripts.validate_repository_metadata.ROOT_DIR", tmp_path)
        (tmp_path / "ARCHITECTURE.md").write_text("# Arch\n")
        skill = tmp_path / "SKILL.md"
        skill.write_text(
            "---\nname: test\ndescription: A test skill\ntriggers: [test, example]\n"
            "minimal_read_set:\n  - ARCHITECTURE.md\nvalidation_command: make quality-gates\n---\n# Title\n"
        )
        assert _validate_file(skill, is_skill=True) == []

    @pytest.mark.unit
    def test_invalid_validation_command_reports_issue(self, tmp_path: Path) -> None:
        skill = tmp_path / "cmd.md"
        skill.write_text("---\ntriggers: [test]\nvalidation_command: echo hello\n---\n")
        issues = _validate_file(skill, is_skill=False)
        assert any(i.field == "validation_command" for i in issues)

    @pytest.mark.unit
    def test_invalid_validation_command_carries_rule_id(self, tmp_path: Path) -> None:
        skill = tmp_path / "cmd.md"
        skill.write_text("---\ntriggers: [test]\nvalidation_command: echo hello\n---\n")
        issues = _validate_file(skill, is_skill=False)
        assert any(i.rule_id == "skills_frontmatter.invalid_value" for i in issues)

    @pytest.mark.unit
    def test_missing_field_carries_rule_id(self, tmp_path: Path) -> None:
        skill = tmp_path / "SKILL.md"
        skill.write_text("---\ntriggers: [x]\n---\n")
        issues = _validate_file(skill, is_skill=True)
        assert any(i.rule_id == "skills_frontmatter.missing_field" for i in issues)

    @pytest.mark.unit
    def test_missing_frontmatter_carries_rule_id(self, tmp_path: Path) -> None:
        skill = tmp_path / "SKILL.md"
        skill.write_text("# Body only, no frontmatter\n")
        issues = _validate_file(skill, is_skill=True)
        assert any(i.rule_id == "skills_frontmatter.missing_frontmatter" for i in issues)


class TestSkillsFrontmatterRulePlaybook:
    @pytest.mark.unit
    def test_known_rule_returns_dict(self) -> None:
        playbook = get_repository_metadata_rule_playbook("skills_frontmatter.missing_field")
        assert isinstance(playbook, dict)
        assert "meaning" in playbook

    @pytest.mark.unit
    def test_unknown_rule_returns_none(self) -> None:
        assert get_repository_metadata_rule_playbook("skills_frontmatter.bogus") is None


# =========================================================================================
# Path-shaped literals inside scripts/*.py
# =========================================================================================


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
        # `project/core/config` is a path PREFIX used by validate_runtime_ownership — allowlisted
        # because it is not a real file (the slash is part of a startswith match).
        assert _is_path_shaped("project/core/config") is False

    @pytest.mark.unit
    def test_real_domain_ports_is_path_shaped(self) -> None:
        assert _is_path_shaped("project/domain/ports.py") is True


class TestScriptPathsHappyPath:
    @pytest.mark.unit
    def test_real_scripts_pass(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        issues = collect_script_path_issues(repo_root)
        assert issues == [], f"Expected zero issues, got: {[i.message for i in issues]}"


class TestBrokenReferenceDetection:
    @pytest.mark.unit
    def test_broken_reference_is_detected(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "fake_validator.py").write_text(
            'PATH = "tests/this_dir_does_not_exist/foo.py"\n', encoding="utf-8"
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
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "real.py").write_text("# placeholder", encoding="utf-8")
        (scripts_dir / "fake_validator.py").write_text('PATH = "tests/real.py"\n', encoding="utf-8")
        assert collect_script_path_issues(tmp_path, scripts_dir=scripts_dir) == []

    @pytest.mark.unit
    def test_non_path_string_is_ignored(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "fake_validator.py").write_text(
            'URL = "https://tests/example/foo"\nMSG = "Run pytest tests/integration to verify"\n',
            encoding="utf-8",
        )
        # First literal is a URL (rejected by prefix check); second contains spaces.
        assert collect_script_path_issues(tmp_path, scripts_dir=scripts_dir) == []


class TestScriptPathsRulePlaybook:
    @pytest.mark.unit
    def test_known_rule_returns_dict(self) -> None:
        playbook = get_repository_metadata_rule_playbook("script_paths.broken_reference")
        assert isinstance(playbook, dict)
        assert "meaning" in playbook
        assert "next_checks" in playbook

    @pytest.mark.unit
    def test_unknown_rule_returns_none(self) -> None:
        assert get_repository_metadata_rule_playbook("script_paths.bogus") is None


# =========================================================================================
# docs/project_context.json schema + cross-references
# =========================================================================================


def _write_context(tmp_path: Path, data: dict[str, Any]) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / "project_context.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _valid_skeleton() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project_name": "Test Project",
        "domain": "Test domain",
        "is_template": False,
        "verticals": {
            "orders": {
                "description": "Order management",
                "status": "active",
                "domain_entities": ["Order"],
                "api_prefix": "/orders",
                "business_rules": ["BR-001"],
                "notes": "",
            }
        },
        "integrations": {},
        "business_rules": {
            "BR-001": {
                "summary": "Orders must have at least one item",
                "vertical": "orders",
                "enforcement": "application_service",
            }
        },
        "glossary": {"order": "A customer purchase request"},
        "api_overview": {"base_path": "/api/v1", "auth_strategy": "jwt", "notes": ""},
        "project_decisions": [],
    }


class TestProjectContextValidation:
    @pytest.mark.unit
    def test_valid_skeleton_passes(self, tmp_path: Path) -> None:
        _write_context(tmp_path, _valid_skeleton())
        assert collect_project_context_issues(tmp_path) == []

    @pytest.mark.unit
    @pytest.mark.parametrize("field", ["project_name", "domain"])
    def test_template_identity_left_behind_is_reported(self, tmp_path: Path, field: str) -> None:
        data = _valid_skeleton()
        data[field] = TEMPLATE_PROJECT_NAME if field == "project_name" else TEMPLATE_DOMAIN
        _write_context(tmp_path, data)

        issues = collect_project_context_issues(tmp_path)

        assert [issue.rule_id for issue in issues] == [
            "project_context.template_identity_not_replaced"
        ]
        assert issues[0].field == field

    # NOTE: The rule used to ask whether the domain CONTAINED "mayak", so a sentence written
    # deliberately for this service — one that merely names the kernel it is built on — failed a
    # rule about forgetting to write a domain at all. Equality against the shipped text is the
    # actual subject.
    @pytest.mark.unit
    def test_a_domain_that_merely_names_the_template_is_not_an_unreplaced_identity(
        self, tmp_path: Path
    ) -> None:
        data = _valid_skeleton()
        data["domain"] = "Retrieval service for engineering documents, built on the Mayak kernel."
        _write_context(tmp_path, data)
        assert collect_project_context_issues(tmp_path) == []

    # NOTE: Without this, editing docs/project_context.json's domain in the template silently
    # disarms the rule for every project created afterwards.
    @pytest.mark.unit
    def test_template_domain_constant_matches_the_shipped_file(self) -> None:
        shipped = json.loads(
            (Path(__file__).parents[2] / "docs" / "project_context.json").read_text(
                encoding="utf-8"
            )
        )
        if not shipped.get("is_template", False):
            pytest.skip("this repository is a project built from the template, not the template")
        assert " ".join(str(shipped["domain"]).split()) == TEMPLATE_DOMAIN
        assert shipped["project_name"] == TEMPLATE_PROJECT_NAME

    # NOTE: Regression guard — `is_template` was briefly required, which turned every existing
    # project on this kernel red the moment it pulled the update, with no migration path.
    @pytest.mark.unit
    def test_context_without_the_flag_still_passes(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        del data["is_template"]
        _write_context(tmp_path, data)
        assert collect_project_context_issues(tmp_path) == []

    @pytest.mark.unit
    def test_template_itself_keeps_its_own_identity(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["is_template"] = True
        data["project_name"] = "Mayak"
        data["domain"] = "Reusable AI-friendly FastAPI backend template."
        _write_context(tmp_path, data)
        assert collect_project_context_issues(tmp_path) == []

    @pytest.mark.unit
    def test_missing_file(self, tmp_path: Path) -> None:
        issues = collect_project_context_issues(tmp_path)
        assert len(issues) == 1
        assert "does not exist" in issues[0].message

    @pytest.mark.unit
    def test_invalid_json(self, tmp_path: Path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "project_context.json").write_text("{invalid", encoding="utf-8")
        issues = collect_project_context_issues(tmp_path)
        assert len(issues) == 1
        assert "Invalid JSON" in issues[0].message

    @pytest.mark.unit
    def test_missing_required_field(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        del data["project_name"]
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any("project_name" in i.field for i in issues)

    @pytest.mark.unit
    def test_wrong_type(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["verticals"] = "not a dict"
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any("verticals" in i.field for i in issues)

    @pytest.mark.unit
    def test_invalid_vertical_status(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["verticals"]["orders"]["status"] = "unknown_status"
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any("status" in i.field for i in issues)

    @pytest.mark.unit
    def test_invalid_integration_type(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["integrations"]["ext"] = {"type": "invalid_type", "description": "test"}
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any("type" in i.field for i in issues)

    @pytest.mark.unit
    def test_business_rule_references_unknown_vertical(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["business_rules"]["BR-002"] = {
            "summary": "test",
            "vertical": "nonexistent",
            "enforcement": "domain",
        }
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any("nonexistent" in i.message for i in issues)

    @pytest.mark.unit
    def test_vertical_references_unknown_business_rule(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["verticals"]["orders"]["business_rules"] = ["BR-999"]
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any("BR-999" in i.message for i in issues)

    @pytest.mark.unit
    def test_cross_cutting_rule_is_valid(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["business_rules"]["BR-002"] = {
            "summary": "Global logging required",
            "vertical": "cross-cutting",
            "enforcement": "infrastructure",
        }
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert not any("BR-002" in i.message for i in issues)

    @pytest.mark.unit
    def test_empty_verticals_and_rules_is_valid(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["verticals"] = {}
        data["business_rules"] = {}
        _write_context(tmp_path, data)
        assert collect_project_context_issues(tmp_path) == []

    @pytest.mark.unit
    def test_invalid_status_carries_rule_id(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["verticals"]["orders"]["status"] = "BOGUS"
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any(issue.rule_id == "project_context.invalid_vertical_status" for issue in issues)

    @pytest.mark.unit
    def test_missing_top_level_carries_rule_id(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data.pop("integrations")
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any(issue.rule_id == "project_context.missing_required_field" for issue in issues)

    @pytest.mark.unit
    def test_invalid_json_carries_rule_id(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "project_context.json").write_text("{ not valid json", encoding="utf-8")
        issues = collect_project_context_issues(tmp_path)
        assert len(issues) == 1
        assert issues[0].rule_id == "project_context.invalid_json"


class TestProjectContextRulePlaybook:
    @pytest.mark.unit
    def test_known_rule_returns_dict(self) -> None:
        playbook = get_repository_metadata_rule_playbook("project_context.invalid_vertical_status")
        assert isinstance(playbook, dict)
        assert "meaning" in playbook
        assert "smallest_command_to_rerun" in playbook

    @pytest.mark.unit
    def test_unknown_rule_returns_none(self) -> None:
        assert get_repository_metadata_rule_playbook("project_context.bogus_rule") is None


# =========================================================================================
# Shape of the shipped example (unaffected by the merge — reads docs/project_context.json and
# PROJECT.md directly, not through any of the three former validators)
# =========================================================================================

_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestTheShippedFileShowsTheShape:
    @staticmethod
    def _shipped_context() -> dict[str, Any]:
        shipped: dict[str, Any] = json.loads(
            (_REPO_ROOT / "docs" / "project_context.json").read_text(encoding="utf-8")
        )
        if not shipped.get("is_template", False):
            pytest.skip("this repository is a project built from the template, not the template")
        return shipped

    @pytest.mark.unit
    def test_the_shipped_context_demonstrates_a_business_rule(self) -> None:
        data = self._shipped_context()
        rules = data["business_rules"]
        assert rules, "business_rules is empty, so the shipped file shows no example of the shape"
        referenced = {
            rule_id
            for vertical in data["verticals"].values()
            for rule_id in vertical.get("business_rules", [])
        }
        assert referenced, "no vertical references a rule, so the cross-reference is undemonstrated"
        assert referenced <= set(rules)

    @pytest.mark.unit
    def test_the_prose_and_the_json_agree_on_whether_rules_exist(self) -> None:
        data = self._shipped_context()
        prose = (_REPO_ROOT / "PROJECT.md").read_text(encoding="utf-8")
        missing = [rule_id for rule_id in data["business_rules"] if rule_id not in prose]
        assert missing == [], (
            f"docs/project_context.json declares {missing} and PROJECT.md never mentions them, "
            "though it calls that file its machine-readable counterpart"
        )

    @pytest.mark.unit
    def test_every_constant_a_shipped_rule_names_still_exists(self) -> None:
        import re

        constant_pattern = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
        data = self._shipped_context()
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((_REPO_ROOT / "project").rglob("*.py"))
        )
        missing: list[str] = []
        for rule_id, rule in data["business_rules"].items():
            for name in constant_pattern.findall(str(rule["summary"])):
                if not re.search(rf"\b{re.escape(name)}\b", sources):
                    missing.append(f"{rule_id} -> {name}")
        assert missing == [], f"these rules name constants no longer in project/: {missing}"
