# FILE: tests/application/test_doctor_ai_context.py
# SUMMARY: Unit tests for the AI-context doctor entrypoint.

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest


from ai_context.errors import ContextBuildError, ContextIssue
from scripts.doctor_ai_context import (
    LAYER_UNAVAILABLE_RULE_ID,
    _migrations_not_verified_notice,
    diagnose,
    diagnose_full,
    main,
    unavailable_validator_payload,
)
from scripts.doctor_layers import (
    SECURITY_LAYER,
    LATE_LAYER_NAMES,
    REENTRY_ENV_VAR,
    TESTS_LAYER,
    TOOL_LAYERS,
    ToolLayer,
    diagnose_early_layers,
    diagnose_tool_layer,
    get_doctor_layer_playbook,
)
from scripts.validate_architecture import ArchitectureIssue
from scripts.validate_cbm import ValidationIssue
from scripts.validate_endpoint_wiring import EndpointWiringIssue
from scripts.validate_module_sizes import ModuleSizeIssue

_REPO_ROOT = Path(__file__).resolve().parents[2]


# FUNCTION: _no_subprocess_layers
# SUMMARY: Keep every test in this module from shelling out to the gate targets.
# NOTE: The tests layer runs this very suite. Without the sentinel, one `diagnose_full()` here
# would spawn a full pytest run, which would reach this module again. The env var is the same one
# the doctor sets on its own children, so this fixture exercises the real guard rather than a
# test-only shortcut. diagnose_late_layers is stubbed as well so the assertions about main()'s
# printing do not depend on whether the working tree happens to be clean.
@pytest.fixture(autouse=True)
def _no_subprocess_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REENTRY_ENV_VAR, "1")
    monkeypatch.setattr(
        "scripts.doctor_ai_context.diagnose_late_layers",
        lambda: (None, ()),
    )


