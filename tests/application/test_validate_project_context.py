# FILE: tests/application/test_validate_project_context.py
# SUMMARY: Tests for the project context validator ensuring schema and cross-reference checks work.

from __future__ import annotations

import json
import re

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


# FUNCTION: _write_wiring
# SUMMARY: Write the two wiring files a checkout registers its verticals in.
# INPUT: service_keys (list[str]): Keys the vertical service registry returns.
# INPUT: router_modules (list[str]): Endpoint modules router_registration.py imports.
def _write_wiring(tmp_path: Path, service_keys: list[str], router_modules: list[str]) -> None:
    core = tmp_path / "project" / "core"
    core.mkdir(parents=True, exist_ok=True)
    entries = ", ".join(f'"{key}": None' for key in service_keys)
    (core / "service_registration.py").write_text(
        "from __future__ import annotations\n"
        "from typing import Any\n\n\n"
        "def build_reference_services() -> dict[str, Any]:\n"
        f"    services: dict[str, Any] = {{{entries}}}\n"
        "    return services\n",
        encoding="utf-8",
    )
    api = tmp_path / "project" / "infrastructure" / "api"
    api.mkdir(parents=True, exist_ok=True)
    imports = "\n".join(
        f"from project.infrastructure.api.endpoints.{module} import router as {module}_router"
        for module in router_modules
    )
    (api / "router_registration.py").write_text(
        f"{imports}\n\n\ndef include_application_routers(app: object) -> None:\n    pass\n",
        encoding="utf-8",
    )


# CLASS: tests.application.test_validate_project_context.TestAStatusIsCheckedAgainstTheWiring
# SUMMARY: Verify a declared status has to agree with what the wiring files register.
# NOTE: The status went stale three releases running, because nothing read it against the code.
# Every other cross-reference in this file is internal to the JSON — this is the one that leaves
# it, and it is one-directional on purpose: a router with no declared vertical is the kernel's
# own health endpoint, not a defect.
class TestAStatusIsCheckedAgainstTheWiring:
    # FUNCTION: test_a_planned_vertical_that_is_already_wired_is_reported
    # SUMMARY: Verify 'planned' plus a registered router or service reddens the gate.
    @pytest.mark.unit
    def test_a_planned_vertical_that_is_already_wired_is_reported(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["verticals"]["orders"]["status"] = "planned"
        _write_context(tmp_path, data)
        _write_wiring(tmp_path, ["orders_service"], ["orders"])

        issues = collect_project_context_issues(tmp_path)

        assert [issue.rule_id for issue in issues] == [
            "project_context.vertical_status_contradicts_wiring"
        ]
        assert issues[0].field == "verticals.orders.status"

    # FUNCTION: test_a_running_vertical_that_nothing_registers_is_reported
    # SUMMARY: Verify 'active' with no registration anywhere reddens the gate.
    @pytest.mark.unit
    def test_a_running_vertical_that_nothing_registers_is_reported(self, tmp_path: Path) -> None:
        _write_context(tmp_path, _valid_skeleton())
        _write_wiring(tmp_path, ["billing_service"], ["billing"])

        issues = collect_project_context_issues(tmp_path)

        assert [issue.rule_id for issue in issues] == [
            "project_context.vertical_status_contradicts_wiring"
        ]

    # FUNCTION: test_a_running_vertical_named_by_either_wiring_file_passes
    # SUMMARY: Verify the service key alone, and the router import alone, each count as wired.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("service_keys", "router_modules"),
        [(["orders_service"], []), ([], ["orders"]), (["orders_service"], ["orders"])],
    )
    def test_a_running_vertical_named_by_either_wiring_file_passes(
        self, tmp_path: Path, service_keys: list[str], router_modules: list[str]
    ) -> None:
        _write_context(tmp_path, _valid_skeleton())
        _write_wiring(tmp_path, service_keys, router_modules)

        assert collect_project_context_issues(tmp_path) == []

    # FUNCTION: test_a_plural_endpoint_module_registers_the_singular_vertical
    # SUMMARY: Verify the conventional plural router module still names its vertical.
    @pytest.mark.unit
    def test_a_plural_endpoint_module_registers_the_singular_vertical(self, tmp_path: Path) -> None:
        data = _valid_skeleton()
        data["verticals"]["order"] = data["verticals"].pop("orders")
        data["business_rules"]["BR-001"]["vertical"] = "order"
        _write_context(tmp_path, data)
        _write_wiring(tmp_path, [], ["orders"])

        assert collect_project_context_issues(tmp_path) == []

    # FUNCTION: test_a_singular_vertical_beside_its_plural_is_judged_on_its_own_wiring
    # SUMMARY: Verify one vertical's router does not register a differently named neighbour.
    # **LOGIC_STEP**: The singular of a plural endpoint module is accepted so that the
    # conventional `orders.py` registers the `order` vertical. When a project declares both names
    # they are two verticals, and reading one's router as the other's registration reported the
    # planned one as already wired — the gate red on correct work, which is how a rule gets
    # switched off.
    @pytest.mark.unit
    def test_a_singular_vertical_beside_its_plural_is_judged_on_its_own_wiring(
        self, tmp_path: Path
    ) -> None:
        data = _valid_skeleton()
        data["verticals"]["order"] = {
            "description": "Not built yet",
            "status": "planned",
            "domain_entities": [],
        }
        _write_context(tmp_path, data)
        _write_wiring(tmp_path, ["orders_service"], ["orders"])

        assert collect_project_context_issues(tmp_path) == []

    # FUNCTION: test_a_checkout_without_wiring_files_says_nothing_about_status
    # SUMMARY: Verify absent wiring is silence, not evidence that nothing is registered.
    @pytest.mark.unit
    def test_a_checkout_without_wiring_files_says_nothing_about_status(
        self, tmp_path: Path
    ) -> None:
        _write_context(tmp_path, _valid_skeleton())

        assert collect_project_context_issues(tmp_path) == []

    # FUNCTION: test_the_new_rule_has_a_playbook
    # SUMMARY: Verify the rule answers `query_ai_context.py failure rule` like every other.
    @pytest.mark.unit
    def test_the_new_rule_has_a_playbook(self) -> None:
        playbook = get_project_context_rule_playbook(
            "project_context.vertical_status_contradicts_wiring"
        )

        assert playbook is not None
        read_first = playbook["read_first"]
        assert isinstance(read_first, list)
        assert "project/core/service_registration.py" in read_first


