# FILE: tests/application/test_validate_architecture.py
# SUMMARY: Unit tests for the repository architecture boundary validator.

from pathlib import Path
import sys
from typing import Any

import pytest

from scripts.validate_architecture import (
    ArchitectureIssue,
    collect_architecture_issues,
    get_architecture_rule_playbook,
    get_layer_rules,
    main,
)


# FUNCTION: _write_fixture
# SUMMARY: Write a Python source fixture into a temporary repository layout.
# INPUT: path (Path): Target file path.
# INPUT: content (str): Python source content.
# OUTPUT: (None): None.
def _write_fixture(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# CLASS: tests.application.test_validate_architecture.TestValidateArchitecture
# SUMMARY: Verify the architecture validator catches forbidden imports and allows valid ones.
class TestValidateArchitecture:
    # FUNCTION: test_get_layer_rules_exposes_declared_and_runtime_constraints
    # SUMMARY: Verify the machine-readable layer rules separate declared dependencies from runtime-enforced prefixes.
    @pytest.mark.unit
    def test_get_layer_rules_exposes_declared_and_runtime_constraints(self) -> None:
        layer_rules: dict[str, dict[str, Any]] = get_layer_rules()

        assert layer_rules["domain"]["guidance_only"]["declared_allowed_dependencies"] == (
            "project.domain",
        )
        assert layer_rules["domain"]["guidance_only"]["rule_strength"] == "guidance_only"
        assert layer_rules["application"]["runtime_enforced"]["forbidden_imports"] == (
            "project.infrastructure",
        )
        assert layer_rules["application"]["runtime_enforced"]["rule_strength"] == "runtime_enforced"
        assert (
            layer_rules["infrastructure"]["runtime_enforced"]["validator"]
            == "scripts/validate_architecture.py"
        )
        assert (
            "project.core.composition_root"
            in layer_rules["infrastructure"]["runtime_enforced"]["forbidden_imports"]
        )
        assert (
            layer_rules["application"]["not_enforced_in_validator"][
                "declared_allowed_dependencies_are_not_whitelist_gate"
            ]
            is True
        )

        # **LOGIC_STEP**: The domain used to assert the same thing as application — that its
        # declared dependencies were guidance rather than a gate. That flipped on 2026-08-12: the
        # domain is now checked with an allowlist, so the declaration IS the gate. Anything reading
        # these rules to tell "intended architecture" from "the build stops you" has to see the
        # difference, which is why the flag is derived per layer rather than hardcoded True.
        assert (
            layer_rules["domain"]["not_enforced_in_validator"][
                "declared_allowed_dependencies_are_not_whitelist_gate"
            ]
            is False
        )
        assert layer_rules["domain"]["runtime_enforced"]["allowed_imports"] == ("project.domain",)
        assert layer_rules["application"]["runtime_enforced"]["allowed_imports"] == ()
        assert "whitelist gate" in layer_rules["domain"]["enforcement_summary"]

    # FUNCTION: test_get_architecture_rule_playbook_returns_shared_failure_guidance
    # SUMMARY: Verify architecture rule playbooks are reusable by the query layer.
    @pytest.mark.unit
    def test_get_architecture_rule_playbook_returns_shared_failure_guidance(
        self,
    ) -> None:
        playbook: dict[str, Any] | None = get_architecture_rule_playbook(
            "arch.application.no_forbidden_import"
        )

        assert playbook is not None
        assert "runtime_enforced boundary" in playbook["meaning"]
        assert playbook["smallest_command_to_rerun"] == (
            "uv run python scripts/validate_architecture.py"
        )
        assert "project/core/composition_root.py" in playbook["read_first"]

    # FUNCTION: test_validator_rejects_domain_importing_infrastructure
    # SUMMARY: Verify domain modules cannot import infrastructure adapters directly.
    @pytest.mark.unit
    def test_validator_rejects_domain_importing_infrastructure(
        self,
        tmp_path: Path,
    ) -> None:
        _write_fixture(
            tmp_path / "project" / "domain" / "bad_module.py",
            "from project.infrastructure.api.dependencies import get_business_service\n",
        )

        issues = collect_architecture_issues(tmp_path)

        assert any(
            "project.infrastructure.api.dependencies" in issue.message
            and issue.rule_id == "arch.domain.import_not_allowed"
            for issue in issues
        )

    # FUNCTION: test_validator_rejects_the_module_a_blacklist_missed
    # SUMMARY: Verify the import that motivated the allowlist is rejected by name.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "line",
        [
            "from langchain_core.messages import BaseMessage\n",
            "import langchain_openai\n",
            "from langgraph.prebuilt import create_react_agent\n",
        ],
    )
    def test_validator_rejects_the_module_a_blacklist_missed(
        self,
        tmp_path: Path,
        line: str,
    ) -> None:
        # **LOGIC_STEP**: These three are the regression. The rule used to ban the prefix
        # `langchain`, and prefix matching accepts only an exact name or `langchain.` with a dot —
        # so `langchain_core`, imported on eight lines under `project/`, passed. Measured on
        # 2026-08-12: a domain module importing it was green on lint, mypy and this validator.
        _write_fixture(tmp_path / "project" / "domain" / "bad_module.py", line)

        issues = collect_architecture_issues(tmp_path)

        assert [issue.rule_id for issue in issues] == ["arch.domain.import_not_allowed"]

    # FUNCTION: test_validator_rejects_a_library_nobody_thought_to_ban
    # SUMMARY: Verify the allowlist covers what has not been invented yet — the point of inverting.
    @pytest.mark.unit
    def test_validator_rejects_a_library_nobody_thought_to_ban(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "domain" / "bad_module.py",
            "import some_llm_library_released_next_quarter\n",
        )

        issues = collect_architecture_issues(tmp_path)

        assert [issue.rule_id for issue in issues] == ["arch.domain.import_not_allowed"]

    # FUNCTION: test_validator_allows_the_standard_library_and_its_own_layer
    # SUMMARY: Verify the allowlist does not reject what every domain module actually imports.
    @pytest.mark.unit
    def test_validator_allows_the_standard_library_and_its_own_layer(
        self,
        tmp_path: Path,
    ) -> None:
        # **LOGIC_STEP**: `tomllib` and `zoneinfo` are here rather than the four names this kernel
        # happens to use, because the stdlib half is read from sys.stdlib_module_names — a test
        # that only covered today's imports would pass against a hand-written list too.
        _write_fixture(
            tmp_path / "project" / "domain" / "good_module.py",
            "from __future__ import annotations\n"
            "import uuid\n"
            "import tomllib\n"
            "from datetime import datetime\n"
            "from zoneinfo import ZoneInfo\n"
            "from project.domain.other import Thing\n",
        )

        assert collect_architecture_issues(tmp_path) == []

    # FUNCTION: test_validator_rejects_application_importing_infrastructure
    # SUMMARY: Verify application modules cannot import infrastructure implementations.
    @pytest.mark.unit
    def test_validator_rejects_application_importing_infrastructure(
        self,
        tmp_path: Path,
    ) -> None:
        _write_fixture(
            tmp_path / "project" / "application" / "bad_module.py",
            "from project.infrastructure.persistence.orm_models import ReferenceTaskORM\n",
        )

        issues = collect_architecture_issues(tmp_path)

        assert any(
            "Application layer must not import" in issue.message
            and "project.infrastructure.persistence.orm_models" in issue.message
            for issue in issues
        )

    # FUNCTION: test_validator_allows_infrastructure_importing_application
    # SUMMARY: Verify infrastructure modules may depend on application code.
    @pytest.mark.unit
    def test_validator_allows_infrastructure_importing_application(
        self,
        tmp_path: Path,
    ) -> None:
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "good_module.py",
            "from project.application.business_service import BusinessService\n",
        )

        issues = collect_architecture_issues(tmp_path)

        assert issues == []

    # FUNCTION: test_validator_rejects_infrastructure_importing_composition_root
    # SUMMARY: Verify infrastructure modules cannot couple back into runtime assembly modules.
    @pytest.mark.unit
    def test_validator_rejects_infrastructure_importing_composition_root(
        self,
        tmp_path: Path,
    ) -> None:
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "bad_module.py",
            "from project.core.composition_root import CompositionRoot\n",
        )

        issues = collect_architecture_issues(tmp_path)

        assert any(
            issue.rule_id == "arch.infrastructure.no_forbidden_import"
            and "project.core.composition_root" in issue.message
            for issue in issues
        )

    # FUNCTION: test_validator_emits_syntax_error_issue_on_broken_file
    # SUMMARY: Regression guard: a broken Python file must surface as a structured ArchitectureIssue with rule_id 'arch.syntax_error', not a raw Python traceback.
    @pytest.mark.unit
    def test_validator_emits_syntax_error_issue_on_broken_file(
        self,
        tmp_path: Path,
    ) -> None:
        _write_fixture(
            tmp_path / "project" / "domain" / "broken.py",
            "def foo(:\n    pass\n",
        )

        issues = collect_architecture_issues(tmp_path)

        syntax_issues = [issue for issue in issues if issue.rule_id == "arch.syntax_error"]
        assert len(syntax_issues) == 1
        assert "SyntaxError while parsing" in syntax_issues[0].message
        # **LOGIC_STEP**: Playbook is registered so failure_playbook() can route this rule_id.
        assert get_architecture_rule_playbook("arch.syntax_error") is not None

    # FUNCTION: test_main_json_output_includes_rule_and_remediation
    # SUMMARY: Verify JSON mode emits stable remediation metadata for architecture violations.
    @pytest.mark.unit
    def test_main_json_output_includes_rule_and_remediation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        issue_repo_root = Path.cwd()
        issue_path = issue_repo_root / "project" / "application" / "bad_module.py"
        monkeypatch.setattr(
            "scripts.validate_architecture.collect_architecture_issues",
            lambda _repo_root: [
                ArchitectureIssue(
                    path=issue_path,
                    line=5,
                    message="Application layer must not import infrastructure.",
                    rule_id="arch.application.no_forbidden_import",
                )
            ],
        )
        monkeypatch.setattr(sys, "argv", ["scripts/validate_architecture.py", "--json"])

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 1
        assert '"rule_id": "arch.application.no_forbidden_import"' in captured.out
        assert '"suggested_fix"' in captured.out
