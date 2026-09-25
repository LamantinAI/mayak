# FILE: tests/template/test_query_ai_context.py
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
    _query_symbol,
    _query_workset,
    main,
)


# The rules bundle tests_for_file needs to decide whether a path is finished by e2e.
def _architecture_rules() -> dict[str, Any]:
    _context_map, _change_map, architecture_rules = query_common.context_bundle()
    return architecture_rules


# Narrow the tests_for_file payload to Any so assertions can index its heterogeneous values.
def _tests_for(context_map: dict[str, Any], path: str) -> dict[str, Any]:
    return query_common.tests_for_file(context_map, path, _architecture_rules())


class TestQueryAIContext:
    @pytest.mark.unit
    def test_query_bootstrap_returns_compact_startup_payload(self) -> None:
        result = _query_bootstrap()
        payload: dict[str, Any] = result.payload

        assert payload["read_first"][0] == "AGENTS.md"
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
        assert "AGENTS.md" in payload["read_last_paths"]
        assert (
            "failure rule endpoint.no_depends_without_alias"
            in payload["query_cli"]["supported_queries"]
        )
        assert "add_endpoint" in payload["task_names"]
        assert "runtime_enforced" in payload["layers"]["domain"]
        # The domain's declared dependencies are a real gate, so this layer
        # reports runtime_enforced where every other layer still reports guidance. The
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

    # Verify no application service resolves to an empty likely_unit_tests list.
    # This is the gate the finding needed. Its neighbours asserted the KEY existed and were
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
            # Skip what has no file of its own — a service the registry knows only
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

    @pytest.mark.unit
    def test_query_before_edit_returns_derived_payload_for_zoned_file(self) -> None:
        # project/core/logging/ is the expert-zone prefix; individual files
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

    @pytest.mark.unit
    def test_query_before_edit_raises_unknown_for_path_outside_project(self) -> None:
        # A path with no zone classification must still surface the original "Unknown" KeyError.
        with pytest.raises(KeyError, match="Unknown or unindexed file policy path"):
            _query_before_edit("/tmp/does-not-exist.py")

    @pytest.mark.unit
    def test_query_before_edit_unknown_path_message_lists_next_actions(self) -> None:
        # The error message must name both remediation surfaces (FILE_POLICY_INDEX and EDIT_ZONES) and signpost a Next actions section so the agent does not need to read source to decide what to do.
        with pytest.raises(KeyError) as exc_info:
            _query_before_edit("/tmp/does-not-exist.py")
        message = str(exc_info.value)
        assert "Unknown or unindexed file policy path" in message
        assert "Next actions" in message
        assert "FILE_POLICY_INDEX" in message
        assert "EDIT_ZONES" in message

    @pytest.mark.unit
    def test_query_before_edit_returns_full_entry_for_health_endpoint(self) -> None:
        # health.py is the kernel's canonical thin-endpoint reference and must have an explicit FILE_POLICY entry.
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
        assert payload["read_first"][0] == "AGENTS.md"

    # Verify final_gate adds `make test-e2e` when the diff touches persistence.
    # docs/agent_rules.md states this rule in prose — "Finish with `make quality-gates`,
    # and with `make test-e2e` as well when the diff touched persistence, endpoints, or wiring" —
    # but final_gate was hardcoded to ["make quality-gates"] regardless of what changed_files
    # named, so `workset diff` on a persistence-only change never surfaced the one gate that runs
    # real queries against the database. requires_e2e_gate/final_gate_for_paths is the fix.
    @pytest.mark.unit
    def test_query_workset_diff_recommends_e2e_for_a_persistence_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        outputs = {
            ("git", "diff", "--name-only", "--cached"): (
                "project/infrastructure/persistence/reference_task_repository.py\n"
            ),
            ("git", "diff", "--name-only"): "",
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

        assert payload["final_gate"] == ["make quality-gates", "make test-e2e"]

    # Verify the per-file answer names the same final gate `workset diff` does.
    # `before-edit file`'s payload kept a hardcoded ["make quality-gates"], out of sync with
    # `workset diff` — the more misleading gap of the two: it is asked about one file, usually
    # immediately before that file is edited.
    @pytest.mark.unit
    def test_before_edit_on_a_persistence_file_recommends_e2e_too(self) -> None:
        context_map, _change_map, _rules = query_common.context_bundle()

        payload = _tests_for(
            context_map, "project/infrastructure/persistence/reference_task_repository.py"
        )
        docs_only = _tests_for(context_map, "docs/agent_rules.md")

        assert payload["final_gate"] == ["make quality-gates", "make test-e2e"]
        assert docs_only["final_gate"] == ["make quality-gates"]

    # Verify a revision under alembic/versions/ also asks for the gate that runs it.
    # A migration is the one change `make quality-gates` cannot check at all without a
    # database: with none reachable `scripts/validate_migrations.py` announces the skip and stays
    # green, and `make test-e2e` is the only local gate that executes the revision.
    @pytest.mark.unit
    def test_query_workset_diff_recommends_e2e_for_a_migration(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        outputs = {
            ("git", "diff", "--name-only", "--cached"): (
                "alembic/versions/9f1c2b3d4e5f_add_a_column.py\n"
            ),
            ("git", "diff", "--name-only"): "",
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

        assert payload["final_gate"] == ["make quality-gates", "make test-e2e"]

    @pytest.mark.unit
    def test_query_workset_diff_does_not_recommend_e2e_for_a_docs_only_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        outputs = {
            ("git", "diff", "--name-only", "--cached"): "README.md\n",
            ("git", "diff", "--name-only"): "",
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

        assert payload["final_gate"] == ["make quality-gates"]

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
        assert payload["read_first"] == ["AGENTS.md"]

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

    # Verify failure queries cover drift rules without forcing markdown spelunking.
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

    @pytest.mark.unit
    def test_query_failure_returns_runtime_ownership_playbook(self) -> None:
        result = _query_failure("runtime_ownership.env_access_restricted")
        payload: dict[str, Any] = result.payload

        assert payload["rule_id"] == "runtime_ownership.env_access_restricted"
        assert "project/core/config.py" in payload["smallest_files_to_read"]
        assert payload["smallest_command_to_rerun"] == (
            "uv run python scripts/validate_runtime_ownership.py"
        )

    # Verify symbol queries locate a class definition without a repo-wide grep.
    # Mapping a name to its file and line otherwise means falling back to grep, which
    # `before-edit file <path>` and `workset diff` cannot help with since both key off a file
    # path, not a name. This command is deliberately narrow: an exact `ast` name match over
    # first-party sources, not a fuzzy or substring search.
    @pytest.mark.unit
    def test_query_symbol_finds_a_class_by_exact_name(self) -> None:
        result = _query_symbol("AgentSettings")
        payload: dict[str, Any] = result.payload
        # The line is read off the file, not written down: a comment added above the class moves it.
        source = Path("project/core/config_settings_agent.py").read_text(encoding="utf-8")
        line = next(
            number
            for number, text in enumerate(source.splitlines(), start=1)
            if text.startswith("class AgentSettings")
        )

        assert payload["name"] == "AgentSettings"
        assert payload["truncated"] is False
        assert {
            "file": "project/core/config_settings_agent.py",
            "kind": "class",
            "line": line,
        } in payload["matches"]

    # Verify symbol queries locate a class-body field ("field" in the item's own wording),
    # not only a def/class — llm_mode is a pydantic Field on AgentSettings, not a function.
    @pytest.mark.unit
    def test_query_symbol_finds_a_field_by_exact_name(self) -> None:
        result = _query_symbol("llm_mode")
        payload: dict[str, Any] = result.payload

        matches = [match for match in payload["matches"] if match["kind"] == "attribute"]
        assert any(match["file"] == "project/core/config_settings_agent.py" for match in matches)

    # Verify a name bound inside a function body is not reported as a field.
    # The walk reached every scope, so `symbol result` answered with 75 matches, 24 of them
    # ordinary locals labelled `attribute` — noise, and a different claim than "function, class or
    # field". A definition is still found wherever it sits, nested helpers included; only bindings
    # are scoped.
    @pytest.mark.unit
    def test_query_symbol_ignores_a_local_variable(self) -> None:
        payload: dict[str, Any] = _query_symbol("result").payload

        assert payload["matches"] == []

    @pytest.mark.unit
    def test_query_symbol_reports_no_matches_for_an_unknown_name(self) -> None:
        result = _query_symbol("NoSuchSymbolAnywhereInTheKernel")
        payload: dict[str, Any] = result.payload

        assert payload["matches"] == []
        assert payload["truncated"] is False

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

    @pytest.mark.unit
    def test_main_supports_symbol_command(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            ["scripts/query_ai_context.py", "symbol", "AgentSettings"],
        )

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 0
        assert '"name": "AgentSettings"' in captured.out
        assert '"kind": "class"' in captured.out

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


# Verify the narrow loop maps a changed file to the tests this repository names after it.
# Mapping went only through the service registry and the route inventory. A changed
# repository, ORM module or middleware therefore returned `likely_tests: []` while a test named
# after it sat in tests/ — measured on a diff of eight files, one of them a repository test, which
# ran zero tests and reported "workset checks passed".
class TestWorksetFindsTestsNamedAfterTheFile:
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

    # Verify service_registration.py maps to the vertical test holding TestWiring.
    @pytest.mark.unit
    def test_the_wiring_hotspot_resolves_to_the_suite_that_checks_wiring(self) -> None:
        # service_file_path() derives the module that defines the constructor, so
        # the registration file could never match it — the one file most likely to break wiring
        # was invisible to the narrow loop by construction. `source_file` is the other half.
        context_map, _, _ = query_common.context_bundle()

        related = _tests_for(context_map, "project/core/service_registration.py")

        assert related["likely_unit_tests"], "the wiring hotspot still resolves to zero tests"

    @pytest.mark.unit
    def test_a_changed_migration_pulls_its_validator_and_the_schema_check(self) -> None:
        context_map, _, _ = query_common.context_bundle()
        revisions = sorted(
            (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob("*.py")
        )
        assert revisions, "no migration to test the mapping against"
        revision = revisions[0].relative_to(Path(__file__).resolve().parents[2]).as_posix()

        related = _tests_for(context_map, revision)

        assert "tests/db/test_migrations_match_models.py" in related["likely_unit_tests"]
        assert any(
            "validate_migrations.py" in command for command in related["required_validators"]
        )

    @pytest.mark.unit
    def test_a_changed_test_runs_itself(self) -> None:
        context_map, _, _ = query_common.context_bundle()
        changed = "tests/template/test_template_neutrality.py"

        related = _tests_for(context_map, changed)

        assert changed in related["likely_unit_tests"]

    @pytest.mark.unit
    def test_functional_tests_are_never_pulled_into_the_narrow_loop(self) -> None:
        # pytest.ini ignores tests/functional, and naming a file there by path
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
            "tests/functional/src/test_migration_lock.py",
        ]

        leaked = [
            f"{path} -> {candidate}"
            for path in paths
            for key in ("likely_unit_tests", "likely_integration_tests")
            for candidate in _tests_for(context_map, path)[key]
            if candidate.startswith("tests/functional/")
        ]

        assert leaked == [], f"functional tests reached the narrow loop: {leaked}"


# Verify a vertical's files map to the tests named after the vertical, not after the file.
# Mapping was by exact stem, so it worked only where a test happened to be spelled like the
# module. On the one vertical this template ships, `before-edit` on the domain model and on the
# endpoint module each answered with no tests at all, while four files named after that vertical
# sat in tests/ — and every vertical copied from the example would have inherited the same hole.
# The vertical names are read from docs/project_context.json rather than hard-coded, so a project
# that replaces the example is covered by this test without editing it.
class TestEveryFileOfAVerticalFindsThatVerticalsTests:
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

    # Verify the name match cannot reach a test belonging to something else.
    # The test above can only prove recall, because it computes what it expects
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
            ("project/core/logging/context.py", "tests/template/test_query_ai_context.py"),
            (
                "project/infrastructure/api/dependencies.py",
                "tests/template/test_validate_dependencies.py",
            ),
            ("project/core/config.py", "tests/template/test_validate_repository_metadata.py"),
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

    # Verify an agentic vertical's _agent/_mock/_tools/_verdict files resolve to its name.
    # The kernel ships no agentic vertical, so the recall test above — which globs the
    # project/ tree for files already present — never exercised these four shapes. Reproduced on
    # an agentic vertical built from this template in the 2026-09 audit: `triage_agent.py` kept
    # its suffix through vertical_names_for_path, "triage_agent" was never a registered vertical
    # name, and `workset diff`'s "each vertical file finds its own tests" gate reported no likely
    # tests for it. _LAYER_SUFFIXES in ai_query/common.py is the fix; this states the four
    # shapes by hand rather than depending on a vertical this template does not ship.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "filename",
        ["triage_agent.py", "triage_mock.py", "triage_tools.py", "triage_verdict.py"],
    )
    def test_agentic_file_shapes_strip_to_the_vertical_name(self, filename: str) -> None:
        names = query_common.vertical_names_for_path(f"project/triage/{filename}")

        assert "triage" in names, (
            f"project/triage/{filename} did not strip to 'triage' — got {names}"
        )


# Verify an edit to the shared rules source is told about both generated wrappers, not one.
# One command rewrites CLAUDE.md and AGENTS.md together, but both answers named only
# CLAUDE.md. An agent that edited docs/agent_rules.md was therefore told its edit regenerated one
# file, and a stale AGENTS.md — the file Codex reads and the one Claude Code now imports — was
# invisible to `workset`. Nothing was red, because nothing compared the answer to the Makefile's
# list. This states the pair by hand.
class TestEditingTheContractSourceNamesBothWrappers:
    @pytest.mark.unit
    @pytest.mark.parametrize("edited", ["docs/agent_rules.md", "scripts/sync_agent_docs.py"])
    def test_editing_the_shared_source_regenerates_both_wrappers(self, edited: str) -> None:
        artifacts = query_common.generated_artifacts_for_paths([edited])

        assert "AGENTS.md" in artifacts
        assert "CLAUDE.md" in artifacts

    @pytest.mark.unit
    @pytest.mark.parametrize("edited", ["AGENTS.md", "CLAUDE.md"])
    def test_a_stale_wrapper_of_either_name_asks_for_the_same_command(self, edited: str) -> None:
        assert "make refresh-agent-docs" in query_common.regeneration_targets_for_paths([edited])
