# FILE: tests/application/test_validate_project_context.py
# SUMMARY: Tests for the project context validator ensuring schema and cross-reference checks work.

from __future__ import annotations

import json

import pytest

from pathlib import Path
from typing import Any

from scripts.validate_project_context import (
    TEMPLATE_DOMAIN,
    TEMPLATE_PROJECT_NAME,
    collect_project_context_issues,
    get_project_context_rule_playbook,
)


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


# CLASS: tests.application.test_validate_project_context.TestProjectContextValidation
# SUMMARY: Tests for project context schema and cross-reference validation.
class TestProjectContextValidation:
    # FUNCTION: test_valid_skeleton_passes
    # SUMMARY: Verify that a well-formed project context produces no issues.
    @pytest.mark.unit
    def test_valid_skeleton_passes(self, tmp_path: Path) -> None:
        _write_context(tmp_path, _valid_skeleton())
        issues = collect_project_context_issues(tmp_path)
        assert issues == []

    # FUNCTION: test_template_identity_left_behind_is_reported
    # SUMMARY: A project that flipped is_template but kept the template's name or domain is caught.
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

    # FUNCTION: test_a_domain_that_merely_names_the_template_is_not_an_unreplaced_identity
    # SUMMARY: Naming the kernel a service is built on is a fact about the service, not an omission.
    # NOTE: The rule used to ask whether the domain CONTAINED "mayak", so this sentence — written
    # deliberately, for this service — failed a rule about forgetting to write a domain at all.
    # The subject is whether the shipped paragraph is still there, which is what equality asks.
    @pytest.mark.unit
    def test_a_domain_that_merely_names_the_template_is_not_an_unreplaced_identity(
        self, tmp_path: Path
    ) -> None:
        data = _valid_skeleton()
        data["domain"] = "Retrieval service for engineering documents, built on the Mayak kernel."
        _write_context(tmp_path, data)

        assert collect_project_context_issues(tmp_path) == []

    # FUNCTION: test_template_domain_constant_matches_the_shipped_file
    # SUMMARY: The constant the rule compares against must be the paragraph the template ships.
    # NOTE: Without this, editing docs/project_context.json's domain in the template silently
    # disarms the rule for every project created afterwards — the constant would match nothing.
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

    # FUNCTION: test_context_without_the_flag_still_passes
    # SUMMARY: Regression guard: `is_template` was briefly required, which turned every existing
    # project on this kernel red the moment it pulled the update, with no migration path.
    @pytest.mark.unit
    def test_context_without_the_flag_still_passes(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        del data["is_template"]
        _write_context(tmp_path, data)

        assert collect_project_context_issues(tmp_path) == []

    # FUNCTION: test_template_itself_keeps_its_own_identity
    # SUMMARY: While is_template is true the template's own name and domain are the correct content.
    @pytest.mark.unit
    def test_template_itself_keeps_its_own_identity(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["is_template"] = True
        data["project_name"] = "Mayak"
        data["domain"] = "Reusable AI-friendly FastAPI backend template."
        _write_context(tmp_path, data)

        assert collect_project_context_issues(tmp_path) == []

    # FUNCTION: test_missing_file
    # SUMMARY: Verify that a missing file produces a helpful issue.
    @pytest.mark.unit
    def test_missing_file(self, tmp_path: Path) -> None:
        issues = collect_project_context_issues(tmp_path)
        assert len(issues) == 1
        assert "does not exist" in issues[0].message

    # FUNCTION: test_invalid_json
    # SUMMARY: Verify that malformed JSON is caught.
    @pytest.mark.unit
    def test_invalid_json(self, tmp_path: Path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "project_context.json").write_text("{invalid", encoding="utf-8")
        issues = collect_project_context_issues(tmp_path)
        assert len(issues) == 1
        assert "Invalid JSON" in issues[0].message

    # FUNCTION: test_missing_required_field
    # SUMMARY: Verify that missing top-level fields are detected.
    @pytest.mark.unit
    def test_missing_required_field(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        del data["project_name"]
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any("project_name" in i.field for i in issues)

    # FUNCTION: test_wrong_type
    # SUMMARY: Verify that wrong field types are detected.
    @pytest.mark.unit
    def test_wrong_type(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["verticals"] = "not a dict"
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any("verticals" in i.field for i in issues)

    # FUNCTION: test_invalid_vertical_status
    # SUMMARY: Verify that invalid vertical status values are detected.
    @pytest.mark.unit
    def test_invalid_vertical_status(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["verticals"]["orders"]["status"] = "unknown_status"
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any("status" in i.field for i in issues)

    # FUNCTION: test_invalid_integration_type
    # SUMMARY: Verify that invalid integration types are detected.
    @pytest.mark.unit
    def test_invalid_integration_type(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["integrations"]["ext"] = {"type": "invalid_type", "description": "test"}
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any("type" in i.field for i in issues)

    # FUNCTION: test_business_rule_references_unknown_vertical
    # SUMMARY: Verify that business rules referencing non-existent verticals are detected.
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

    # FUNCTION: test_vertical_references_unknown_business_rule
    # SUMMARY: Verify that verticals referencing non-existent business rules are detected.
    @pytest.mark.unit
    def test_vertical_references_unknown_business_rule(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["verticals"]["orders"]["business_rules"] = ["BR-999"]
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any("BR-999" in i.message for i in issues)

    # FUNCTION: test_cross_cutting_rule_is_valid
    # SUMMARY: Verify that cross-cutting business rules pass validation.
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

    # FUNCTION: test_empty_verticals_and_rules_is_valid
    # SUMMARY: Verify that empty verticals and rules are accepted.
    @pytest.mark.unit
    def test_empty_verticals_and_rules_is_valid(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["verticals"] = {}
        data["business_rules"] = {}
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert issues == []

    # FUNCTION: test_invalid_status_carries_rule_id
    # SUMMARY: Verify that invalid_vertical_status issue carries the matching rule_id.
    @pytest.mark.unit
    def test_invalid_status_carries_rule_id(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["verticals"]["orders"]["status"] = "BOGUS"
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any(issue.rule_id == "project_context.invalid_vertical_status" for issue in issues)

    # FUNCTION: test_missing_top_level_carries_rule_id
    # SUMMARY: Verify that missing required top-level field issues carry rule_id.
    @pytest.mark.unit
    def test_missing_top_level_carries_rule_id(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data.pop("integrations")
        _write_context(tmp_path, data)
        issues = collect_project_context_issues(tmp_path)
        assert any(issue.rule_id == "project_context.missing_required_field" for issue in issues)

    # FUNCTION: test_invalid_json_carries_rule_id
    # SUMMARY: Verify that an unparseable file produces a single invalid_json issue.
    @pytest.mark.unit
    def test_invalid_json_carries_rule_id(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "project_context.json").write_text("{ not valid json", encoding="utf-8")
        issues = collect_project_context_issues(tmp_path)
        assert len(issues) == 1
        assert issues[0].rule_id == "project_context.invalid_json"


# CLASS: tests.application.test_validate_project_context.TestProjectContextRulePlaybook
# SUMMARY: Tests for the get_project_context_rule_playbook helper.
class TestProjectContextRulePlaybook:
    # FUNCTION: test_known_rule_returns_dict
    # SUMMARY: Known rule_id returns a dict with the expected keys.
    @pytest.mark.unit
    def test_known_rule_returns_dict(self) -> None:
        playbook = get_project_context_rule_playbook("project_context.invalid_vertical_status")
        assert isinstance(playbook, dict)
        assert "meaning" in playbook
        assert "smallest_command_to_rerun" in playbook

    # FUNCTION: test_unknown_rule_returns_none
    # SUMMARY: Unknown rule_id returns None (chain pattern).
    @pytest.mark.unit
    def test_unknown_rule_returns_none(self) -> None:
        assert get_project_context_rule_playbook("project_context.bogus_rule") is None