# CLASS: tests.application.test_doctor_ai_context.TestDoctorAIContext
# SUMMARY: Verify the doctor entrypoint reports the first blocking issue class in priority order.
class TestDoctorAIContext:
    # FUNCTION: test_diagnose_reports_syntax_error_first
    # SUMMARY: Verify syntax failures in context extraction short-circuit later validators.
    @pytest.mark.unit
    def test_diagnose_reports_syntax_error_first(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "scripts.doctor_ai_context._build_generated_outputs",
            lambda: (_ for _ in ()).throw(
                ContextBuildError(
                    ContextIssue(
                        issue_type="syntax_error",
                        path=Path("project/application/broken.py"),
                        line=9,
                        message="SyntaxError while parsing project/application/broken.py: invalid syntax",
                        recommended_next_command="uv run python generate_ai_context.py --check --json",
                    )
                )
            ),
        )

        payload: dict[str, Any] = diagnose()

        assert payload["degraded_status"] == "syntax_error"
        assert payload["issues"][0]["issue_type"] == "syntax_error"

    # FUNCTION: test_diagnose_reports_architecture_before_other_validators
    # SUMMARY: Verify the doctor returns the first architecture issue before endpoint/runtime/cbm checks.
    @pytest.mark.unit
    def test_diagnose_reports_architecture_before_other_validators(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("scripts.doctor_ai_context._build_generated_outputs", lambda: {})
        monkeypatch.setattr(
            "scripts.doctor_ai_context.generated_output_issues", lambda _outputs: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.build_context_map",
            lambda: {"integrity": {"status": "ok", "issues": []}},
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_architecture_issues",
            lambda _repo_root: [
                ArchitectureIssue(
                    path=Path("project/infrastructure/bad_module.py"),
                    line=5,
                    message="Infrastructure layer must not import 'project.core.composition_root'.",
                    rule_id="arch.infrastructure.no_forbidden_import",
                )
            ],
        )

        payload: dict[str, Any] = diagnose()

        assert payload["status"] == "error"
        assert payload["blocking_layer"] == "architecture"
        assert payload["issues"][0]["rule_id"] == "arch.infrastructure.no_forbidden_import"

    # FUNCTION: test_diagnose_reports_generated_outdated_before_validators
    # SUMMARY: Verify outdated generated artifacts are reported before running deeper validators.
    @pytest.mark.unit
    def test_diagnose_reports_generated_outdated_before_validators(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("scripts.doctor_ai_context._build_generated_outputs", lambda: {})
        monkeypatch.setattr(
            "scripts.doctor_ai_context.generated_output_issues",
            lambda _outputs: [
                {
                    "rule_id": "drift.generated.outdated",
                    "category": "drift",
                    "file": "docs/ai_context_map.json",
                    "line": 1,
                    "message": "Outdated generated file: docs/ai_context_map.json",
                }
            ],
        )

        payload: dict[str, Any] = diagnose()

        assert payload["status"] == "error"
        assert payload["degraded_status"] == "generated_outdated"
        assert payload["issues"][0]["recommended_next_command"] == "make refresh-generated-docs"

    # FUNCTION: test_diagnose_reports_integrity_error
    # SUMMARY: Verify the doctor catches integrity errors in service/dependency wiring.
    @pytest.mark.unit
    def test_diagnose_reports_integrity_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("scripts.doctor_ai_context._build_generated_outputs", lambda: {})
        monkeypatch.setattr(
            "scripts.doctor_ai_context.generated_output_issues", lambda _outputs: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.build_context_map",
            lambda: {
                "integrity": {
                    "status": "error",
                    "issues": [
                        {
                            "issue_type": "service_key_missing",
                            "message": "Dependency getter references unknown service key 'missing_svc'.",
                            "repair_protocol": ["Check service_registration.py"],
                            "recommended_next_command": "make refresh-ai-context",
                        }
                    ],
                }
            },
        )

        payload: dict[str, Any] = diagnose()

        assert payload["status"] == "error"
        assert payload["degraded_status"] == "integrity_error"

    # FUNCTION: test_diagnose_reports_endpoint_wiring_error
    # SUMMARY: Verify the doctor catches endpoint wiring violations after architecture passes.
    @pytest.mark.unit
    def test_diagnose_reports_endpoint_wiring_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("scripts.doctor_ai_context._build_generated_outputs", lambda: {})
        monkeypatch.setattr(
            "scripts.doctor_ai_context.generated_output_issues", lambda _outputs: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.build_context_map",
            lambda: {"integrity": {"status": "ok", "issues": []}},
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_architecture_issues", lambda _root: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_endpoint_wiring_issues",
            lambda _root: [
                EndpointWiringIssue(
                    path=Path("project/infrastructure/api/endpoints/bad.py"),
                    line=10,
                    rule_id="endpoint.no_direct_service_import",
                    message="Endpoint imports service directly.",
                )
            ],
        )

        payload: dict[str, Any] = diagnose()

        assert payload["status"] == "error"
        assert payload["blocking_layer"] == "endpoint_wiring"
        assert payload["issues"][0]["rule_id"] == "endpoint.no_direct_service_import"

    # FUNCTION: test_diagnose_reports_cbm_error
    # SUMMARY: Verify the doctor catches CBM annotation issues and reports a rule_id derived from the message (no longer the legacy literal cbm.strict_core_missing_metadata).
    @pytest.mark.unit
    def test_diagnose_reports_cbm_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("scripts.doctor_ai_context._build_generated_outputs", lambda: {})
        monkeypatch.setattr(
            "scripts.doctor_ai_context.generated_output_issues", lambda _outputs: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.build_context_map",
            lambda: {"integrity": {"status": "ok", "issues": []}},
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_architecture_issues", lambda _root: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_endpoint_wiring_issues", lambda _root: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_runtime_ownership_issues",
            lambda _root: [],
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_validation_issues",
            lambda _root: [
                ValidationIssue(
                    path=Path("project/application/my_service.py"),
                    line=1,
                    # **LOGIC_STEP**: Use a real CBM error message so classify_issue routes to the matching rule_id.
                    message="Missing '# FILE:' tag",
                )
            ],
        )

        payload: dict[str, Any] = diagnose()

        assert payload["status"] == "error"
        assert payload["blocking_layer"] == "cbm"
        # **LOGIC_STEP**: rule_id is derived from the message, not a hard-coded literal.
        assert payload["issues"][0]["rule_id"] == "cbm.missing_file_tag"

    # FUNCTION: test_diagnose_reports_cbm_error_rule_id_is_real
    # SUMMARY: Regression guard for the historical bug where doctor reported the fake rule_id "cbm.strict_core_missing_metadata" (not in _CBM_RULE_MAP) for every CBM issue.
    @pytest.mark.unit
    def test_diagnose_reports_cbm_error_rule_id_is_real(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from scripts.validate_cbm import _CBM_RULE_MAP

        monkeypatch.setattr("scripts.doctor_ai_context._build_generated_outputs", lambda: {})
        monkeypatch.setattr(
            "scripts.doctor_ai_context.generated_output_issues", lambda _outputs: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.build_context_map",
            lambda: {"integrity": {"status": "ok", "issues": []}},
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_architecture_issues", lambda _root: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_endpoint_wiring_issues", lambda _root: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_runtime_ownership_issues",
            lambda _root: [],
        )
        # **LOGIC_STEP**: Even when the message does NOT match a known CBM keyword,
        # the doctor must NOT emit the legacy fake rule_id. classify_issue returns
        # "cbm.unknown" as the canonical fallback for unmatched messages.
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_validation_issues",
            lambda _root: [
                ValidationIssue(
                    path=Path("project/application/my_service.py"),
                    line=1,
                    message="Missing file-level CBM annotation.",
                )
            ],
        )

        payload: dict[str, Any] = diagnose()

        rule_id = payload["issues"][0]["rule_id"]
        real_rule_ids = {entry[1] for entry in _CBM_RULE_MAP} | {"cbm.unknown"}
        assert rule_id in real_rule_ids
        assert rule_id != "cbm.strict_core_missing_metadata"

    # FUNCTION: test_diagnose_reports_module_size_error
    # SUMMARY: Verify the doctor catches oversized modules after all other validators pass.
    @pytest.mark.unit
    def test_diagnose_reports_module_size_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("scripts.doctor_ai_context._build_generated_outputs", lambda: {})
        monkeypatch.setattr(
            "scripts.doctor_ai_context.generated_output_issues", lambda _outputs: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.build_context_map",
            lambda: {"integrity": {"status": "ok", "issues": []}},
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_architecture_issues", lambda _root: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_endpoint_wiring_issues", lambda _root: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_runtime_ownership_issues",
            lambda _root: [],
        )
        monkeypatch.setattr("scripts.doctor_ai_context.collect_validation_issues", lambda _root: [])
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_module_size_issues",
            lambda _root: [
                ModuleSizeIssue(
                    path=Path("project/application/huge.py"),
                    line_count=600,
                )
            ],
        )

        payload: dict[str, Any] = diagnose()

        assert payload["status"] == "error"
        assert payload["blocking_layer"] == "module_size"
        # **LOGIC_STEP**: The budget is stated in code lines, and the message says so explicitly —
        # an agent reading it must not conclude that deleting comments will bring the file under.
        # The wording comes from the issue itself, so the doctor cannot drift from the validator.
        assert "600 code lines" in payload["issues"][0]["message"]
        assert "comments do not count" in payload["issues"][0]["message"]

    # FUNCTION: test_diagnose_returns_ok_when_all_layers_pass
    # SUMMARY: Verify the doctor reports ok status with all checked layers when everything passes.
    @pytest.mark.unit
    def test_diagnose_returns_ok_when_all_layers_pass(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("scripts.doctor_ai_context._build_generated_outputs", lambda: {})
        monkeypatch.setattr(
            "scripts.doctor_ai_context.generated_output_issues", lambda _outputs: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.build_context_map",
            lambda: {"integrity": {"status": "ok", "issues": []}},
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_architecture_issues", lambda _root: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_endpoint_wiring_issues", lambda _root: []
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_runtime_ownership_issues",
            lambda _root: [],
        )
        monkeypatch.setattr("scripts.doctor_ai_context.collect_validation_issues", lambda _root: [])
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_module_size_issues", lambda _root: []
        )
        # **LOGIC_STEP**: Stub the layers added by the validator-recovery bundle so the
        # OK path is reachable in this isolated test.
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_skills_frontmatter_issues",
            lambda _root: [],
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_project_context_issues",
            lambda _root: [],
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_migration_issues",
            lambda _root: [],
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.collect_file_policy_issues",
            lambda _root: [],
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context._diagnose_drift_layer",
            lambda **kwargs: None,
        )

        payload: dict[str, Any] = diagnose()

        assert payload["status"] == "ok"
        assert "context" in payload["checked_layers"]
        assert "module_size" in payload["checked_layers"]
        assert "project_context" in payload["checked_layers"]
        assert "file_policy" in payload["checked_layers"]
        assert "agent_docs_drift" in payload["checked_layers"]
        assert payload["final_gate"] == "make quality-gates"

    # FUNCTION: test_main_renders_text_error_output
    # SUMMARY: Verify the doctor CLI renders human-readable error output with blocking layer info.
    @pytest.mark.unit
    def test_main_renders_text_error_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "scripts.doctor_ai_context.diagnose",
            lambda: {
                "status": "error",
                "blocking_layer": "cbm",
                "issues": [
                    {
                        "message": "Missing CBM annotation.",
                        "recommended_next_command": "uv run python validate_cbm.py",
                    }
                ],
            },
        )
        monkeypatch.setattr(sys, "argv", ["scripts/doctor_ai_context.py"])

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "blocking_layer: cbm" in captured.out
        assert "next: uv run python validate_cbm.py" in captured.out

    # FUNCTION: test_main_renders_json_output
    # SUMMARY: Verify the doctor CLI supports machine-readable JSON output.
    @pytest.mark.unit
    def test_main_renders_json_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "scripts.doctor_ai_context.diagnose",
            lambda: {
                "status": "ok",
                "checked_layers": ["context"],
                "final_gate": "make quality-gates",
            },
        )
        monkeypatch.setattr(sys, "argv", ["scripts/doctor_ai_context.py", "--json"])

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 0
        assert '"status": "ok"' in captured.out


