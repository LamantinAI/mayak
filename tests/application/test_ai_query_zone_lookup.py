# FILE: tests/application/test_ai_query_zone_lookup.py
# SUMMARY: Unit tests for zone_for_path FILE_POLICY-first resolution and EDIT_ZONES fallback.

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from ai_query.common import annotate_paths, zone_for_path, zone_via_edit_zones_patterns
from scripts.generate_ai_context import build_architecture_rules


# CLASS: tests.application.test_ai_query_zone_lookup.TestZoneForPathFilePolicyFirst
# SUMMARY: zone_for_path consults file_policy_index before EDIT_ZONES patterns.
class TestZoneForPathFilePolicyFirst:
    # FUNCTION: test_zone_for_path_uses_file_policy_for_validate_architecture
    # SUMMARY: validate_architecture.py is in FILE_POLICY as 'expert' but not in EDIT_ZONES.expert patterns.
    @pytest.mark.unit
    def test_zone_for_path_uses_file_policy_for_validate_architecture(self) -> None:
        rules: dict[str, Any] = build_architecture_rules()
        result = zone_for_path("scripts/validate_architecture.py", rules)
        assert result == {"zone": "expert", "risk": "high"}

    # FUNCTION: test_zone_for_path_uses_file_policy_for_validate_endpoint_wiring
    # SUMMARY: validate_endpoint_wiring.py is in FILE_POLICY as 'expert' but not in EDIT_ZONES.expert patterns.
    @pytest.mark.unit
    def test_zone_for_path_uses_file_policy_for_validate_endpoint_wiring(self) -> None:
        rules: dict[str, Any] = build_architecture_rules()
        result = zone_for_path("scripts/validate_endpoint_wiring.py", rules)
        assert result == {"zone": "expert", "risk": "high"}


# CLASS: tests.application.test_ai_query_zone_lookup.TestZoneForPathRegression
# SUMMARY: Paths that were correctly classified before the FILE_POLICY-first change continue to work.
class TestZoneForPathRegression:
    # FUNCTION: test_zone_for_path_regression_runtime_ownership
    # SUMMARY: validate_runtime_ownership.py is in both FILE_POLICY and EDIT_ZONES.expert — must remain expert/high.
    @pytest.mark.unit
    def test_zone_for_path_regression_runtime_ownership(self) -> None:
        rules: dict[str, Any] = build_architecture_rules()
        result = zone_for_path("scripts/validate_runtime_ownership.py", rules)
        assert result == {"zone": "expert", "risk": "high"}

    # FUNCTION: test_zone_for_path_regression_unrelated_safe_path
    # SUMMARY: An unindexed path that matches an EDIT_ZONES.safe pattern still resolves via the fallback.
    @pytest.mark.unit
    def test_zone_for_path_regression_unrelated_safe_path(self) -> None:
        rules: dict[str, Any] = build_architecture_rules()
        result = zone_for_path("tests/application/test_validate_cbm.py", rules)
        assert result["zone"] != "unclassified"
        assert result["risk"] != "unknown"


# CLASS: tests.application.test_ai_query_zone_lookup.TestZoneForPathFallback
# SUMMARY: Paths without a FILE_POLICY entry fall back to EDIT_ZONES pattern matching.
class TestZoneForPathFallback:
    # FUNCTION: test_zone_for_path_falls_back_to_edit_zones_when_no_file_policy_entry
    # SUMMARY: An invented path with no FILE_POLICY entry still matches an EDIT_ZONES pattern.
    @pytest.mark.unit
    def test_zone_for_path_falls_back_to_edit_zones_when_no_file_policy_entry(self) -> None:
        rules: dict[str, Any] = build_architecture_rules()
        # An invented .github/workflows/ child has no FILE_POLICY entry, but
        # .github/workflows/ is in EDIT_ZONES.caution by pattern.
        fallback_result = zone_for_path(".github/workflows/imaginary.yml", rules)
        pattern_only_result = zone_via_edit_zones_patterns(
            ".github/workflows/imaginary.yml", rules["edit_zones"]
        )
        assert fallback_result == pattern_only_result
        assert fallback_result["zone"] != "unclassified"

    # FUNCTION: test_zone_for_path_unclassified_when_no_match
    # SUMMARY: A path matched by neither FILE_POLICY nor EDIT_ZONES returns unclassified/unknown.
    @pytest.mark.unit
    def test_zone_for_path_unclassified_when_no_match(self) -> None:
        rules: dict[str, Any] = build_architecture_rules()
        result = zone_for_path("nonexistent_directory_xyz/random_file.txt", rules)
        assert result == {"zone": "unclassified", "risk": "unknown"}


# CLASS: tests.application.test_ai_query_zone_lookup.TestAnnotatePathsEndToEnd
# SUMMARY: annotate_paths consumes the same zone_for_path and reflects FILE_POLICY override.
class TestAnnotatePathsEndToEnd:
    # FUNCTION: test_annotate_paths_reflects_file_policy_override
    # SUMMARY: Both files previously misclassified as unclassified now annotate as expert/high.
    @pytest.mark.unit
    def test_annotate_paths_reflects_file_policy_override(self) -> None:
        rules: dict[str, Any] = build_architecture_rules()
        annotated = annotate_paths(
            ["scripts/validate_architecture.py", "scripts/validate_endpoint_wiring.py"],
            rules,
        )
        assert len(annotated) == 2
        for record in annotated:
            assert record["zone"] == "expert"
            assert record["risk"] == "high"


