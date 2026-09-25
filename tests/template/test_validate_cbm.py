# FILE: tests/template/test_validate_cbm.py
# SUMMARY: The file-header check: what it refuses, what it lets through, and that it reaches project/.

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import validate_cbm
from scripts.validate_cbm import (
    collect_validation_issues,
    default_repo_root,
    validate_python_source,
)


def _messages(tmp_path: Path, source: str) -> list[str]:
    path = tmp_path / "module.py"
    path.write_text(source, encoding="utf-8")
    return [issue.message for issue in validate_python_source(path)]


class TestTheHeaderRule:
    # Everything below the header is free: symbols carry no tags, and a plain comment is a comment.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "source",
        [
            "# FILE: project/core/module.py\n# SUMMARY: One line.\n\ndef run() -> None:\n    pass\n",
            "# FILE: module.py\n# SUMMARY: A summary that\n# runs over two lines.\n\nVALUE = 1\n",
            "\n# FILE: project/core/module.py\n# SUMMARY: After a blank line.\n\nclass Example:\n"
            "    # Why the class exists, as a plain comment.\n    pass\n",
        ],
        ids=["tagless-function", "two-line-summary", "leading-blank-line"],
    )
    def test_a_file_that_opens_with_its_header_passes(self, tmp_path: Path, source: str) -> None:
        assert _messages(tmp_path, source) == []

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                "def run() -> None:\n    pass\n",
                ["Missing '# FILE:' tag", "Missing file '# SUMMARY:' tag"],
            ),
            ("# FILE: module.py\n\n# SUMMARY: Too late.\n", ["Missing file '# SUMMARY:' tag"]),
            (
                '"""Docstring."""\n# FILE: module.py\n# SUMMARY: Docstring first.\n',
                [
                    "Module docstrings are not allowed; the file header carries the summary",
                    "Missing '# FILE:' tag",
                    "Missing file '# SUMMARY:' tag",
                ],
            ),
        ],
        ids=["no-header", "summary-outside-the-header", "module-docstring"],
    )
    def test_a_file_without_its_header_is_refused(
        self, tmp_path: Path, source: str, expected: list[str]
    ) -> None:
        assert _messages(tmp_path, source) == expected


class TestTheValidatorReachesTheRepository:
    # A single .parent made the root scripts/, where no path starts with "project/", so the
    # validator passed any repository state.
    @pytest.mark.unit
    def test_default_repo_root_contains_the_scanned_package(self) -> None:
        root = default_repo_root()

        assert (root / "project").is_dir()
        assert (root / "pyproject.toml").is_file()

    @pytest.mark.unit
    def test_a_file_without_a_header_under_project_is_found(self, tmp_path: Path) -> None:
        package = tmp_path / "project" / "core"
        package.mkdir(parents=True)
        (package / "broken.py").write_text("VALUE = 1\n", encoding="utf-8")
        (tmp_path / "outside.py").write_text("VALUE = 1\n", encoding="utf-8")

        issues = collect_validation_issues(tmp_path)

        assert {issue.path.name for issue in issues} == {"broken.py"}

    @pytest.mark.unit
    def test_main_json_flag_emits_valid_json(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/validate_cbm.py", "--json"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert json.loads(result.stdout) == {"status": "ok", "issues": []}

    # Called without sys.argv[1:], main() once ignored every flag and exited 0.
    @pytest.mark.unit
    def test_main_rejects_unknown_flag_via_argv(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/validate_cbm.py", "--not-a-real-flag"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 2
        assert "unrecognized arguments" in result.stderr


class TestTheRuleTableStaysReachable:
    # Classification finds the keyword inside the finished message, so the table and the messages
    # are two copies of one wording. Reword one and the rule silently degrades to cbm.unknown.
    @pytest.mark.unit
    def test_every_keyword_appears_in_a_message_the_validator_builds(self) -> None:
        source = Path(validate_cbm.__file__).read_text(encoding="utf-8")
        table_start = source.index("_CBM_RULE_MAP: list[")
        table_end = source.index("def classify_issue", table_start)
        messages = source[:table_start] + source[table_end:]

        unreachable = [
            keyword for keyword, _, _ in validate_cbm._CBM_RULE_MAP if keyword not in messages
        ]

        assert unreachable == []

    @pytest.mark.unit
    def test_every_rule_id_has_a_playbook_and_an_invented_one_has_none(self) -> None:
        rule_ids = [rule_id for _, rule_id, _ in validate_cbm._CBM_RULE_MAP]

        assert all(validate_cbm.get_cbm_rule_playbook(rule_id) for rule_id in rule_ids)
        assert validate_cbm.get_cbm_rule_playbook("cbm.this_does_not_exist") is None


# The validator checks that the tag is present; nothing else compared it with the file. Three files
# once spelled it as a dotted module (`tests.application.test_query_ai_context.py`), which resolves
# to nothing: an agent told to open the file the header names had no file to open. Both spellings
# that do resolve are accepted — the bare name has always been used inside scripts/ and ai_context/.
class TestTheFileTagNamesTheFile:
    @staticmethod
    def _declared_tags() -> list[tuple[str, str]]:
        root = default_repo_root()
        listed = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        tags: list[tuple[str, str]] = []
        for name in listed.stdout.split():
            first = (root / name).read_text(encoding="utf-8").split("\n", 1)[0]
            if first.startswith("# FILE: "):
                tags.append((name, first[len("# FILE: ") :].strip()))
        return tags

    @pytest.mark.unit
    def test_no_tag_names_something_that_is_not_the_file(self) -> None:
        wrong = [
            (name, tag)
            for name, tag in self._declared_tags()
            if tag != name and tag != Path(name).name
        ]

        assert wrong == []

    # The test above is green for a reader that finds no tags at all; the kernel ships well over a
    # hundred headed modules, so a count this far below the truth means the reader broke.
    @pytest.mark.unit
    def test_the_reader_finds_the_tags_it_is_meant_to_check(self) -> None:
        assert len(self._declared_tags()) > 100