# CLASS: tests.application.test_doctor_ai_context.TestExtendedCheckedLayers
# SUMMARY: Verify the doctor reports the new validator layers added by the validator-recovery bundle.
class TestExtendedCheckedLayers:
    # FUNCTION: test_checked_layers_include_new_layers
    # SUMMARY: Run doctor against the live repo and assert the four new layers are present in the OK payload.
    @pytest.mark.unit
    def test_checked_layers_include_new_layers(self) -> None:
        from scripts.doctor_ai_context import diagnose

        payload: dict[str, Any] = diagnose()
        # When the live repo is clean we expect status=ok with the extended layer list.
        # If anything else (drift, integrity error) is reported we still expect the
        # blocking_layer to be one of the layers we registered — i.e. the doctor
        # never returns a layer not in the canonical set.
        if payload.get("status") == "ok":
            layers = set(payload.get("checked_layers", []))
            assert {
                "project_context",
                "migrations",
                "file_policy",
                "agent_docs_drift",
                "project_map_drift",
            }.issubset(layers)
        else:
            blocking = payload.get("blocking_layer")
            assert blocking in {
                "context",
                "architecture",
                "endpoint_wiring",
                "runtime_ownership",
                "cbm",
                "module_size",
                "skills_frontmatter",
                "project_context",
                "migrations",
                "file_policy",
                "agent_docs_drift",
                "project_map_drift",
                "generated_outdated",
                "integrity_error",
                # degraded_query_payload may surface a status string here too:
                "context_build_error",
            }, f"Unexpected blocking_layer: {blocking!r}"


