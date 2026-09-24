# FILE: tests/application/test_validate_file_policy.py
# SUMMARY: Unit tests for the FILE_POLICY_INDEX schema validator.

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_file_policy import (
    REQUIRED_FIELDS,
    collect_file_policy_issues,
    get_file_policy_rule_playbook,
)


def _valid_entry(entry_key: str) -> dict[str, object]:
    """Build a fully-populated FILE_POLICY entry that points at the given existing path."""
    return {
        "role": "Test entry.",
        "why_it_exists": "Used to exercise validate_file_policy invariants.",
        "layer": "test",
        "common_tasks": [],
        "kernel_or_reference": "template_kernel",
        "classification_reason": "Test fixture.",
        "source_of_truth": "tests/application/test_validate_file_policy.py",
        "edit_zone": "safe",
        "regenerate_if_changed": [],
        "validators_if_changed": [],
        "generated_artifacts": [],
        "do_not_edit_directly": False,
        "recommended_diff_style": "minimal-diff",
        "likely_failure_rules": [],
    }


# SUMMARY: Validate clean / well-formed indices produce no issues.
class TestFilePolicyHappyPath:
    # SUMMARY: The shipped FILE_POLICY_INDEX must validate clean against the live repo.
    @pytest.mark.unit
    def test_real_index_passes(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        issues = collect_file_policy_issues(repo_root)
        assert issues == []

    # SUMMARY: A synthetic well-formed entry passes when given via index_override.
    @pytest.mark.unit
    def test_synthetic_valid_entry_passes(self, tmp_path: Path) -> None:
        entry_path = tmp_path / "fixture.txt"
        entry_path.write_text("placeholder")
        index = {entry_path.name: _valid_entry(entry_path.name)}
        issues = collect_file_policy_issues(tmp_path, index_override=index)
        assert issues == []


# SUMMARY: Verify missing required fields surface file_policy.missing_field.
class TestFilePolicyMissingField:
    # SUMMARY: Removing edit_zone produces a missing_field issue.
    @pytest.mark.unit
    def test_missing_edit_zone_reports_missing_field(self, tmp_path: Path) -> None:
        entry_path = tmp_path / "fixture.txt"
        entry_path.write_text("x")
        entry = _valid_entry(entry_path.name)
        del entry["edit_zone"]
        issues = collect_file_policy_issues(tmp_path, index_override={entry_path.name: entry})
        assert any(
            issue.rule_id == "file_policy.missing_field" and issue.field == "edit_zone"
            for issue in issues
        )

    # SUMMARY: Each of the 14 required fields, removed alone, is reported as missing.
    @pytest.mark.unit
    def test_all_required_fields_individually_reportable(self, tmp_path: Path) -> None:
        entry_path = tmp_path / "fixture.txt"
        entry_path.write_text("x")
        for field_name in sorted(REQUIRED_FIELDS):
            entry = _valid_entry(entry_path.name)
            del entry[field_name]
            issues = collect_file_policy_issues(tmp_path, index_override={entry_path.name: entry})
            matched = [
                issue
                for issue in issues
                if issue.rule_id == "file_policy.missing_field" and issue.field == field_name
            ]
            assert matched, f"missing-field for '{field_name}' was not reported"


# SUMMARY: Verify edit_zone schema invariants.
class TestFilePolicyInvalidZone:
    # SUMMARY: A trailing-space typo in edit_zone surfaces a clean validator error, not KeyError.
    @pytest.mark.unit
    def test_trailing_space_zone_reports_invalid_zone(self, tmp_path: Path) -> None:
        entry_path = tmp_path / "fixture.txt"
        entry_path.write_text("x")
        entry = _valid_entry(entry_path.name)
        entry["edit_zone"] = "expert "  # trailing space
        issues = collect_file_policy_issues(tmp_path, index_override={entry_path.name: entry})
        assert any(issue.rule_id == "file_policy.invalid_zone" for issue in issues)

    # SUMMARY: An obviously-wrong zone surfaces invalid_zone.
    @pytest.mark.unit
    def test_unknown_zone_reports_invalid_zone(self, tmp_path: Path) -> None:
        entry_path = tmp_path / "fixture.txt"
        entry_path.write_text("x")
        entry = _valid_entry(entry_path.name)
        entry["edit_zone"] = "bogus_zone"
        issues = collect_file_policy_issues(tmp_path, index_override={entry_path.name: entry})
        assert any(issue.rule_id == "file_policy.invalid_zone" for issue in issues)


# SUMMARY: Verify on-disk path checks.
class TestFilePolicyBrokenPath:
    # SUMMARY: When the FILE_POLICY entry key does not exist on disk, broken_path is raised.
    @pytest.mark.unit
    def test_missing_entry_key_reports_broken_path(self, tmp_path: Path) -> None:
        entry = _valid_entry("nonexistent.py")
        issues = collect_file_policy_issues(tmp_path, index_override={"nonexistent.py": entry})
        assert any(
            issue.rule_id == "file_policy.broken_path" and issue.field == "<entry>"
            for issue in issues
        )

    # SUMMARY: Missing generated_artifacts paths surface broken_path.
    @pytest.mark.unit
    def test_missing_generated_artifact_reports_broken_path(self, tmp_path: Path) -> None:
        entry_path = tmp_path / "fixture.txt"
        entry_path.write_text("x")
        entry = _valid_entry(entry_path.name)
        entry["generated_artifacts"] = ["docs/does-not-exist.json"]
        issues = collect_file_policy_issues(tmp_path, index_override={entry_path.name: entry})
        assert any(
            issue.rule_id == "file_policy.broken_path" and issue.field == "generated_artifacts"
            for issue in issues
        )

    # SUMMARY: A `uv run python scripts/X.py` reference to a missing script surfaces broken_path.
    @pytest.mark.unit
    def test_missing_validator_script_reports_broken_path(self, tmp_path: Path) -> None:
        entry_path = tmp_path / "fixture.txt"
        entry_path.write_text("x")
        entry = _valid_entry(entry_path.name)
        entry["validators_if_changed"] = ["uv run python scripts/does-not-exist.py"]
        issues = collect_file_policy_issues(tmp_path, index_override={entry_path.name: entry})
        assert any(
            issue.rule_id == "file_policy.broken_path" and issue.field == "validators_if_changed"
            for issue in issues
        )


# SUMMARY: Verify file_policy.zone_pattern_drift detects FILE_POLICY vs EDIT_ZONES disagreement.
class TestZonePatternDrift:
    # SUMMARY: FILE_POLICY entry with edit_zone='safe' on a path matched by EDIT_ZONES.expert pattern emits drift.
    @pytest.mark.unit
    def test_zone_pattern_drift_detected(self, tmp_path: Path) -> None:
        fake_dir = tmp_path / "fake_dir"
        fake_dir.mkdir()
        entry_path = fake_dir / "fake_file.txt"
        entry_path.write_text("x")
        key = "fake_dir/fake_file.txt"
        entry = _valid_entry(key)
        entry["edit_zone"] = "safe"
        edit_zones_override = {
            "safe": [],
            "caution": [],
            "expert": ["fake_dir/"],
            "generated_do_not_edit": [],
        }
        issues = collect_file_policy_issues(
            tmp_path,
            index_override={key: entry},
            edit_zones_override=edit_zones_override,
        )
        drift_issues = [i for i in issues if i.rule_id == "file_policy.zone_pattern_drift"]
        assert len(drift_issues) == 1
        assert drift_issues[0].entry_key == key
        assert drift_issues[0].field == "edit_zone"
        assert "safe" in drift_issues[0].message
        assert "expert" in drift_issues[0].message

    # SUMMARY: FILE_POLICY entry whose path matches no EDIT_ZONES pattern is allowed (FILE_POLICY-only).
    @pytest.mark.unit
    def test_zone_pattern_drift_allows_unclassified_fallback(self, tmp_path: Path) -> None:
        fake_dir = tmp_path / "isolated_dir"
        fake_dir.mkdir()
        entry_path = fake_dir / "isolated_file.txt"
        entry_path.write_text("x")
        key = "isolated_dir/isolated_file.txt"
        entry = _valid_entry(key)
        entry["edit_zone"] = "expert"
        edit_zones_override = {
            "safe": ["totally_different_dir/"],
            "caution": [],
            "expert": [],
            "generated_do_not_edit": [],
        }
        issues = collect_file_policy_issues(
            tmp_path,
            index_override={key: entry},
            edit_zones_override=edit_zones_override,
        )
        drift_issues = [i for i in issues if i.rule_id == "file_policy.zone_pattern_drift"]
        assert drift_issues == []

    # SUMMARY: FILE_POLICY entry whose edit_zone matches the EDIT_ZONES pattern zone produces no drift.
    @pytest.mark.unit
    def test_zone_pattern_agreement_no_issue(self, tmp_path: Path) -> None:
        fake_dir = tmp_path / "matched_dir"
        fake_dir.mkdir()
        entry_path = fake_dir / "matched_file.txt"
        entry_path.write_text("x")
        key = "matched_dir/matched_file.txt"
        entry = _valid_entry(key)
        entry["edit_zone"] = "expert"
        edit_zones_override = {
            "safe": [],
            "caution": [],
            "expert": ["matched_dir/"],
            "generated_do_not_edit": [],
        }
        issues = collect_file_policy_issues(
            tmp_path,
            index_override={key: entry},
            edit_zones_override=edit_zones_override,
        )
        drift_issues = [i for i in issues if i.rule_id == "file_policy.zone_pattern_drift"]
        assert drift_issues == []


# SUMMARY: Tests for get_file_policy_rule_playbook helper.
class TestRulePlaybook:
    # SUMMARY: Known rule_id returns a dict with the expected keys.
    @pytest.mark.unit
    def test_known_rule_returns_dict(self) -> None:
        playbook = get_file_policy_rule_playbook("file_policy.invalid_zone")
        assert isinstance(playbook, dict)
        assert "meaning" in playbook

    # SUMMARY: Unknown rule_id returns None.
    @pytest.mark.unit
    def test_unknown_rule_returns_none(self) -> None:
        assert get_file_policy_rule_playbook("file_policy.bogus") is None

    # SUMMARY: The new file_policy.zone_pattern_drift rule has a registered playbook.
    @pytest.mark.unit
    def test_zone_pattern_drift_rule_has_playbook(self) -> None:
        playbook = get_file_policy_rule_playbook("file_policy.zone_pattern_drift")
        assert isinstance(playbook, dict)
        assert "meaning" in playbook
        assert "smallest_command_to_rerun" in playbook
