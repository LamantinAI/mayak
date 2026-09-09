# FILE: tests/application/test_generate_ai_context.py
# SUMMARY: Unit tests for the AI context map generator script.

from pathlib import Path
import sys
from typing import Any

import pytest

from ai_context.errors import ContextBuildError, ContextIssue
from ai_context.extraction import extract_service_registry_entries
from ai_context.rendering import render_json
from ai_query.common import failure_playbook
from scripts.generate_ai_context import (
    _build_integrity_report,
    build_architecture_rules,
    build_change_map,
    build_context_map,
    main,
)
from scripts.validate_architecture import get_layer_rules


# CLASS: tests.application.test_generate_ai_context.TestGenerateAIContext
# SUMMARY: Verify the AI context map exposes the expected sections and deterministic serialization.
class TestGenerateAIContext:
    # FUNCTION: test_build_context_map_contains_required_sections
    # SUMMARY: Verify the generated context map exposes all fixed top-level sections used by onboarding.
    @pytest.mark.unit
    def test_build_context_map_contains_required_sections(self) -> None:
        payload: dict[str, Any] = build_context_map()

        assert payload["schema_version"] == 7
        assert "core_entrypoints" in payload
        assert "dependency_registry" in payload
        assert "env_prefixes" in payload
        assert "file_policy_index" in payload
        assert "generated_maps" in payload
        assert "integrity" in payload
        assert "canonical_examples" in payload
        assert "cold_paths" in payload
        assert "quality_gates" in payload
        assert "quality_gates_by_concern" in payload
        assert "read_last_paths" in payload
        assert "route_inventory" in payload
        assert "router_modules" in payload
        assert "service_keys" in payload
        assert "service_registry" in payload
        assert "template_kernel_paths" in payload
        assert "reference_implementation_paths" in payload
        assert "shared" in payload["service_keys"]
        assert "vertical" in payload["service_keys"]
        assert payload["core_entrypoints"][:3] == [
            "AGENTS.md",
            "docs/architecture_rules.json",
            "scripts/query_ai_context.py",
        ]
        assert "health" in payload["router_modules"]
        shared_keys = payload["service_keys"]["shared"]
        # Core infrastructure keys must always be present in the kernel.
        for required_key in [
            "db_pool",
            "llm_service",
        ]:
            assert required_key in shared_keys, f"Missing required service key: {required_key}"
        assert payload["quality_gates"] == ["make quality-gates"]
        assert payload["quality_gates_by_concern"]["autofix"] == ["make ai-autofix"]
        assert payload["quality_gates_by_concern"]["generated_refresh"] == [
            "make refresh-ai-context",
            "make refresh-agent-docs",
            "make refresh-project-map",
            "make refresh-generated-docs",
        ]
        assert payload["quality_gates_by_concern"]["endpoint_wiring"] == [
            "uv run python scripts/validate_endpoint_wiring.py"
        ]
        assert payload["quality_gates_by_concern"]["runtime_ownership"] == [
            "uv run python scripts/validate_runtime_ownership.py"
        ]
        # **LOGIC_STEP**: There is one validation command, so there is one entry naming it. The
        # "fast" and "workset_fast" groups pointed at a middle rung measured slower than the full
        # gate and at a narrow loop that never ran mypy; both targets are gone.
        assert payload["quality_gates_by_concern"]["full"] == ["make quality-gates"]
        assert "fast" not in payload["quality_gates_by_concern"]
        assert "workset_fast" not in payload["quality_gates_by_concern"]
        assert payload["integrity"]["status"] == "ok"
        assert "docs/ai_change_map.json" in payload["generated_maps"]
        assert payload["canonical_examples"]["shared_service_wiring"] == (
            "project/core/composition_root.py"
        )
        assert "tests/functional/" in payload["cold_paths"]
        assert "docs/adr/" in payload["read_last_paths"]
        assert payload["query_cli"]["path"] == "scripts/query_ai_context.py"
        assert "workset" in payload["query_cli"]["shortcuts"]
        assert "before-edit" in payload["query_cli"]["shortcuts"]
        assert "failure" in payload["query_cli"]["shortcuts"]
        assert payload["query_cli"]["shortcuts"] == [
            "workset",
            "before-edit",
            "failure",
        ]
        assert "bootstrap" in payload["query_cli"]["supported_queries"]
        assert "workset diff" in payload["query_cli"]["supported_queries"]
        assert (
            "before-edit file project/infrastructure/api/dependencies.py"
            in payload["query_cli"]["supported_queries"]
        )
        assert (
            "failure rule endpoint.no_depends_without_alias"
            in payload["query_cli"]["supported_queries"]
        )
        assert (
            "failure rule runtime_ownership.env_access_restricted"
            in payload["query_cli"]["supported_queries"]
        )
        assert "failure rule drift.agent_docs.outdated" in payload["query_cli"]["supported_queries"]
        assert payload["file_policy_index"]["project/core/composition_root.py"]["risk"] == "high"
        assert "project/core/" in payload["template_kernel_paths"]
        assert "memory" not in payload["service_registry"]

    # FUNCTION: test_extract_services_from_regular_assignment
    # SUMMARY: Verify service extraction supports plain dict assignments used by the composition root.
    @pytest.mark.unit
    def test_extract_services_from_regular_assignment(self, tmp_path: Path) -> None:
        source_path = tmp_path / "services_assign.py"
        source_path.write_text(
            "\n".join(
                [
                    "services = {",
                    '    "alpha": object(),',
                    '    "beta": object(),',
                    "}",
                ]
            ),
            encoding="utf-8",
        )

        # **LOGIC_STEP**: Asserted against extract_service_registry_entries, the function the
        # generator actually runs — not a second, parallel extractor that no production path
        # calls, which would keep the tests green while the real walker stayed untested.
        result = extract_service_registry_entries(source_path, tmp_path, "core")

        assert sorted(result) == ["alpha", "beta"]

    # FUNCTION: test_extract_services_from_annotated_assignment
    # SUMMARY: Verify service extraction supports typed dict assignments used by vertical registries.
    @pytest.mark.unit
    def test_extract_services_from_annotated_assignment(self, tmp_path: Path) -> None:
        source_path = tmp_path / "services_annassign.py"
        source_path.write_text(
            "\n".join(
                [
                    "from typing import Any",
                    "",
                    "services: dict[str, Any] = {",
                    '    "reference_task_repository": object(),',
                    '    "reference_task_service": object(),',
                    "}",
                ]
            ),
            encoding="utf-8",
        )

        result = extract_service_registry_entries(source_path, tmp_path, "core")

        assert sorted(result) == [
            "reference_task_repository",
            "reference_task_service",
        ]

    # FUNCTION: test_extract_services_ignores_other_assignments
    # SUMMARY: Verify typed assignments to other variables do not leak into the requested service registry.
    @pytest.mark.unit
    def test_extract_services_ignores_other_assignments(self, tmp_path: Path) -> None:
        source_path = tmp_path / "services_other.py"
        source_path.write_text(
            "\n".join(
                [
                    "from typing import Any",
                    "",
                    "other: dict[str, Any] = {",
                    '    "wrong": object(),',
                    "}",
                ]
            ),
            encoding="utf-8",
        )

        result = extract_service_registry_entries(source_path, tmp_path, "core")

        assert result == {}

    # FUNCTION: test_extract_services_from_a_returned_dict_literal
    # SUMMARY: Verify a registry returned inline, with no local variable at all, is still read.
    # NOTE: `services = {...}` is the reference vertical's spelling, not a rule the language
    # enforces. An extractor recognizing only that shape misses a builder that returns the literal
    # instead: it extracts to an empty registry, which docs/ai_context_map.json then reports as a
    # project with no services — with no gate going red, because that map is generated and agrees
    # with itself. Not seen in a real project: every one built from this template so far copied
    # the reference spelling. Found by reading the extractor against the shapes an agent may write.
    @pytest.mark.unit
    def test_extract_services_from_a_returned_dict_literal(self, tmp_path: Path) -> None:
        source_path = tmp_path / "services_returned_literal.py"
        source_path.write_text(
            "\n".join(
                [
                    "from typing import Any",
                    "",
                    "def build_services() -> dict[str, Any]:",
                    "    return {",
                    '        "reference_task_service": object(),',
                    "    }",
                ]
            ),
            encoding="utf-8",
        )

        result = extract_service_registry_entries(source_path, tmp_path, "core")

        assert sorted(result) == ["reference_task_service"]

    # FUNCTION: test_extract_services_follows_a_returned_variable_of_any_name
    # SUMMARY: Verify a registry built under another name and then returned is read from the return.
    @pytest.mark.unit
    def test_extract_services_follows_a_returned_variable_of_any_name(self, tmp_path: Path) -> None:
        source_path = tmp_path / "services_returned_variable.py"
        source_path.write_text(
            "\n".join(
                [
                    "from typing import Any",
                    "",
                    "def build_services() -> dict[str, Any]:",
                    "    registry: dict[str, Any] = {",
                    '        "reference_task_service": object(),',
                    "    }",
                    "    return registry",
                ]
            ),
            encoding="utf-8",
        )

        result = extract_service_registry_entries(source_path, tmp_path, "core")

        assert sorted(result) == ["reference_task_service"]

    # FUNCTION: test_extract_services_ignores_a_returned_mapping_that_is_not_a_registry
    # SUMMARY: Verify an unrelated dict-returning helper contributes no phantom services.
    # NOTE: Reading returns rather than one variable name is what lets a differently-spelled
    # builder be found; done naively it also turns every mapping in the file — request headers,
    # an error body — into service entries. Nothing would go red: the map is generated, so it
    # agrees with itself either way. A registry is a mapping of string keys onto names, calls or
    # `None`; the helper below fails that shape on its values.
    @pytest.mark.unit
    def test_extract_services_ignores_a_returned_mapping_that_is_not_a_registry(
        self, tmp_path: Path
    ) -> None:
        source_path = tmp_path / "services_beside_a_helper.py"
        source_path.write_text(
            "\n".join(
                [
                    "from typing import Any",
                    "",
                    "def default_headers() -> dict[str, str]:",
                    "    headers = {",
                    '        "accept": "application/json",',
                    "    }",
                    "    return headers",
                    "",
                    "def build_services(dependency: Any) -> dict[str, Any]:",
                    "    services = {",
                    '        "reference_task_service": dependency,',
                    "    }",
                    "    return services",
                ]
            ),
            encoding="utf-8",
        )

        result = extract_service_registry_entries(source_path, tmp_path, "core")

        assert sorted(result) == ["reference_task_service"]

    # FUNCTION: test_extract_services_keeps_two_builders_that_share_a_variable_name_apart
    # SUMMARY: Verify a name bound in one builder cannot answer for the return of another.
    # NOTE: service_registration.py is where every vertical's builder lands, so a file with
    # several of them is the normal case, and each is free to call its local mapping the same
    # thing. Resolved file-wide, the last binding wins and the earlier vertical vanishes.
    @pytest.mark.unit
    def test_extract_services_keeps_two_builders_that_share_a_variable_name_apart(
        self, tmp_path: Path
    ) -> None:
        source_path = tmp_path / "two_builders.py"
        source_path.write_text(
            "\n".join(
                [
                    "from typing import Any",
                    "",
                    "def build_first(dependency: Any) -> dict[str, Any]:",
                    "    registry = {",
                    '        "first_service": dependency,',
                    "    }",
                    "    return registry",
                    "",
                    "def build_second(dependency: Any) -> dict[str, Any]:",
                    "    registry = {",
                    '        "second_service": dependency,',
                    "    }",
                    "    return registry",
                ]
            ),
            encoding="utf-8",
        )

        result = extract_service_registry_entries(source_path, tmp_path, "core")

        assert sorted(result) == ["first_service", "second_service"]

    # FUNCTION: test_render_json_is_deterministic
    # SUMMARY: Verify JSON rendering is stable and newline-terminated for drift checks.
    @pytest.mark.unit
    def test_render_json_is_deterministic(self) -> None:
        rendered = render_json({"b": 1, "a": ["x"]})

        assert rendered == '{\n  "a": [\n    "x"\n  ],\n  "b": 1\n}\n'

    # FUNCTION: test_build_change_map_contains_common_agent_tasks
    # SUMMARY: Verify the generated task index exposes common edit flows and the shared golden path.
    @pytest.mark.unit
    def test_build_change_map_contains_common_agent_tasks(self) -> None:
        payload: dict[str, Any] = build_change_map()

        assert payload["schema_version"] == 7
        assert payload["golden_path"] == "make quality-gates"
        assert "add_endpoint" in payload["tasks"]
        assert "add_migration" in payload["tasks"]
        assert "modify_existing_service" in payload["tasks"]
        assert "introduce_new_external_client" in payload["tasks"]
        assert payload["tasks"]["add_migration"]["entrypoint"] == (
            'make autogenerate-migration MSG="description"'
        )
        assert "add_vertical" not in payload["tasks"], (
            "Kernel ships no canonical reference vertical; remove this assertion if a vertical scaffold is added later."
        )
        assert (
            "uv run python scripts/validate_endpoint_wiring.py"
            in payload["tasks"]["add_endpoint"]["minimal_checks"]
        )
        assert payload["tasks"]["add_endpoint"]["minimal_context"]["source_files"] == [
            "project/application/dtos.py",
            "project/infrastructure/api/endpoints/health.py",
            "project/infrastructure/api/dependencies.py",
            "project/infrastructure/api/router_registration.py",
        ]
        assert (
            "service instantiated directly in endpoint"
            in payload["tasks"]["add_endpoint"]["common_mistakes"]
        )

    # FUNCTION: test_build_architecture_rules_contains_runtime_policy
    # SUMMARY: Verify machine-readable architecture rules expose layer constraints and Python version policy.
    @pytest.mark.unit
    def test_build_architecture_rules_contains_runtime_policy(self) -> None:
        payload: dict[str, Any] = build_architecture_rules()
        layer_rules: dict[str, dict[str, Any]] = get_layer_rules()
        expected_layers = {
            layer: {
                "runtime_enforced": {
                    "validator": rules["runtime_enforced"]["validator"],
                    "forbidden_imports": list(rules["runtime_enforced"]["forbidden_imports"]),
                    "allowed_imports": list(rules["runtime_enforced"]["allowed_imports"]),
                    "enforcement_notes": list(rules["runtime_enforced"]["enforcement_notes"]),
                    "rule_strength": rules["runtime_enforced"]["rule_strength"],
                },
                "guidance_only": {
                    "declared_allowed_dependencies": list(
                        rules["guidance_only"]["declared_allowed_dependencies"]
                    ),
                    "narrative_notes": list(rules["guidance_only"]["narrative_notes"]),
                    "rule_strength": rules["guidance_only"]["rule_strength"],
                },
                "not_enforced_in_validator": {
                    "declared_allowed_dependencies_are_not_whitelist_gate": rules[
                        "not_enforced_in_validator"
                    ]["declared_allowed_dependencies_are_not_whitelist_gate"],
                    "explanation": rules["not_enforced_in_validator"]["explanation"],
                    "rule_strength": rules["not_enforced_in_validator"]["rule_strength"],
                },
                "enforcement_summary": rules["enforcement_summary"],
            }
            for layer, rules in layer_rules.items()
        }

        assert payload["schema_version"] == 7
        assert payload["python_runtime_policy"]["minimum_supported"] == "3.13"
        assert payload["python_runtime_policy"]["default_local"] == "3.13"
        assert payload["quality_gates"] == ["make quality-gates"]
        assert payload["canonical_examples"]["typed_dependency_aliases"] == (
            "project/infrastructure/api/dependencies.py"
        )
        assert "project/core/logging/" in payload["cold_paths"]
        assert "AGENTS.md" in payload["read_last_paths"]
        assert payload["cbm_policy"]["optional_detail"] == [
            "attributes",
            "private helpers",
        ]
        assert "branching logic" in payload["cbm_policy"]["logic_step_when_to_use"]
        assert payload["query_cli"]["path"] == "scripts/query_ai_context.py"
        assert payload["query_cli"]["recommended_first_step"] == (
            "uv run python scripts/query_ai_context.py bootstrap"
        )
        assert (
            "uv run python scripts/query_ai_context.py bootstrap"
            in payload["query_cli"]["focused_examples"]
        )
        assert (
            "uv run python scripts/query_ai_context.py workset diff"
            in payload["query_cli"]["focused_examples"]
        )
        assert (
            "uv run python scripts/query_ai_context.py before-edit file project/infrastructure/api/dependencies.py"
            in payload["query_cli"]["focused_examples"]
        )
        assert "workset" in payload["query_cli"]["shortcuts"]
        assert "before-edit" in payload["query_cli"]["shortcuts"]
        assert "failure" in payload["query_cli"]["shortcuts"]
        assert (
            "uv run python scripts/query_ai_context.py failure rule endpoint.no_depends_without_alias"
            in payload["query_cli"]["focused_examples"]
        )
        assert payload["layers"] == expected_layers
        assert payload["enforcement_model"]["strictly_validated_rules"] == [
            {
                "layer": "domain",
                "rule_id": "arch.domain.import_not_allowed",
                "strength": "runtime_enforced",
                "validator": "scripts/validate_architecture.py",
                "scope": "allowed_import_prefixes_plus_stdlib",
            },
            {
                "layer": "application",
                "rule_id": "arch.application.no_forbidden_import",
                "strength": "runtime_enforced",
                "validator": "scripts/validate_architecture.py",
                "scope": "forbidden_import_prefixes",
            },
            {
                "layer": "infrastructure",
                "rule_id": "arch.infrastructure.no_forbidden_import",
                "strength": "runtime_enforced",
                "validator": "scripts/validate_architecture.py",
                "scope": "forbidden_import_prefixes",
            },
        ]
        # **LOGIC_STEP**: Index 0 is the domain, and its declared dependencies are not mere
        # guidance — they are the allowlist the validator enforces. Application, at index 1, still
        # declares intent nothing checks, which is what this flag exists to say.
        guidance_rules = payload["enforcement_model"]["guidance_only_rules"]
        assert guidance_rules[0]["layer"] == "domain"
        assert guidance_rules[0]["not_whitelist_gate"] is False
        assert guidance_rules[1]["layer"] == "application"
        assert guidance_rules[1]["not_whitelist_gate"] is True
        assert payload["source_of_truth"]["checks_and_validators"] == [
            "scripts/validate_architecture.py",
            "scripts/validate_endpoint_wiring.py",
            "scripts/validate_runtime_ownership.py",
            "make quality-gates",
        ]
        assert payload["source_of_truth"]["operational_query_interface"] == (
            "scripts/query_ai_context.py"
        )
        assert payload["source_of_truth"]["generated_navigation"] == [
            "docs/ai_context_map.json",
            "docs/ai_change_map.json",
        ]
        assert "docs/agent_rules.md" in payload["source_of_truth"]["fallback_docs"]
        assert "project/core/" in payload["template_kernel_paths"]
        assert "scripts/validate_runtime_ownership.py" in payload["template_kernel_paths"]
        assert "project/prompts/" in payload["reference_implementation_paths"]
        assert (
            payload["file_policy_index"]["project/infrastructure/api/dependencies.py"][
                "recommended_diff_style"
            ]
            == "minimal-diff"
        )
        assert (
            "Do not instantiate application services directly inside FastAPI endpoints."
            in payload["anti_patterns"]
        )
        assert payload["edit_zones"]["expert"] == [
            "project/core/logging/",
            "project/core/composition_root.py",
            "project/core/lifecycle.py",
            "scripts/generate_ai_context.py",
            "scripts/query_ai_context.py",
            "scripts/structure_builder.py",
            "ai_context/",
            "ai_query/",
            "scripts/validate_runtime_ownership.py",
            # **LOGIC_STEP**: The catch-all must stay last. Zone lookup takes the first matching
            # pattern, so a prefix placed above the explicit entries would swallow them; and
            # without it at all, before-edit answers `Unknown or unindexed file policy path` for
            # every script not named above.
            "scripts/",
        ]
        assert (
            payload["endpoint_wiring_contract"]["validator"]
            == "scripts/validate_endpoint_wiring.py"
        )
        assert payload["runtime_ownership_contract"]["validator"] == (
            "scripts/validate_runtime_ownership.py"
        )

    # FUNCTION: test_integrity_report_rejects_missing_dependency_getter
    # SUMMARY: Verify integrity checks fail when an alias points to a getter that does not exist.
    @pytest.mark.unit
    def test_integrity_report_rejects_missing_dependency_getter(self) -> None:
        report: dict[str, Any] = _build_integrity_report(
            service_registry={
                "business_service": {
                    "resolution_status": "resolved",
                    "construction_kind": "bound_variable",
                }
            },
            dependency_registry={
                "aliases": {
                    "BusinessServiceDep": {
                        "getter": "missing_getter",
                        "service_key": "business_service",
                    }
                },
                "getters": {},
            },
            router_modules=[],
            route_inventory={},
        )

        assert report["status"] == "error"
        assert any("missing getter" in error for error in report["errors"])
        assert report["issues"][0]["issue_type"] == "dependency_getter_missing"

    # FUNCTION: test_integrity_report_rejects_missing_service_key_references
    # SUMMARY: Verify integrity checks fail when getters or route dependencies point at absent services.
    @pytest.mark.unit
    def test_integrity_report_rejects_missing_service_key_references(self) -> None:
        report: dict[str, Any] = _build_integrity_report(
            service_registry={},
            dependency_registry={
                "aliases": {
                    "BusinessServiceDep": {
                        "getter": "get_business_service",
                        "service_key": "business_service",
                    }
                },
                "getters": {
                    "get_business_service": {
                        "service_key": "business_service",
                        "service_type": "BusinessService",
                        "service_module": "project.application.business_service.BusinessService",
                    }
                },
            },
            router_modules=["business"],
            route_inventory={
                "business": {
                    "endpoints": [
                        {
                            "dependencies": [
                                {
                                    "alias": "BusinessServiceDep",
                                    "service_key": "business_service",
                                }
                            ]
                        }
                    ]
                }
            },
        )

        assert report["status"] == "error"
        assert any("missing service key" in error for error in report["errors"])

    # FUNCTION: test_integrity_report_rejects_missing_router_inventory
    # SUMMARY: Verify integrity checks fail when an imported router module is absent from the extracted route inventory.
    @pytest.mark.unit
    def test_integrity_report_rejects_missing_router_inventory(self) -> None:
        report: dict[str, Any] = _build_integrity_report(
            service_registry={},
            dependency_registry={"aliases": {}, "getters": {}},
            router_modules=["missing_router"],
            route_inventory={},
        )

        assert report["status"] == "error"
        assert any("missing from route_inventory" in error for error in report["errors"])

    # FUNCTION: test_main_check_mode_fails_when_output_file_is_missing
    # SUMMARY: Verify check mode reports drift when one of the generated output files does not exist.
    @pytest.mark.unit
    def test_main_check_mode_fails_when_output_file_is_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        missing_output = tmp_path / "missing.json"
        monkeypatch.setattr("scripts.generate_ai_context.OUTPUT_PATH", missing_output)
        monkeypatch.setattr(
            "scripts.generate_ai_context.CHANGE_MAP_OUTPUT_PATH",
            tmp_path / "change_map.json",
        )
        monkeypatch.setattr(
            "scripts.generate_ai_context.ARCHITECTURE_RULES_OUTPUT_PATH",
            tmp_path / "architecture_rules.json",
        )
        monkeypatch.setattr(sys, "argv", ["scripts/generate_ai_context.py", "--check"])

        exit_code = main()

        assert exit_code == 1

    # FUNCTION: test_main_check_mode_json_reports_structured_drift_issue
    # SUMMARY: Verify JSON check mode emits stable remediation metadata for generated-artifact drift.
    @pytest.mark.unit
    def test_main_check_mode_json_reports_structured_drift_issue(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        missing_output = tmp_path / "missing.json"
        monkeypatch.setattr("scripts.generate_ai_context.OUTPUT_PATH", missing_output)
        monkeypatch.setattr(
            "scripts.generate_ai_context.CHANGE_MAP_OUTPUT_PATH",
            tmp_path / "change_map.json",
        )
        monkeypatch.setattr(
            "scripts.generate_ai_context.ARCHITECTURE_RULES_OUTPUT_PATH",
            tmp_path / "architecture_rules.json",
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["scripts/generate_ai_context.py", "--check", "--json"],
        )

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 1
        assert '"rule_id": "drift.generated.missing"' in captured.out
        assert '"suggested_fix"' in captured.out

    # FUNCTION: test_main_json_reports_structured_syntax_error
    # SUMMARY: Verify syntax failures in AST extraction degrade into a machine-readable error instead of a traceback.
    @pytest.mark.unit
    def test_main_json_reports_structured_syntax_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "scripts.generate_ai_context._build_generated_outputs",
            lambda: (_ for _ in ()).throw(
                ContextBuildError(
                    ContextIssue(
                        issue_type="syntax_error",
                        path=Path("project/application/broken.py"),
                        line=7,
                        message="SyntaxError while parsing project/application/broken.py: invalid syntax",
                        recommended_next_command="uv run python scripts/generate_ai_context.py --check --json",
                    )
                )
            ),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["scripts/generate_ai_context.py", "--check", "--json"],
        )

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 1
        assert '"degraded_status": "syntax_error"' in captured.out
        assert '"issue_type": "syntax_error"' in captured.out

    # FUNCTION: test_text_check_mode_prints_a_resolvable_failure_rule_id
    # SUMMARY: Verify the text output names a rule_id that `failure rule` can actually resolve.
    @pytest.mark.unit
    def test_text_check_mode_prints_a_resolvable_failure_rule_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # **LOGIC_STEP**: the validator-recovery rule in docs/agent_rules.md tells the agent to run `failure rule <rule_id>` with
        # what it just read. The only identifier the text output carried was degraded_status —
        # "generated_outdated", a layer name — and the registered rule is
        # "drift.generated.outdated", so the prescribed next command answered "Unknown failure
        # rule ID". This asserts the printed token resolves, not that it equals a literal.
        monkeypatch.setattr("scripts.generate_ai_context.OUTPUT_PATH", tmp_path / "missing.json")
        monkeypatch.setattr(
            "scripts.generate_ai_context.CHANGE_MAP_OUTPUT_PATH",
            tmp_path / "change_map.json",
        )
        monkeypatch.setattr(
            "scripts.generate_ai_context.ARCHITECTURE_RULES_OUTPUT_PATH",
            tmp_path / "architecture_rules.json",
        )
        monkeypatch.setattr(sys, "argv", ["scripts/generate_ai_context.py", "--check"])

        exit_code = main()

        captured = capsys.readouterr()
        printed_rules = [
            line.split("rule:", 1)[1].strip()
            for line in captured.out.splitlines()
            if line.strip().startswith("rule:")
        ]
        assert exit_code == 1
        assert printed_rules, f"text output named no rule_id:\n{captured.out}"
        for rule_id in printed_rules:
            assert failure_playbook(rule_id)["rule_id"] == rule_id

    # FUNCTION: test_text_mode_syntax_error_survives_a_payload_without_a_rule_id
    # SUMMARY: Verify the rule line is skipped, not crashed on, for issues that carry issue_type only.
    @pytest.mark.unit
    def test_text_mode_syntax_error_survives_a_payload_without_a_rule_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # **LOGIC_STEP**: ContextIssue.to_payload has no rule_id — only the validator-contract
        # payloads do. Printing it unconditionally would turn a reported syntax error into a
        # KeyError traceback, which is the failure mode the degraded payload exists to avoid.
        monkeypatch.setattr(
            "scripts.generate_ai_context._build_generated_outputs",
            lambda: (_ for _ in ()).throw(
                ContextBuildError(
                    ContextIssue(
                        issue_type="syntax_error",
                        path=Path("project/application/broken.py"),
                        line=7,
                        message="SyntaxError while parsing project/application/broken.py",
                        recommended_next_command="uv run python scripts/generate_ai_context.py --check",
                    )
                )
            ),
        )
        monkeypatch.setattr(sys, "argv", ["scripts/generate_ai_context.py", "--check"])

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "status: syntax_error" in captured.out
        assert "rule:" not in captured.out