# CLASS: tests.application.test_doctor_ai_context.TestGateLayersAreModelled
# SUMMARY: Verify the doctor can name a blocking layer for every step `make quality-gates` runs.
# NOTE: The doctor answered "doctor status: ok" on a red suite, twice, on two different failures.
# It modelled 14 of the 23 steps and the nine it missed included the test run, mypy and ruff —
# while ARCHITECTURE.md and CLAUDE.md both say to run it first to find the blocking layer.
class TestGateLayersAreModelled:
    # FUNCTION: _quality_gate_steps
    # SUMMARY: Read the commands `quality-gates` runs straight out of the Makefile.
    # OUTPUT: (list[str]): One entry per recipe line, sub-make lines resolved to their target name.
    # NOTE: The steps live in `quality-gates-steps`. `quality-gates` itself is two lines — run the
    # steps, and on failure run the doctor — because the doctor used to hide behind a second target
    # name that the rules told everyone to prefer over the one they would type by habit.
    @staticmethod
    def _quality_gate_steps() -> list[str]:
        makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        recipe = makefile.split("\nquality-gates-steps:", 1)[1].split("\n\n", 1)[0]
        steps: list[str] = []
        for line in recipe.splitlines():
            if not line.startswith("\t"):
                continue
            steps.append(line.strip().lstrip("@"))
        return steps

    # FUNCTION: test_every_quality_gates_step_maps_to_a_doctor_layer
    # SUMMARY: Verify each gate step maps to one named doctor layer, and no reported layer is idle.
    @pytest.mark.unit
    def test_every_quality_gates_step_maps_to_a_doctor_layer(self) -> None:
        # **LOGIC_STEP**: One explicit step -> layer pair, not a bag of candidates. The first
        # version of this test built a candidate set that unconditionally included five drift
        # layer names for ANY step naming a script, so a step mapped to the wrong layer — or to a
        # layer that never runs — still passed. A near-tautological guard over a defect about
        # unnoticed gaps is the same defect one level up.
        step_to_layer = {
            "$(MAKE) --no-print-directory gate-lockfile": "lockfile",
            "$(MAKE) --no-print-directory gate-lint": "lint",
            "$(MAKE) --no-print-directory gate-format": "format",
            "$(MAKE) --no-print-directory gate-types": "types",
            "$(MAKE) --no-print-directory gate-tests": "tests",
            "$(UV) run python scripts/validate_cbm.py": "cbm",
            "$(UV) run python scripts/validate_architecture.py": "architecture",
            "$(UV) run python scripts/validate_endpoint_wiring.py": "endpoint_wiring",
            "$(UV) run python scripts/validate_runtime_ownership.py": "runtime_ownership",
            "$(UV) run python scripts/validate_migrations.py": "migrations",
            "$(UV) run python scripts/validate_module_sizes.py": "module_size",
            "$(UV) run python scripts/validate_test_quality.py": "test_quality",
            "$(UV) run python scripts/validate_dependencies.py": "dependencies",
            "$(UV) run python scripts/validate_skills_frontmatter.py": "skills_frontmatter",
            "$(UV) run python scripts/validate_project_context.py": "project_context",
            "$(UV) run python scripts/validate_file_policy.py": "file_policy",
            "$(UV) run python scripts/validate_script_paths.py": "script_paths",
            "$(UV) run python scripts/validate_secrets.py": "secrets",
            "$(MAKE) --no-print-directory security-scan": "security",
            "$(UV) run python scripts/structure_builder.py --check": "project_map_drift",
            "$(UV) run python scripts/generate_ai_context.py --check": "context",
            "$(UV) run python scripts/sync_agent_docs.py --check": "agent_docs_drift",
        }
        steps = self._quality_gate_steps()
        payload: dict[str, Any] = diagnose()
        if payload["status"] != "ok":
            pytest.skip("working tree is not clean enough to read an OK payload")
        # **LOGIC_STEP**: Layer names the doctor genuinely reports — a live payload plus the
        # subprocess layers this module's fixture suppresses.
        layers = set(payload.get("checked_layers", []))
        layers.update(layer.name for layer in TOOL_LAYERS)
        layers.update(LATE_LAYER_NAMES)

        unlisted = [step for step in steps if step not in step_to_layer]
        assert unlisted == [], (
            "quality-gates grew a step this mapping does not cover — add it here and give the "
            f"doctor a layer for it: {unlisted}"
        )
        needed = {step_to_layer[step] for step in steps}
        assert sorted(needed - layers) == [], (
            f"steps whose layer the doctor never reports: {sorted(needed - layers)}"
        )
        # **LOGIC_STEP**: The other direction. A layer the doctor reports that no gate step needs
        # is either dead weight or a sign the gate quietly lost a step.
        assert sorted(layers - needed) == [], (
            f"the doctor reports layers no quality-gates step corresponds to: {sorted(layers - needed)}"
        )

    # FUNCTION: test_tool_layer_reports_the_failing_target_with_a_resolvable_rule
    # SUMMARY: Verify a failing gate target becomes a payload whose rule_id has a playbook.
    @pytest.mark.unit
    def test_tool_layer_reports_the_failing_target_with_a_resolvable_rule(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess as _subprocess

        class _Failed:
            returncode = 2
            stdout = "make[1]: Entering directory\nsome/file.py:3: error: bad type\n"
            stderr = ""

        monkeypatch.delenv(REENTRY_ENV_VAR, raising=False)
        monkeypatch.setattr(_subprocess, "run", lambda *a, **k: _Failed())
        layer = ToolLayer("types", "gate-types", "gate.types.failed", "pyproject.toml")

        payload: dict[str, Any] | None = diagnose_tool_layer(layer)

        assert payload is not None
        assert payload["blocking_layer"] == "types"
        # **LOGIC_STEP**: make's own framing must not become the diagnosis.
        assert payload["issues"][0]["message"] == "some/file.py:3: error: bad type"
        assert get_doctor_layer_playbook(str(payload["issues"][0]["rule_id"])) is not None

    # FUNCTION: test_the_security_layer_reports_the_bandit_finding
    # SUMMARY: Verify the security layer reports bandit's issue line, not the noise around it.
    # NOTE: bandit prints a `[tester] WARNING nosec encountered ...` line for each suppression it
    # meets, before any finding. That line matches none of the other diagnostic shapes, so it was
    # the first candidate and would have been reported as the diagnosis — sending the reader to a
    # suppression comment instead of to the query that failed.
    @pytest.mark.unit
    def test_the_security_layer_reports_the_bandit_finding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess as _subprocess

        class _Failed:
            returncode = 1
            stdout = (
                "[tester]\tWARNING\tnosec encountered (B608), but no failed test on file a.py:46\n"
                ">> Issue: [B608:hardcoded_sql_expressions] Possible SQL injection vector.\n"
                "   Location: project/infrastructure/persistence/queries.py:71:4\n"
            )
            stderr = ""

        monkeypatch.delenv(REENTRY_ENV_VAR, raising=False)
        monkeypatch.setattr(_subprocess, "run", lambda *a, **k: _Failed())

        payload: dict[str, Any] | None = diagnose_tool_layer(SECURITY_LAYER)

        assert payload is not None
        assert payload["blocking_layer"] == "security"
        assert payload["issues"][0]["message"].startswith(">> Issue: [B608")
        assert get_doctor_layer_playbook(str(payload["issues"][0]["rule_id"])) is not None

    # FUNCTION: test_every_tool_layer_rule_id_resolves_through_failure_playbook
    # SUMMARY: Verify a printed rule_id is one `failure rule` can answer.
    @pytest.mark.unit
    @pytest.mark.parametrize("layer", [*TOOL_LAYERS, TESTS_LAYER], ids=lambda layer: layer.name)
    def test_every_tool_layer_rule_id_resolves_through_failure_playbook(
        self, layer: ToolLayer
    ) -> None:
        from ai_query.common import failure_playbook

        assert failure_playbook(layer.rule_id)["rule_id"] == layer.rule_id

    # FUNCTION: test_the_unavailable_layer_rule_id_resolves_too
    # SUMMARY: Regression guard: the parametrised test above covers gate layers only, so a rule id the doctor can print outside them needs its own case or it reaches `failure rule` as a KeyError.
    @pytest.mark.unit
    def test_the_unavailable_layer_rule_id_resolves_too(self) -> None:
        from ai_query.common import failure_playbook

        playbook = failure_playbook(LAYER_UNAVAILABLE_RULE_ID)

        assert playbook["rule_id"] == LAYER_UNAVAILABLE_RULE_ID
        assert playbook["likely_fix_shape"]

    # FUNCTION: test_a_gate_layer_payload_carries_the_fix_shape
    # SUMMARY: Regression guard: the gate payload dropped `likely_fix_shape`, so the doctor answered a failed `make gate-format` with "run make gate-format" and kept "run make ai-autofix" to itself.
    @pytest.mark.unit
    def test_a_gate_layer_payload_carries_the_fix_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess as _subprocess

        class _Failed:
            returncode = 1
            stdout = "unformatted: File would be reformatted\n"
            stderr = ""

        monkeypatch.delenv(REENTRY_ENV_VAR, raising=False)
        monkeypatch.setattr(_subprocess, "run", lambda *a, **k: _Failed())
        layer = ToolLayer("format", "gate-format", "gate.format.failed", "pyproject.toml")

        payload: dict[str, Any] | None = diagnose_tool_layer(layer)

        assert payload is not None
        issue = payload["issues"][0]
        assert "ai-autofix" in str(issue["likely_fix_shape"])

    # FUNCTION: test_reentry_sentinel_disables_the_subprocess_layers
    # SUMMARY: Verify a doctor running inside its own child process cannot spawn the suite again.
    @pytest.mark.unit
    def test_reentry_sentinel_disables_the_subprocess_layers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # **LOGIC_STEP**: The sentinel is set by the autouse fixture above. This asserts the
        # mechanism that keeps "the doctor runs the tests" from recursing forever — the first
        # clean run without it had to be killed after ten minutes.
        import subprocess as _subprocess

        def _explode(*args: object, **kwargs: object) -> object:
            raise AssertionError("a tool layer shelled out while the re-entry sentinel was set")

        monkeypatch.setattr(_subprocess, "run", _explode)

        payload, executed = diagnose_early_layers()

        assert payload is None
        assert executed == ()

    # FUNCTION: test_diagnose_full_returns_an_early_failure_before_the_validators
    # SUMMARY: Verify a failing tool step short-circuits ahead of the context map.
    @pytest.mark.unit
    def test_diagnose_full_returns_an_early_failure_before_the_validators(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "scripts.doctor_ai_context.diagnose_early_layers",
            lambda: (
                {"status": "error", "blocking_layer": "lint", "issues": []},
                ("lockfile", "lint"),
            ),
        )
        monkeypatch.setattr(
            "scripts.doctor_ai_context.diagnose",
            lambda: pytest.fail("validators ran even though a tool layer had already failed"),
        )

        assert diagnose_full()["blocking_layer"] == "lint"


# CLASS: tests.application.test_doctor_ai_context.TestDoctorSurvivesItsOwnTooling
# SUMMARY: Verify a validator the doctor cannot import is diagnosed rather than raised as a traceback.
class TestDoctorSurvivesItsOwnTooling:
    # FUNCTION: test_a_missing_validator_becomes_a_named_issue
    # SUMMARY: Regression guard: deleting scripts/validate_cbm.py made `make doctor` exit with a raw ModuleNotFoundError from ai_query/common.py, at the one moment a diagnostic tool has a job to do.
    @pytest.mark.unit
    def test_a_missing_validator_becomes_a_named_issue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "scripts.doctor_ai_context.UNAVAILABLE_VALIDATOR",
            "No module named 'scripts.validate_cbm'",
        )

        payload: dict[str, Any] = diagnose()

        issue = payload["issues"][0]
        assert payload["status"] == "error"
        assert issue["rule_id"] == LAYER_UNAVAILABLE_RULE_ID
        assert "validate_cbm" in str(issue["message"])

    # FUNCTION: test_the_unavailable_payload_names_what_went_unchecked
    # SUMMARY: The payload has to say that the layers below the broken one were never run, or a reader treats one reported issue as the whole diagnosis.
    @pytest.mark.unit
    def test_the_unavailable_payload_names_what_went_unchecked(self) -> None:
        issue = unavailable_validator_payload("No module named 'scripts.validate_secrets'")[
            "issues"
        ][0]  # type: ignore[index]

        assert "unchecked" in str(issue["message"])
        assert issue["likely_fix_shape"]

    # FUNCTION: test_terse_output_prints_the_fix_shape
    # SUMMARY: Regression guard: `likely_fix_shape` was built and never printed, so the terse output offered only `next:` — which for every gate layer is the command that just failed.
    @pytest.mark.unit
    def test_terse_output_prints_the_fix_shape(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "scripts.doctor_ai_context.diagnose_full",
            lambda: {
                "status": "error",
                "blocking_layer": "format",
                "issues": [
                    {
                        "message": "unformatted: File would be reformatted",
                        "recommended_next_command": "make gate-format",
                        "likely_fix_shape": "Run `make ai-autofix`.",
                    }
                ],
            },
        )
        monkeypatch.setattr(sys, "argv", ["scripts/doctor_ai_context.py"])

        exit_code = main()

        printed = capsys.readouterr().out
        assert exit_code == 1
        assert "fix: Run `make ai-autofix`." in printed
        assert printed.index("fix:") < printed.index("next:")