# CLASS: tests.application.test_ai_query_zone_lookup.TestVerticalPathsAreClassified
# SUMMARY: The paths a typical vertical touches first must resolve to a zone, not to 'unclassified'.
class TestVerticalPathsAreClassified:
    # FUNCTION: test_vertical_paths_resolve_to_a_zone
    # SUMMARY: before-edit exited 1 on these three paths, so guidance was missing exactly where the change protocol sends the agent.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "path",
        [
            "project/domain/ports.py",
            "project/infrastructure/persistence/reference_task_repository.py",
            "alembic/versions/0001_initial.py",
            "tests/functional/src/test_reference_task_repository.py",
            "tests/infrastructure/test_reference_task_repository.py",
        ],
    )
    def test_vertical_paths_resolve_to_a_zone(self, path: str) -> None:
        rules: dict[str, Any] = build_architecture_rules()

        result = zone_for_path(path, rules)

        assert result["zone"] != "unclassified", path
        assert result["risk"] != "unknown", path


# CLASS: tests.application.test_ai_query_zone_lookup.TestEveryApplicationFileHasAPolicy
# SUMMARY: No file the agent can be asked to edit may answer 'unclassified'.
class TestEveryApplicationFileHasAPolicy:
    # FUNCTION: test_no_python_file_in_the_application_package_is_unclassified
    # SUMMARY: Walk project/ on disk and fail naming every file without a zone.
    # NOTE: 21 of 59 files here had no policy — every config module, the launcher, the LLM
    # adapters — and `before-edit` exited 1 on each with `Unknown or unindexed file policy path`.
    # validate_file_policy.py stayed green throughout: it audits the explicit index, and these
    # files were in neither the index nor a pattern. This test walks the tree instead of a list,
    # so a new subpackage cannot reintroduce the gap.
    @pytest.mark.unit
    def test_no_python_file_in_the_application_package_is_unclassified(self) -> None:
        rules: dict[str, Any] = build_architecture_rules()
        root = Path(__file__).parents[2]

        unclassified = [
            str(path.relative_to(root))
            for path in sorted((root / "project").rglob("*.py"))
            if zone_for_path(str(path.relative_to(root)), rules)["zone"] == "unclassified"
        ]

        assert not unclassified, (
            "these files have no edit policy, so before-edit refuses to answer for them: "
            f"{unclassified}. Add an EDIT_ZONES prefix or a FILE_POLICY_INDEX entry."
        )

    # FUNCTION: test_no_tracked_file_in_the_repository_is_unclassified
    # SUMMARY: Walk every file git tracks and fail naming each one before-edit cannot answer for.
    # NOTE: The walk above covers project/ only, and 29 tracked files sat outside it with no zone
    # at all: README.md, every ADR, docs/agent_rules.md — the hand-written source both agent
    # wrappers are generated from — .env.sample, the hooks, and AGENTS.md, which the pre-edit
    # guard refuses writes to while before-edit could not say why. Asking about any of them
    # exited 1. A tool that answers "unknown" for the file in front of you is not consulted twice.
    @pytest.mark.unit
    def test_no_tracked_file_in_the_repository_is_unclassified(self) -> None:
        rules: dict[str, Any] = build_architecture_rules()
        root = Path(__file__).parents[2]
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\0")

        unclassified = [
            path
            for path in tracked
            if path and zone_for_path(path, rules)["zone"] == "unclassified"
        ]

        assert not unclassified, (
            "these tracked files have no edit policy, so before-edit refuses to answer for them: "
            f"{unclassified}. Add an EDIT_ZONES prefix or a FILE_POLICY_INDEX entry."
        )

    # FUNCTION: test_the_most_specific_pattern_wins_over_a_broader_one
    # SUMMARY: A file under an expert prefix stays expert even though a caution prefix also matches.
    # NOTE: This is the regression the `project/` catch-all above would otherwise have caused.
    # zone_via_edit_zones_patterns used to return the FIRST matching zone while iterating zones in
    # dict order, and `caution` is declared before `expert` — so the catch-all silently demoted
    # the whole logging kernel from expert/high to caution/medium. Seventeen files, no gate red.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("path", "expected_zone"),
        [
            ("project/core/logging/logger.py", "expert"),
            ("project/core/logging/trace_formatter.py", "expert"),
            ("project/core/composition_root.py", "expert"),
            ("project/infrastructure/api/endpoints/health.py", "safe"),
            ("project/infrastructure/persistence/orm_models.py", "caution"),
            ("project/core/config_settings_core.py", "caution"),
        ],
    )
    def test_the_most_specific_pattern_wins_over_a_broader_one(
        self, path: str, expected_zone: str
    ) -> None:
        rules: dict[str, Any] = build_architecture_rules()

        assert zone_for_path(path, rules)["zone"] == expected_zone, path
