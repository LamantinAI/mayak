# FILE: test_validator_error_contract.py
# SUMMARY: Contract test (T4 validator-error-contract) proving every scripts/validate_*.py JSON converter — plus the generate_ai_context.py drift-issue producer — surfaces rule_id, suggested_fix, read_first, next_commands, and stop_widening_condition on every emitted issue. This is the red->green fixation for the audit finding that stop_widening_condition was 0/10 in actual CLI JSON output despite living in every validator's internal rule-playbook dict.

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.generate_ai_context import generated_output_issues
from scripts.validate_architecture import collect_architecture_issues
from scripts.validate_architecture import _issue_to_payload as _architecture_issue_to_payload
from scripts.validate_cbm import _issue_to_json as _cbm_issue_to_json
from scripts.validate_cbm import collect_validation_issues
from scripts.validate_endpoint_wiring import collect_endpoint_wiring_issues
from scripts.validate_endpoint_wiring import _issue_to_payload as _endpoint_issue_to_payload
from scripts.validate_file_policy import collect_file_policy_issues
from scripts.validate_file_policy import _issue_to_payload as _file_policy_issue_to_payload
from scripts.validate_migrations import MigrationIssue
from scripts.validate_migrations import _issue_to_payload as _migrations_issue_to_payload
from scripts.validate_module_sizes import MAX_CODE_LINES
from scripts.validate_module_sizes import _issue_to_json as _module_size_issue_to_json
from scripts.validate_module_sizes import collect_module_size_issues
from scripts.validate_project_context import collect_project_context_issues
from scripts.validate_project_context import (
    _issue_to_payload as _project_context_issue_to_payload,
)
from scripts.validate_runtime_ownership import collect_runtime_ownership_issues
from scripts.validate_runtime_ownership import (
    _issue_to_payload as _runtime_ownership_issue_to_payload,
)
from scripts.validate_script_paths import collect_script_path_issues
from scripts.validate_script_paths import _issue_to_payload as _script_paths_issue_to_payload
from scripts.validate_skills_frontmatter import collect_skills_frontmatter_issues
from scripts.validate_skills_frontmatter import (
    _issue_to_payload as _skills_frontmatter_issue_to_payload,
)
import scripts.validate_skills_frontmatter as validate_skills_frontmatter


# ATTRIBUTE: _REQUIRED_KEYS (tuple[str, ...])
# SUMMARY: The five fields the 2026-07-04 audit matrix tracked across all 10 validators
# (rule_id, suggested_fix, read_first, next_commands, stop_widening_condition), plus the
# minimum location/severity fields every canon payload must also carry.
_REQUIRED_KEYS = (
    "rule_id",
    "category",
    "file",
    "line",
    "message",
    "severity",
    "suggested_fix",
    "read_first",
    "next_commands",
    "stop_widening_condition",
)


# FUNCTION: _assert_contract
# SUMMARY: Assert a single issue payload satisfies the ValidatorIssuePayload canon: all required
# keys present, and the five audit-tracked fields are non-empty (not just present-but-blank).
# INPUT: payload (dict[str, object]): Issue payload returned by a validator's _issue_to_payload/_issue_to_json converter.
# OUTPUT: (None): None. Raises AssertionError on the first violated contract clause.
def _assert_contract(payload: dict[str, object]) -> None:
    for key in _REQUIRED_KEYS:
        assert key in payload, f"payload missing required key {key!r}: {payload!r}"

    assert isinstance(payload["rule_id"], str) and payload["rule_id"], "rule_id must be non-empty"
    assert isinstance(payload["suggested_fix"], str) and payload["suggested_fix"], (
        f"suggested_fix must be non-empty: {payload!r}"
    )
    assert isinstance(payload["read_first"], list) and payload["read_first"], (
        f"read_first must be a non-empty list: {payload!r}"
    )
    assert isinstance(payload["next_commands"], list) and payload["next_commands"], (
        f"next_commands must be a non-empty list: {payload!r}"
    )
    assert (
        isinstance(payload["stop_widening_condition"], str) and payload["stop_widening_condition"]
    ), f"stop_widening_condition must be non-empty: {payload!r}"


