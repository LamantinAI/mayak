# FILE: tests/application/test_query_ai_context.py
# SUMMARY: Unit tests for the AI context query CLI helpers.

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import ai_query.common as query_common

from ai_context.errors import ContextBuildError, ContextIssue
from scripts.query_ai_context import (
    _query_bootstrap,
    _query_before_edit,
    _query_failure,
    _query_overview,
    _query_workset,
    main,
)


# FUNCTION: tests.application.test_query_ai_context._tests_for
# SUMMARY: Narrow the tests_for_file payload to Any so assertions can index its heterogeneous values.
def _tests_for(context_map: dict[str, Any], path: str) -> dict[str, Any]:
    return query_common.tests_for_file(context_map, path)


# CLASS: tests.application.test_query_ai_context.TestQueryAIContext
# SUMMARY: Verify the AI context query helpers expose the expected repository navigation metadata.
class TestQueryAIContext:
    # FUNCTION: test_query_bootstrap_returns_compact_startup_payload
    # SUMMARY: Verify bootstrap queries expose the startup path, shortcuts, and kernel/sample segmentation.
    @pytest.mark.unit
    def test_query_bootstrap_returns_compact_startup_payload(self) -> None:
        result = _query_bootstrap()
        payload: dict[str, Any] = result.payload

        assert payload["read_first"][0] == "CLAUDE.md"
        assert "docs/architecture_rules.json" in payload["read_first"]
        assert "uv run python scripts/query_ai_context.py overview" in payload["read_first"]
        assert payload["task_shortcuts"] == [
            "workset",
            "before-edit",
            "failure",
        ]
        assert "workset diff" in payload["query_examples"]
        assert "project/core/" in payload["template_kernel_paths"]
        assert "project/prompts/" in payload["reference_implementation_paths"]
        assert payload["canonical_examples"]["shared_service_wiring"] == (
            "project/core/composition_root.py"
        )
        assert "tests/functional/" in payload["cold_paths"]
        assert "docs/adr/" in payload["read_last_paths"]
        assert any(
            move.startswith("Do not edit generated file directly:")
            for move in payload["forbidden_moves"]
        )

    # FUNCTION: test_query_overview_contains_query_cli_examples
    # SUMMARY: Verify the overview query exposes the CLI examples and task list used by agents.
    @pytest.mark.unit
    def test_query_overview_contains_query_cli_examples(self) -> None:
        result = _query_overview()
        payload: dict[str, Any] = result.payload

        assert payload["schema_versions"]["context_map"] == 7
        assert payload["integrity"]["status"] == "ok"
        assert payload["query_cli"]["path"] == "scripts/query_ai_context.py"
        assert "bootstrap" in payload["query_cli"]["supported_queries"]
        assert "workset" in payload["query_cli"]["shortcuts"]
        assert "workset diff" in payload["query_cli"]["supported_queries"]
        assert (
            "before-edit file project/infrastructure/api/dependencies.py"
            in payload["query_cli"]["supported_queries"]
        )
        assert "failure" in payload["query_cli"]["shortcuts"]
        assert payload["canonical_examples"]["typed_dependency_aliases"] == (
            "project/infrastructure/api/dependencies.py"
        )
        assert "project/core/logging/" in payload["cold_paths"]
        assert "CLAUDE.md" in payload["read_last_paths"]
        assert (
            "failure rule endpoint.no_depends_without_alias"
            in payload["query_cli"]["supported_queries"]
        )
        assert "add_endpoint" in payload["task_names"]
        assert "runtime_enforced" in payload["layers"]["domain"]
        # **LOGIC_STEP**: The domain's declared dependencies became a real gate on 2026-08-12, so
        # this layer reports runtime_enforced where every other layer still reports guidance. The
        # application layer is asserted alongside it to keep the contrast visible.
        assert (
            payload["layers"]["domain"]["not_enforced_in_validator"]["rule_strength"]
            == "runtime_enforced"
        )
        assert (
            payload["layers"]["application"]["not_enforced_in_validator"]["rule_strength"]
            == "not_enforced_in_validator"
        )
        assert (
            payload["enforcement_model"]["strictly_validated_rules"][0]["rule_id"]
            == "arch.domain.import_not_allowed"
        )

    # FUNCTION: test_query_before_edit_returns_pre_edit_guardrails
    # SUMMARY: Verify before-edit queries expose likely failure bundles and smallest-diff guidance for high-signal files.
    @pytest.mark.unit
    def test_query_before_edit_returns_pre_edit_guardrails(self) -> None:
        result = _query_before_edit("project/infrastructure/api/dependencies.py")
        payload: dict[str, Any] = result.payload

        assert payload["file"] == "project/infrastructure/api/dependencies.py"
        assert payload["edit_zone"] == "caution"
        assert payload["recommended_diff_style"] == "minimal-diff"
        assert "endpoint.no_depends_without_alias" in payload["watch_rules"]
        assert any(
            failure["rule_id"] == "endpoint.alias_chain_invalid"
            for failure in payload["likely_failures"]
        )
        assert payload["regeneration"]["decision"] == "refresh-ai-context"
        related = payload["related_tests"]
        assert isinstance(related, dict)
        assert "likely_unit_tests" in related
        assert "likely_integration_tests" in related

    # FUNCTION: test_every_registered_service_has_a_test_the_workset_check_can_find
    # SUMMARY: Verify no application service resolves to an empty likely_unit_tests list.
    # NOTE: This is the gate the finding needed. Its neighbours asserted the KEY existed and were
    # green while the LIST was empty for every vertical in the repository. The narrow loop that
    # consumed this mapping is gone, but the `workset` query still answers "what should I look at
    # when this file changes" — an empty answer is a silent zero, and worse than a red gate.
    @pytest.mark.unit
    def test_every_registered_service_has_a_test_the_workset_check_can_find(self) -> None:
        context_map, _, _ = query_common.context_bundle()
        registry = context_map["service_registry"]
        assert isinstance(registry, dict)

        without_tests: list[str] = []
        for service_key, metadata in registry.items():
            assert isinstance(metadata, dict)
            service_path = query_common.service_file_path(metadata)
            # **LOGIC_STEP**: Skip what has no file of its own — a service the registry knows only
            # by key cannot have a test named after it, and that is not this gate's business.
            if not service_path or not (Path(__file__).parents[2] / service_path).is_file():
                continue
            related = _tests_for(context_map, service_path)
            if not related["likely_unit_tests"]:
                without_tests.append(f"{service_key} ({service_path})")

        assert not without_tests, (
            "these services resolve to zero likely unit tests, so the workset query has nothing "
            f"to point at when they change: {without_tests}. Name the test file the way "
            ".agents/skills/add-vertical prescribes, or add the spelling to service_test_candidates()."
        )

    # FUNCTION: test_query_before_edit_shows_related_tests_for_service_file
    # SUMMARY: Verify before-edit for a service file returns guardrails and related test structure.
    @pytest.mark.unit
    def test_query_before_edit_shows_related_tests_for_service_file(self) -> None:
        result = _query_before_edit("project/core/composition_root.py")
        payload: dict[str, Any] = result.payload

        assert payload["file"] == "project/core/composition_root.py"
        assert payload["edit_zone"] in {"safe", "expert"}
        related = payload["related_tests"]
        assert isinstance(related, dict)
        assert "likely_unit_tests" in related
        assert "likely_integration_tests" in related

    # FUNCTION: test_query_before_edit_returns_derived_payload_for_zoned_file
    # SUMMARY: Verify before-edit synthesizes a derived FILE_POLICY payload for files that belong to EDIT_ZONES but lack an explicit FILE_POLICY entry.
    @pytest.mark.unit
    def test_query_before_edit_returns_derived_payload_for_zoned_file(self) -> None:
        # **LOGIC_STEP**: project/core/logging/ is the expert-zone prefix; individual files
        # under it (e.g. config.py) intentionally have no FILE_POLICY entry and exercise the
        # derived-fallback branch.
        result = _query_before_edit("project/core/logging/config.py")
        payload: dict[str, Any] = result.payload

        assert payload["file"] == "project/core/logging/config.py"
        assert payload["derived"] is True
        assert payload["edit_zone"] == "expert"
        assert payload["risk"] == "high"
        assert payload["recommended_diff_style"] == "minimal-diff"
        # Common tasks are empty in derived payload (only explicit entries declare them).
        assert payload["common_tasks"] == []
        # kernel_or_reference is heuristic — files under project/core/ are template_kernel.
        assert payload["kernel_or_reference"] == "template_kernel"

    # FUNCTION: test_query_before_edit_raises_unknown_for_path_outside_project
    # SUMMARY: Verify before-edit preserves the KeyError for paths that are not in any EDIT_ZONES classification (truly outside project tree).
    @pytest.mark.unit
    def test_query_before_edit_raises_unknown_for_path_outside_project(self) -> None:
        # **LOGIC_STEP**: A path with no zone classification must still surface the original "Unknown" KeyError.
        with pytest.raises(KeyError, match="Unknown or unindexed file policy path"):
            _query_before_edit("/tmp/does-not-exist.py")

    # FUNCTION: test_query_before_edit_unknown_path_message_lists_next_actions
    # SUMMARY: Verify the unknown-path KeyError carries actionable next-action guidance for the agent (P1 from 2026-05-12 evolution report).
    @pytest.mark.unit
    def test_query_before_edit_unknown_path_message_lists_next_actions(self) -> None:
        # **LOGIC_STEP**: The error message must name both remediation surfaces (FILE_POLICY_INDEX and EDIT_ZONES) and signpost a Next actions section so the agent does not need to read source to decide what to do.
        with pytest.raises(KeyError) as exc_info:
            _query_before_edit("/tmp/does-not-exist.py")
        message = str(exc_info.value)
        assert "Unknown or unindexed file policy path" in message
        assert "Next actions" in message
        assert "FILE_POLICY_INDEX" in message
        assert "EDIT_ZONES" in message

    # FUNCTION: test_query_before_edit_returns_full_entry_for_health_endpoint
    # SUMMARY: Verify health.py has an explicit (non-derived) FILE_POLICY entry suitable as the canonical add_endpoint reference.
    @pytest.mark.unit
    def test_query_before_edit_returns_full_entry_for_health_endpoint(self) -> None:
        # **LOGIC_STEP**: health.py is the kernel's canonical thin-endpoint reference and must have an explicit FILE_POLICY entry.
        result = _query_before_edit("project/infrastructure/api/endpoints/health.py")
        payload: dict[str, Any] = result.payload

        assert payload["file"] == "project/infrastructure/api/endpoints/health.py"
        assert payload["derived"] is False
        assert payload["edit_zone"] == "safe"
        assert "add_endpoint" in payload["common_tasks"]
        # Validators must be present and explicitly include endpoint wiring + runtime ownership.
        validators = payload["required_validators"]
        assert any("validate_endpoint_wiring.py" in cmd for cmd in validators)
        assert any("validate_runtime_ownership.py" in cmd for cmd in validators)

    # FUNCTION: test_query_workset_diff_aggregates_zones_tasks_validators_and_regeneration
    # SUMMARY: Verify a workset payload carries the aggregation an agent acts on, not only the file list.
    # NOTE: This used to go through `workset files <paths>`, a second way to reach the same handler
    # that was removed on 2026-08-14 after three months of measurement: 11 calls against 50 for
    # `workset diff`, and nothing it could answer that `diff` or `before-edit file` could not.
    @pytest.mark.unit
    def test_query_workset_diff_aggregates_zones_tasks_validators_and_regeneration(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        outputs = {
            ("git", "diff", "--name-only", "--cached"): (
                "project/infrastructure/api/dependencies.py\n"
            ),
            ("git", "diff", "--name-only"): ("project/infrastructure/api/router_registration.py\n"),
            ("git", "ls-files", "--others", "--exclude-standard"): "",
        }

        def fake_run(
            argv: list[str],
            cwd: str,
            check: bool,
            capture_output: bool,
            text: bool,
        ) -> subprocess.CompletedProcess[str]:
            del cwd, check, capture_output, text
            return subprocess.CompletedProcess(argv, 0, stdout=outputs[tuple(argv)], stderr="")

        monkeypatch.setattr(query_common.subprocess, "run", fake_run)

        result = _query_workset("diff", [])
        payload: dict[str, Any] = result.payload

        assert payload["subject_kind"] == "diff"
        assert payload["status"] == "ok"
        assert payload["changed_files"] == [
            "project/infrastructure/api/dependencies.py",
            "project/infrastructure/api/router_registration.py",
        ]
        assert "add_endpoint" in payload["matched_tasks"]
        assert "docs/ai_context_map.json" in payload["affected_generated_artifacts"]
        assert payload["regeneration"]["decision"] == "refresh-ai-context"
        assert "uv run python scripts/validate_endpoint_wiring.py" in payload["required_validators"]
        assert payload["recommended_diff_style"] == "minimal-diff"
        assert payload["read_first"][0] == "CLAUDE.md"

    # FUNCTION: test_query_workset_diff_resolves_git_worktree_changes
    # SUMMARY: Verify workset diff queries merge staged, unstaged, and untracked git paths into one aggregated payload.
    @pytest.mark.unit
    def test_query_workset_diff_resolves_git_worktree_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        outputs = {
            ("git", "diff", "--name-only", "--cached"): (
                "project/core/composition_root.py\n"
                "project/infrastructure/api/endpoints/missing_router.py\n"
            ),
            ("git", "diff", "--name-only"): (
                "project/infrastructure/api/dependencies.py\nproject/core/composition_root.py\n"
            ),
            ("git", "ls-files", "--others", "--exclude-standard"): "README.md\n",
        }

        def fake_run(
            argv: list[str],
            cwd: str,
            check: bool,
            capture_output: bool,
            text: bool,
        ) -> subprocess.CompletedProcess[str]:
            del cwd, check, capture_output, text
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=outputs[tuple(argv)],
                stderr="",
            )

        monkeypatch.setattr(query_common.subprocess, "run", fake_run)

        result = _query_workset("diff", [])
        payload: dict[str, Any] = result.payload

        assert payload["status"] == "ok"
        assert payload["changed_files"] == [
            "project/core/composition_root.py",
            "project/infrastructure/api/dependencies.py",
            "README.md",
        ]
        assert payload["deleted_files"] == [
            "project/infrastructure/api/endpoints/missing_router.py"
        ]
        assert payload["regeneration"]["decision"] == "refresh-ai-context"

    # FUNCTION: test_query_workset_diff_reports_empty_status
    # SUMMARY: Verify workset diff queries return an empty status instead of failing when the git diff is empty.
    @pytest.mark.unit
    def test_query_workset_diff_reports_empty_status(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fake_run(
            argv: list[str],
            cwd: str,
            check: bool,
            capture_output: bool,
            text: bool,
        ) -> subprocess.CompletedProcess[str]:
            del argv, cwd, check, capture_output, text
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        monkeypatch.setattr(query_common.subprocess, "run", fake_run)

        result = _query_workset("diff", [])
        payload: dict[str, Any] = result.payload

        assert payload["status"] == "empty"
        assert payload["changed_files"] == []
        assert payload["deleted_files"] == []
        assert payload["read_first"] == ["CLAUDE.md"]

    # FUNCTION: test_query_workset_diff_reports_git_repository_errors
    # SUMMARY: Verify workset diff queries surface a high-signal fallback message when git diff resolution is unavailable.
    @pytest.mark.unit
    def test_query_workset_diff_reports_git_repository_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fake_run(
            argv: list[str],
            cwd: str,
            check: bool,
            capture_output: bool,
            text: bool,
        ) -> subprocess.CompletedProcess[str]:
            del argv, cwd, check, capture_output, text
            raise subprocess.CalledProcessError(
                128,
                ["git", "diff", "--name-only"],
                stderr="fatal: not a git repository",
            )

        monkeypatch.setattr(query_common.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError) as exc_info:
            _query_workset("diff", [])

        assert "before-edit file <path>" in str(exc_info.value)

    # FUNCTION: test_query_failure_returns_endpoint_playbook
    # SUMMARY: Verify failure queries expose the shared endpoint-validator remediation protocol.
    @pytest.mark.unit
    def test_query_failure_returns_endpoint_playbook(self) -> None:
        result = _query_failure("endpoint.no_depends_without_alias")
        payload: dict[str, Any] = result.payload

        assert payload["rule_id"] == "endpoint.no_depends_without_alias"
        assert "typed alias" in payload["likely_fix_shape"]
        assert payload["smallest_command_to_rerun"] == (
            "uv run python scripts/validate_endpoint_wiring.py"
        )
        assert "project/infrastructure/api/dependencies.py" in payload["smallest_files_to_read"]
        assert "make quality-gates" in payload["next_checks"]

    # FUNCTION: test_query_failure_returns_drift_playbook
    # SUMMARY: Verify failure queries cover drift rules without forcing markdown spelunking.
    @pytest.mark.unit
    def test_query_failure_returns_drift_playbook(self) -> None:
        result = _query_failure("drift.agent_docs.outdated")
        payload: dict[str, Any] = result.payload

        assert payload["rule_id"] == "drift.agent_docs.outdated"
        assert "docs/agent_rules.md" in payload["smallest_files_to_read"]
        assert payload["smallest_command_to_rerun"] == (
            "uv run python scripts/sync_agent_docs.py --check"
        )
        assert "uv run python scripts/sync_agent_docs.py" in payload["next_checks"]

    # FUNCTION: test_query_failure_returns_runtime_ownership_playbook
    # SUMMARY: Verify failure queries expose runtime ownership remediation hints for the new validator.
    @pytest.mark.unit
    def test_query_failure_returns_runtime_ownership_playbook(self) -> None:
        result = _query_failure("runtime_ownership.env_access_restricted")
        payload: dict[str, Any] = result.payload

        assert payload["rule_id"] == "runtime_ownership.env_access_restricted"
        assert "project/core/config.py" in payload["smallest_files_to_read"]
        assert payload["smallest_command_to_rerun"] == (
            "uv run python scripts/validate_runtime_ownership.py"
        )

    # FUNCTION: test_main_reports_unknown_query_targets
    # SUMMARY: Verify the CLI exits with a non-zero code when the requested target does not exist.
    @pytest.mark.unit
    def test_main_reports_unknown_query_targets(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            ["scripts/query_ai_context.py", "failure", "rule", "nonexistent_rule_id"],
        )

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "Unknown failure rule ID" in captured.out

    # FUNCTION: test_main_renders_bootstrap_text_output
    # SUMMARY: Verify the CLI can render bootstrap output in text mode for terminal startup flows.
    @pytest.mark.unit
    def test_main_renders_bootstrap_text_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            ["scripts/query_ai_context.py", "--format", "text", "bootstrap"],
        )

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "query: bootstrap" in captured.out
        assert ("task_shortcuts: workset, before-edit, failure") in captured.out

    # FUNCTION: test_main_supports_workset_diff_command
    # SUMMARY: Verify the CLI parser and router accept the new workset diff command and render JSON output.
    @pytest.mark.unit
    def test_main_supports_workset_diff_command(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def fake_run(
            argv: list[str],
            cwd: str,
            check: bool,
            capture_output: bool,
            text: bool,
        ) -> subprocess.CompletedProcess[str]:
            del cwd, check, capture_output, text
            if argv == ["git", "diff", "--name-only", "--cached"]:
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            if argv == ["git", "diff", "--name-only"]:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout="project/infrastructure/api/dependencies.py\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(query_common.subprocess, "run", fake_run)
        monkeypatch.setattr(
            sys,
            "argv",
            ["scripts/query_ai_context.py", "workset", "diff"],
        )

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 0
        assert '"subject_kind": "diff"' in captured.out

    # FUNCTION: test_main_renders_failure_text_output
    # SUMMARY: Verify new failure queries keep the high-signal remediation summary in text mode.
    @pytest.mark.unit
    def test_main_renders_failure_text_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "scripts/query_ai_context.py",
                "--format",
                "text",
                "failure",
                "rule",
                "endpoint.no_depends_without_alias",
            ],
        )

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "query: failure" in captured.out
        assert "rerun: uv run python scripts/validate_endpoint_wiring.py" in captured.out

    # FUNCTION: test_main_reports_structured_syntax_error_without_traceback
    # SUMMARY: Verify query CLI degrades into a structured syntax error payload when AST extraction fails.
    @pytest.mark.unit
    def test_main_reports_structured_syntax_error_without_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "scripts.query_ai_context.query_context_status",
            lambda: (_ for _ in ()).throw(
                ContextBuildError(
                    ContextIssue(
                        issue_type="syntax_error",
                        path=Path("project/application/broken.py"),
                        line=4,
                        message="SyntaxError while parsing project/application/broken.py: expected ':'",
                        recommended_next_command="uv run python scripts/generate_ai_context.py --check --json",
                    )
                )
            ),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["scripts/query_ai_context.py", "bootstrap"],
        )

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 1
        assert '"degraded_status": "syntax_error"' in captured.out

    # FUNCTION: test_main_includes_generated_outdated_context_status
    # SUMMARY: Verify successful queries surface generated-artifact drift as context_status metadata.
    @pytest.mark.unit
    def test_main_includes_generated_outdated_context_status(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "scripts.query_ai_context.query_context_status",
            lambda: {
                "status": "generated_outdated",
                "issues": [
                    {
                        "issue_type": "generated_outdated",
                        "message": "Outdated generated file: docs/ai_context_map.json",
                        "recommended_next_command": "make refresh-generated-docs",
                    }
                ],
                "available_commands": ["make refresh-generated-docs"],
                "temporarily_unavailable_queries": [],
            },
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["scripts/query_ai_context.py", "bootstrap"],
        )

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 0
        assert '"context_status"' in captured.out
        assert '"status": "generated_outdated"' in captured.out