# CLASS: tests.application.test_doctor_ai_context.TestDoctorRepeatsTheUnverifiedMigrationNotice
# SUMMARY: Verify an "ok" doctor run still says when the migration check never reached a database.
# NOTE: The skip is not an error, so it is filtered out of the blocking set and the doctor used to
# report a clean run indistinguishable from a verified one. Measured in both projects of the
# 2026-09-07 duel: a migration that dropped a column instead of renaming it passed every local
# gate. The notice is read from validate_migrations.py's own issue rather than re-worded, so the
# sentence is written once — this asserts both halves, that it is carried and that it is that one.
class TestDoctorRepeatsTheUnverifiedMigrationNotice:
    # FUNCTION: test_the_skip_is_pulled_out_of_the_issue_batch
    # SUMMARY: Verify the skip's own message is what the doctor reports.
    # OUTPUT: (None): None.
    @pytest.mark.unit
    def test_the_skip_is_pulled_out_of_the_issue_batch(self) -> None:
        from scripts.validate_migrations import _NOT_VERIFIED_BANNER, MigrationIssue

        skipped = MigrationIssue(
            rule_id="migrations.database_unreachable",
            command_name="upgrade-head",
            message=f"{_NOT_VERIFIED_BANNER} — no database reachable, run `make db-up-worktree`.",
            returncode=0,
            severity="info",
        )
        other = MigrationIssue(
            rule_id="migrations.multiple_heads",
            command_name="heads",
            message="two heads",
            returncode=1,
            severity="error",
        )

        notice = _migrations_not_verified_notice([other, skipped])

        assert notice is not None
        assert notice.startswith(_NOT_VERIFIED_BANNER)

    # FUNCTION: test_a_verified_run_carries_no_notice
    # SUMMARY: Verify nothing is announced when the database was actually reached.
    # OUTPUT: (None): None.
    @pytest.mark.unit
    def test_a_verified_run_carries_no_notice(self) -> None:
        from scripts.validate_migrations import MigrationIssue

        assert _migrations_not_verified_notice([]) is None
        assert (
            _migrations_not_verified_notice(
                [
                    MigrationIssue(
                        rule_id="migrations.multiple_heads",
                        command_name="heads",
                        message="x",
                        returncode=1,
                        severity="error",
                    )
                ]
            )
            is None
        )