# FUNCTION: _write_fixture
# SUMMARY: Write a Python source fixture into a temporary repository layout.
# INPUT: path (Path): Target file path.
# INPUT: content (str): Python source content.
# OUTPUT: (None): None.
def _write_fixture(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# CLASS: tests.application.test_validator_error_contract.TestValidatorErrorContract
# SUMMARY: AC1 — force a minimal violation per validator and assert its JSON issue payload
# satisfies the ValidatorIssuePayload canon (all 10 validators, 5 audit-tracked fields each).
class TestValidatorErrorContract:
    # FUNCTION: test_architecture_issue_satisfies_contract
    # SUMMARY: Force a domain->infrastructure forbidden import and check the JSON payload.
    @pytest.mark.unit
    def test_architecture_issue_satisfies_contract(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "domain" / "bad_module.py",
            "from project.infrastructure.api.dependencies import get_business_service\n",
        )
        issues = collect_architecture_issues(tmp_path)
        assert issues, "expected at least one forced architecture violation"
        for issue in issues:
            _assert_contract(_architecture_issue_to_payload(issue, tmp_path))

    # FUNCTION: test_endpoint_wiring_issue_satisfies_contract
    # SUMMARY: Force a direct-service-import endpoint violation and check the JSON payload.
    @pytest.mark.unit
    def test_endpoint_wiring_issue_satisfies_contract(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "api" / "endpoints" / "bad.py",
            "\n".join(
                [
                    "from fastapi import APIRouter",
                    "from project.application.business_service import BusinessService",
                    "",
                    "router = APIRouter()",
                    "",
                    "@router.get('/')",
                    "async def read_items(service: BusinessService) -> dict[str, str]:",
                    "    return {'status': 'ok'}",
                ]
            ),
        )
        context_map: dict[str, object] = {
            "service_registry": {
                "business_service": {
                    "class": "BusinessService",
                    "module": "project.application.business_service.BusinessService",
                }
            },
            "dependency_registry": {
                "aliases": {
                    "BusinessServiceDep": {
                        "getter": "get_business_service",
                        "service_key": "business_service",
                        "service_module": "project.application.business_service.BusinessService",
                        "service_type": "BusinessService",
                    }
                }
            },
        }
        issues = collect_endpoint_wiring_issues(tmp_path, context_map=context_map)
        assert issues, "expected at least one forced endpoint-wiring violation"
        for issue in issues:
            _assert_contract(_endpoint_issue_to_payload(issue, tmp_path))

    # FUNCTION: test_cbm_issue_satisfies_contract
    # SUMMARY: Force a missing '# FILE:' CBM tag violation and check the JSON payload.
    @pytest.mark.unit
    def test_cbm_issue_satisfies_contract(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "core" / "bad_module.py",
            "x = 1\n",
        )
        issues = collect_validation_issues(tmp_path)
        assert issues, "expected at least one forced CBM violation"
        for issue in issues:
            _assert_contract(_cbm_issue_to_json(issue, tmp_path))

    # FUNCTION: test_module_size_issue_satisfies_contract
    # SUMMARY: Force an oversized production module and check the JSON payload.
    @pytest.mark.unit
    def test_module_size_issue_satisfies_contract(self, tmp_path: Path) -> None:
        path = tmp_path / "project" / "core" / "big_module.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(["x = 1"] * (MAX_CODE_LINES + 1)) + "\n", encoding="utf-8")

        issues = collect_module_size_issues(tmp_path)
        assert issues, "expected at least one forced module-size violation"
        for issue in issues:
            _assert_contract(_module_size_issue_to_json(issue))

    # FUNCTION: test_file_policy_issue_satisfies_contract
    # SUMMARY: Force a FILE_POLICY_INDEX entry with a missing required field and check the JSON payload.
    @pytest.mark.unit
    def test_file_policy_issue_satisfies_contract(self, tmp_path: Path) -> None:
        entry_path = tmp_path / "fixture.txt"
        entry_path.write_text("placeholder", encoding="utf-8")
        index: dict[str, dict[str, object]] = {entry_path.name: {"edit_zone": "safe"}}

        issues = collect_file_policy_issues(tmp_path, index_override=index)
        assert issues, "expected at least one forced file_policy violation"
        for issue in issues:
            _assert_contract(_file_policy_issue_to_payload(issue))

    # FUNCTION: test_migrations_issue_satisfies_contract
    # SUMMARY: Force a head-drift migration failure via a mocked CalledProcessError and check the JSON payload.
    @pytest.mark.unit
    def test_migrations_issue_satisfies_contract(self) -> None:
        issue = MigrationIssue(
            rule_id="migrations.head_drift",
            command_name="check",
            message="check failed with exit code 1",
            returncode=1,
        )
        _assert_contract(_migrations_issue_to_payload(issue))

    # FUNCTION: test_project_context_issue_satisfies_contract
    # SUMMARY: Force a missing docs/project_context.json and check the JSON payload.
    @pytest.mark.unit
    def test_project_context_issue_satisfies_contract(self, tmp_path: Path) -> None:
        issues = collect_project_context_issues(tmp_path)
        assert issues, "expected at least one forced project_context violation"
        for issue in issues:
            _assert_contract(_project_context_issue_to_payload(issue))

    # FUNCTION: test_script_paths_issue_satisfies_contract
    # SUMMARY: Force a broken path-shaped literal inside a scripts/*.py fixture and check the JSON payload.
    @pytest.mark.unit
    def test_script_paths_issue_satisfies_contract(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        bad_script = scripts_dir / "fake_validator.py"
        bad_script.write_text(
            "TARGET = 'tests/application/does_not_exist.py'\n",
            encoding="utf-8",
        )

        issues = collect_script_path_issues(tmp_path, scripts_dir=scripts_dir)
        assert issues, "expected at least one forced script_paths violation"
        for issue in issues:
            _assert_contract(_script_paths_issue_to_payload(issue))

    # FUNCTION: test_skills_frontmatter_issue_satisfies_contract
    # SUMMARY: Force a SKILL.md with no frontmatter block and check the JSON payload.
    @pytest.mark.unit
    def test_skills_frontmatter_issue_satisfies_contract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(validate_skills_frontmatter, "ROOT_DIR", tmp_path)
        skills_dir = tmp_path / "skills" / "broken_skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("# No frontmatter\n", encoding="utf-8")
        monkeypatch.setattr(validate_skills_frontmatter, "SKILL_DIRS", [tmp_path / "skills"])

        issues = collect_skills_frontmatter_issues(tmp_path)
        assert issues, "expected at least one forced skills_frontmatter violation"
        for issue in issues:
            _assert_contract(_skills_frontmatter_issue_to_payload(issue))

    # FUNCTION: test_runtime_ownership_issue_satisfies_contract
    # SUMMARY: AC2 — force an os.getenv access outside the allowlist and check that read_first, next_commands, and stop_widening_condition (previously entirely absent from this validator's inline main() payload) are present in the JSON payload.
    @pytest.mark.unit
    def test_runtime_ownership_issue_satisfies_contract(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "application" / "bad_module.py",
            "import os\nos.getenv('SOME_VAR')\n",
        )
        issues = collect_runtime_ownership_issues(tmp_path)
        assert issues, "expected at least one forced runtime_ownership violation"
        for issue in issues:
            payload = _runtime_ownership_issue_to_payload(issue, tmp_path)
            _assert_contract(payload)
            # AC2 explicit check: these three keys were entirely absent (read_first,
            # next_commands) or dropped (stop_widening_condition) in the pre-fix main().
            assert payload["read_first"]
            assert payload["next_commands"]
            assert payload["stop_widening_condition"]


# CLASS: tests.application.test_validator_error_contract.TestGeneratedOutputIssuesContract
# SUMMARY: Perimeter-extension coverage — scripts/generate_ai_context.py's generated_output_issues() is a second producer of the same issue-payload shape (drift.generated.missing/outdated) with the identical pre-fix gap (no stop_widening_condition even though ai_query.common._DRIFT_RULE_PLAYBOOKS already had it for both rule_ids).
class TestGeneratedOutputIssuesContract:
    # FUNCTION: test_missing_generated_file_satisfies_contract
    # SUMMARY: Force a missing generated output file and check the JSON issue payload.
    @pytest.mark.unit
    def test_missing_generated_file_satisfies_contract(self, tmp_path: Path) -> None:
        missing_path = tmp_path / "docs" / "ai_context_map.json"
        issues = generated_output_issues({missing_path: "irrelevant rendered content"})

        assert len(issues) == 1
        assert issues[0]["rule_id"] == "drift.generated.missing"
        _assert_contract(issues[0])

    # FUNCTION: test_outdated_generated_file_satisfies_contract
    # SUMMARY: Force an outdated generated output file and check the JSON issue payload.
    @pytest.mark.unit
    def test_outdated_generated_file_satisfies_contract(self, tmp_path: Path) -> None:
        outdated_path = tmp_path / "docs" / "ai_context_map.json"
        outdated_path.parent.mkdir(parents=True, exist_ok=True)
        outdated_path.write_text("stale content", encoding="utf-8")

        issues = generated_output_issues({outdated_path: "fresh content"})

        assert len(issues) == 1
        assert issues[0]["rule_id"] == "drift.generated.outdated"
        _assert_contract(issues[0])


# CLASS: tests.application.test_validator_error_contract.TestFailureRuleRegression
# SUMMARY: AC3 — query_ai_context.py failure rule <id> must not have regressed: it reads
# get_*_rule_playbook() functions directly (untouched by this task), independent of the
# _issue_to_payload/_issue_to_json converters this task modified.
class TestFailureRuleRegression:
    # FUNCTION: test_failure_playbook_still_returns_stop_widening_condition
    # SUMMARY: Sample one rule_id per validator family and confirm failure_playbook() still returns stop_widening_condition, proving the get_*_rule_playbook() code path this task did not touch is unaffected.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "rule_id",
        [
            "arch.domain.import_not_allowed",
            "endpoint.no_direct_service_import",
            "cbm.missing_file_tag",
            "size.module_exceeds_limit",
            "file_policy.missing_field",
            "migrations.head_drift",
            "migrations.multiple_heads",
            "migrations.broken_revision_graph",
            "project_context.invalid_json",
            "script_paths.broken_reference",
            "skills_frontmatter.missing_frontmatter",
            "runtime_ownership.env_access_restricted",
            "drift.agent_docs.outdated",
        ],
    )
    def test_failure_playbook_still_returns_stop_widening_condition(self, rule_id: str) -> None:
        from ai_query.common import failure_playbook

        playbook = failure_playbook(rule_id)

        assert playbook["stop_widening_condition"]


# CLASS: tests.application.test_validator_error_contract.TestWave6ValidatorsSatisfyContract
# SUMMARY: The two validators added for the missing safety nets emit the same canonical payload.
class TestWave6ValidatorsSatisfyContract:
    # FUNCTION: test_test_quality_issue_satisfies_contract
    # SUMMARY: Verify validate_test_quality's converter carries the full remediation canon.
    @pytest.mark.unit
    def test_test_quality_issue_satisfies_contract(self, tmp_path: Path) -> None:
        from scripts.validate_test_quality import _issue_to_payload as _test_quality_payload
        from scripts.validate_test_quality import validate_test_module

        module = tmp_path / "test_probe.py"
        module.write_text("def test_green() -> None:\n    assert True\n", encoding="utf-8")

        issues = validate_test_module(module)

        assert issues
        for issue in issues:
            _assert_contract(_test_quality_payload(issue, tmp_path))

    # FUNCTION: test_dependency_issue_satisfies_contract
    # SUMMARY: Verify validate_dependencies' converter carries the full remediation canon.
    @pytest.mark.unit
    def test_dependency_issue_satisfies_contract(self, tmp_path: Path) -> None:
        from scripts.validate_dependencies import _issue_to_payload as _dependency_payload
        from scripts.validate_dependencies import collect_dependency_issues

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "probe"\ndependencies = ["fastapi"]\n',
            encoding="utf-8",
        )
        runtime = tmp_path / "project" / "core"
        runtime.mkdir(parents=True)
        (runtime / "module.py").write_text("import orjson\n", encoding="utf-8")

        issues = collect_dependency_issues(tmp_path)

        assert issues
        for issue in issues:
            _assert_contract(_dependency_payload(issue, tmp_path))

    # FUNCTION: test_secrets_issue_satisfies_contract
    # SUMMARY: Verify validate_secrets' converter carries the full remediation canon.
    # NOTE: The file's own SUMMARY claims every `scripts/validate_*.py` converter is proven here.
    # It was twelve of thirteen until 2026-08-14: `validate_secrets._issue_to_payload` has the same
    # shape and runs in the same gate, and was never imported by this file.
    @pytest.mark.unit
    def test_secrets_issue_satisfies_contract(self) -> None:
        from scripts.validate_secrets import SECRET_RULE_ID, SecretIssue
        from scripts.validate_secrets import _issue_to_payload as _secrets_payload

        issue = SecretIssue(
            rule_id=SECRET_RULE_ID,
            source_file="project/core/config.py",
            line=12,
            kind="AWS access key id",
            message="AWS access key id found on line 12",
        )

        _assert_contract(_secrets_payload(issue))