# ATTRIBUTE: _CONSTANT (re.Pattern[str])
# SUMMARY: An UPPER_SNAKE_CASE identifier written into a business-rule summary.
_CONSTANT = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")

_REPO_ROOT = Path(__file__).resolve().parents[2]


# CLASS: tests.application.test_validate_project_context.TestTheShippedFileShowsTheShape
# SUMMARY: Verify the file every project copies demonstrates business_rules instead of leaving {}.
# NOTE: The section shipped empty, so a project inheriting this file learned the key exists and
# nothing about what goes in it — while the validator enforces a shape (summary, vertical, and a
# cross-reference from the vertical) that has to be discovered by reading the validator's source.
# The three shipped rules are the ones the reference vertical really enforces, and the constants
# they name are read back out of the code here, so deleting one turns this red rather than leaving
# a confident sentence about a bound that no longer exists.
class TestTheShippedFileShowsTheShape:
    # FUNCTION: test_the_shipped_context_demonstrates_a_business_rule
    # SUMMARY: Verify the shipped file carries rules and references them from a vertical.
    @pytest.mark.unit
    def test_the_shipped_context_demonstrates_a_business_rule(self) -> None:
        data = json.loads(
            (_REPO_ROOT / "docs" / "project_context.json").read_text(encoding="utf-8")
        )
        rules = data["business_rules"]

        assert rules, "business_rules is empty, so the shipped file shows no example of the shape"
        referenced = {
            rule_id
            for vertical in data["verticals"].values()
            for rule_id in vertical.get("business_rules", [])
        }
        assert referenced, "no vertical references a rule, so the cross-reference is undemonstrated"
        assert referenced <= set(rules)

    # FUNCTION: test_the_prose_and_the_json_agree_on_whether_rules_exist
    # SUMMARY: Verify PROJECT.md names every rule the machine-readable file declares.
    # **LOGIC_STEP**: PROJECT.md's own header calls docs/project_context.json its machine-readable
    # counterpart, and nothing checked that they say the same thing. Filling the JSON in left the
    # prose reading "No formal business rules in the kernel", so the file CLAUDE.md sends a reader
    # to for domain context contradicted the file it points at. Ids only: the sentences are
    # written for different readers and are meant to differ.
    @pytest.mark.unit
    def test_the_prose_and_the_json_agree_on_whether_rules_exist(self) -> None:
        data = json.loads(
            (_REPO_ROOT / "docs" / "project_context.json").read_text(encoding="utf-8")
        )
        prose = (_REPO_ROOT / "PROJECT.md").read_text(encoding="utf-8")

        missing = [rule_id for rule_id in data["business_rules"] if rule_id not in prose]

        assert missing == [], (
            f"docs/project_context.json declares {missing} and PROJECT.md never mentions them, "
            "though it calls that file its machine-readable counterpart"
        )

    # FUNCTION: test_every_constant_a_shipped_rule_names_still_exists
    # SUMMARY: Verify a rule's summary does not describe a bound the code no longer has.
    @pytest.mark.unit
    def test_every_constant_a_shipped_rule_names_still_exists(self) -> None:
        data = json.loads(
            (_REPO_ROOT / "docs" / "project_context.json").read_text(encoding="utf-8")
        )
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((_REPO_ROOT / "project").rglob("*.py"))
        )

        missing: list[str] = []
        for rule_id, rule in data["business_rules"].items():
            for name in _CONSTANT.findall(str(rule["summary"])):
                if f"{name} " not in sources and f"{name}\n" not in sources:
                    missing.append(f"{rule_id} -> {name}")

        assert missing == [], f"these rules name constants no longer in project/: {missing}"