# CLASS: tests.application.test_query_ai_context.TestWorksetFindsTestsNamedAfterTheFile
# SUMMARY: Verify the narrow loop maps a changed file to the tests this repository names after it.
# NOTE: Mapping went only through the service registry and the route inventory. A changed
# repository, ORM module or middleware therefore returned `likely_tests: []` while a test named
# after it sat in tests/ — measured on a diff of eight files, one of them a repository test, which
# ran zero tests and reported "workset checks passed".
class TestWorksetFindsTestsNamedAfterTheFile:
    # FUNCTION: test_every_module_with_a_matching_test_resolves_to_it
    # SUMMARY: Verify no module in project/ has a same-named test the narrow loop would miss.
    @pytest.mark.unit
    def test_every_module_with_a_matching_test_resolves_to_it(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        context_map, _, _ = query_common.context_bundle()

        missed: list[str] = []
        for module in sorted((repo_root / "project").rglob("*.py")):
            stem = module.stem
            if stem.startswith("__"):
                continue
            for suite in ("application", "infrastructure"):
                candidate = f"tests/{suite}/test_{stem}.py"
                if not (repo_root / candidate).is_file():
                    continue
                module_path = module.relative_to(repo_root).as_posix()
                related = _tests_for(context_map, module_path)
                if candidate not in related["likely_unit_tests"]:
                    missed.append(f"{module_path} -> {candidate}")

        assert missed == [], (
            "these modules have a test named after them that the workset query would not name: "
            f"{missed}"
        )

    # FUNCTION: test_the_wiring_hotspot_resolves_to_the_suite_that_checks_wiring
    # SUMMARY: Verify service_registration.py maps to the vertical test holding TestWiring.
    @pytest.mark.unit
    def test_the_wiring_hotspot_resolves_to_the_suite_that_checks_wiring(self) -> None:
        # **LOGIC_STEP**: service_file_path() derives the module that defines the constructor, so
        # the registration file could never match it — the one file most likely to break wiring
        # was invisible to the narrow loop by construction. `source_file` is the other half.
        context_map, _, _ = query_common.context_bundle()

        related = _tests_for(context_map, "project/core/service_registration.py")

        assert related["likely_unit_tests"], "the wiring hotspot still resolves to zero tests"

    # FUNCTION: test_a_changed_migration_pulls_its_validator_and_its_ledger
    # SUMMARY: Verify an edited revision no longer returns an empty required_validators list.
    @pytest.mark.unit
    def test_a_changed_migration_pulls_its_validator_and_its_ledger(self) -> None:
        context_map, _, _ = query_common.context_bundle()
        revisions = sorted(
            (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob("*.py")
        )
        assert revisions, "no migration to test the mapping against"
        revision = revisions[0].relative_to(Path(__file__).resolve().parents[2]).as_posix()

        related = _tests_for(context_map, revision)

        assert "tests/application/test_validate_migrations.py" in related["likely_unit_tests"]
        assert any(
            "validate_migrations.py" in command for command in related["required_validators"]
        )

    # FUNCTION: test_a_changed_test_runs_itself
    # SUMMARY: Verify a diff that touches only a test still runs something.
    @pytest.mark.unit
    def test_a_changed_test_runs_itself(self) -> None:
        context_map, _, _ = query_common.context_bundle()
        changed = "tests/application/test_template_neutrality.py"

        related = _tests_for(context_map, changed)

        assert changed in related["likely_unit_tests"]

    # FUNCTION: test_functional_tests_are_never_pulled_into_the_narrow_loop
    # SUMMARY: Verify no mapping can name a Docker-and-database suite as a likely unit test.
    @pytest.mark.unit
    def test_functional_tests_are_never_pulled_into_the_narrow_loop(self) -> None:
        # **LOGIC_STEP**: pytest.ini ignores tests/functional, and naming a file there by path
        # overrides that ignore — anything acting on this mapping would try to run a suite that
        # needs Docker and a live database. Those belong to `make test-e2e`.
        repo_root = Path(__file__).resolve().parents[2]
        context_map, _, _ = query_common.context_bundle()
        paths = [
            module.relative_to(repo_root).as_posix()
            for module in sorted((repo_root / "project").rglob("*.py"))
        ]
        paths += [
            "tests/functional/src/test_reference_tasks_api.py",
            "tests/functional/src/test_reference_task_repository.py",
        ]

        leaked = [
            f"{path} -> {candidate}"
            for path in paths
            for key in ("likely_unit_tests", "likely_integration_tests")
            for candidate in _tests_for(context_map, path)[key]
            if candidate.startswith("tests/functional/")
        ]

        assert leaked == [], f"functional tests reached the narrow loop: {leaked}"


# CLASS: tests.application.test_query_ai_context.TestEveryFileOfAVerticalFindsThatVerticalsTests
# SUMMARY: Verify a vertical's files map to the tests named after the vertical, not after the file.
# NOTE: Mapping was by exact stem, so it worked only where a test happened to be spelled like the
# module. On the one vertical this template ships, `before-edit` on the domain model and on the
# endpoint module each answered with no tests at all, while four files named after that vertical
# sat in tests/ — and every vertical copied from the example would have inherited the same hole.
# The vertical names are read from docs/project_context.json rather than hard-coded, so a project
# that replaces the example is covered by this test without editing it.
class TestEveryFileOfAVerticalFindsThatVerticalsTests:
    # FUNCTION: test_a_verticals_modules_resolve_to_the_tests_named_after_it
    # SUMMARY: Verify each module of a vertical returns every runnable test bearing its name.
    @pytest.mark.unit
    def test_a_verticals_modules_resolve_to_the_tests_named_after_it(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        context_map, _, _ = query_common.context_bundle()
        verticals = json.loads(
            (repo_root / "docs" / "project_context.json").read_text(encoding="utf-8")
        )["verticals"]
        assert verticals, "no vertical declared, so this test would prove nothing"

        missed: list[str] = []
        for name in verticals:
            expected = [
                path.relative_to(repo_root).as_posix()
                for suite in ("tests/application", "tests/infrastructure")
                for path in sorted((repo_root / suite).rglob("test_*.py"))
                if f"_{name}_" in f"_{path.stem.removeprefix('test_')}_"
            ]
            if not expected:
                continue
            for module in sorted((repo_root / "project").rglob(f"{name}*.py")):
                related = _tests_for(context_map, module.relative_to(repo_root).as_posix())
                for candidate in expected:
                    if candidate not in related["likely_unit_tests"]:
                        missed.append(f"{module.relative_to(repo_root).as_posix()} -> {candidate}")

        assert missed == [], (
            "these files of a vertical do not resolve to a test named after that vertical: "
            f"{missed}"
        )

    # FUNCTION: test_a_file_named_after_no_vertical_pulls_in_no_verticals_tests
    # SUMMARY: Verify the name match cannot reach a test belonging to something else.
    # **LOGIC_STEP**: The test above can only prove recall, because it computes what it expects
    # the same way the code does. This one states the answer by hand for three kernel files whose
    # stems are ordinary English words. Before the derived name was required to be a registered
    # vertical, `context.py` — ContextVars for the logger — answered with four tests for the
    # ai_context tooling, and `dependencies.py`, one of the four wiring files, with the unit tests
    # of the dependency-pinning validator. The narrow loop runs whatever is returned, so a wrong
    # suggestion is worse than none: it reports the change as exercised.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("module", "forbidden"),
        [
            ("project/core/logging/context.py", "tests/application/test_query_ai_context.py"),
            (
                "project/infrastructure/api/dependencies.py",
                "tests/application/test_validate_dependencies.py",
            ),
            ("project/core/config.py", "tests/application/test_validate_project_context.py"),
        ],
    )
    def test_a_file_named_after_no_vertical_pulls_in_no_verticals_tests(
        self, module: str, forbidden: str
    ) -> None:
        context_map, _, _ = query_common.context_bundle()

        related = _tests_for(context_map, module)

        assert forbidden not in related["likely_unit_tests"], (
            f"{module} is named after no registered vertical, so {forbidden} — which tests "
            "something else entirely — must not be suggested for it"
        )
