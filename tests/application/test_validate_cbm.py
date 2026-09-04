# FILE: tests/application/test_validate_cbm.py
# SUMMARY: Unit tests for strict Code-Base Markup validation rules.

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import validate_cbm
from scripts.validate_cbm import (
    collect_validation_issues,
    default_repo_root,
    fix_python_source,
    validate_python_source,
)


# CLASS: tests.application.test_validate_cbm.TestValidateCBM
# SUMMARY: Verify the standalone CBM validator catches missing required annotations.
class TestValidateCBM:
    # FUNCTION: test_validate_python_source_accepts_valid_cbm_file
    # SUMMARY: Verify a production-style file with required CBM tags passes validation.
    @pytest.mark.unit
    def test_validate_python_source_accepts_valid_cbm_file(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "valid_module.py"
        path.write_text(
            "# FILE: valid_module.py\n"
            "# SUMMARY: Valid module.\n\n"
            "# CLASS: valid_module.Example\n"
            "# SUMMARY: Example class.\n"
            "class Example:\n"
            "    # FUNCTION: run\n"
            "    # SUMMARY: Execute the example.\n"
            "    def run(self) -> None:\n"
            "        return None\n",
            encoding="utf-8",
        )

        assert validate_python_source(path) == []

    # FUNCTION: test_validate_python_source_rejects_missing_function_tags
    # SUMMARY: Verify missing function-level CBM comments are reported.
    @pytest.mark.unit
    def test_validate_python_source_rejects_missing_function_tags(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "invalid_module.py"
        path.write_text(
            "# FILE: invalid_module.py\n"
            "# SUMMARY: Invalid module.\n\n"
            "# CLASS: invalid_module.Example\n"
            "# SUMMARY: Example class.\n"
            "class Example:\n"
            "    def run(self) -> None:\n"
            "        return None\n",
            encoding="utf-8",
        )

        issues = validate_python_source(path)

        assert any("Function 'run' is missing '# FUNCTION:'" in issue.message for issue in issues)
        assert any("Function 'run' is missing '# SUMMARY:'" in issue.message for issue in issues)

    # FUNCTION: test_validate_python_source_allows_unannotated_module_attributes
    # SUMMARY: Verify trivial module-level assignments are optional detail under the pragmatic CBM policy.
    @pytest.mark.unit
    def test_validate_python_source_allows_unannotated_module_attributes(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "invalid_attribute_module.py"
        path.write_text(
            "# FILE: invalid_attribute_module.py\n# SUMMARY: Invalid module.\n\nVALUE = 1\n",
            encoding="utf-8",
        )

        issues = validate_python_source(path)

        assert issues == []

    # FUNCTION: test_validate_python_source_rejects_missing_class_attribute_summary
    # SUMMARY: Verify class attributes require both ATTRIBUTE and SUMMARY metadata.
    @pytest.mark.unit
    def test_validate_python_source_rejects_missing_class_attribute_summary(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "invalid_class_attribute_module.py"
        path.write_text(
            "# FILE: invalid_class_attribute_module.py\n"
            "# SUMMARY: Invalid module.\n\n"
            "# CLASS: invalid_class_attribute_module.Example\n"
            "# SUMMARY: Example class.\n"
            "class Example:\n"
            "    # ATTRIBUTE: value (int)\n"
            "    value = 1\n",
            encoding="utf-8",
        )

        issues = validate_python_source(path)

        assert any(
            "Attribute 'Example.value' includes '# ATTRIBUTE:' but is missing '# SUMMARY:'"
            for issue in issues
        )

    # FUNCTION: test_validate_python_source_allows_private_helpers_without_cbm
    # SUMMARY: Verify private helper methods are optional detail and do not require function-level CBM tags.
    @pytest.mark.unit
    def test_validate_python_source_allows_private_helpers_without_cbm(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "private_helper_module.py"
        path.write_text(
            "# FILE: private_helper_module.py\n"
            "# SUMMARY: Valid module.\n\n"
            "# CLASS: private_helper_module.Example\n"
            "# SUMMARY: Example class.\n"
            "class Example:\n"
            "    def _helper(self) -> None:\n"
            "        return None\n",
            encoding="utf-8",
        )

        assert validate_python_source(path) == []

    # FUNCTION: test_validate_python_source_keeps_constructor_under_strict_core
    # SUMMARY: Verify constructors remain part of the strict-core CBM requirement.
    @pytest.mark.unit
    def test_validate_python_source_keeps_constructor_under_strict_core(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "constructor_module.py"
        path.write_text(
            "# FILE: constructor_module.py\n"
            "# SUMMARY: Valid module.\n\n"
            "# CLASS: constructor_module.Example\n"
            "# SUMMARY: Example class.\n"
            "class Example:\n"
            "    def __init__(self) -> None:\n"
            "        self.value = 1\n",
            encoding="utf-8",
        )

        issues = validate_python_source(path)

        assert any(
            "Function '__init__' is missing '# FUNCTION:'" in issue.message for issue in issues
        )

    # FUNCTION: test_validate_python_source_rejects_module_docstring
    # SUMMARY: Verify module docstrings are rejected in strict production files.
    @pytest.mark.unit
    def test_validate_python_source_rejects_module_docstring(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "docstring_module.py"
        path.write_text(
            '"""Bad docstring."""\n# FILE: docstring_module.py\n# SUMMARY: Invalid module.\n',
            encoding="utf-8",
        )

        issues = validate_python_source(path)

        assert any("Module docstrings are not allowed" in issue.message for issue in issues)

    # FUNCTION: test_validate_python_source_rejects_mismatched_class_tag_name
    # SUMMARY: Verify class CBM tags must reference the actual class name.
    @pytest.mark.unit
    def test_validate_python_source_rejects_mismatched_class_tag_name(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "mismatched_class_module.py"
        path.write_text(
            "# FILE: mismatched_class_module.py\n"
            "# SUMMARY: Invalid module.\n\n"
            "# CLASS: mismatched_class_module.WrongName\n"
            "# SUMMARY: Example class.\n"
            "class RightName:\n"
            "    # FUNCTION: run\n"
            "    # SUMMARY: Execute the example.\n"
            "    def run(self) -> None:\n"
            "        return None\n",
            encoding="utf-8",
        )

        issues = validate_python_source(path)

        assert any(
            "Class 'RightName' has mismatched '# CLASS:' tag name 'WrongName'" in issue.message
            for issue in issues
        )

    # FUNCTION: test_validate_python_source_rejects_mismatched_function_tag_name
    # SUMMARY: Verify function CBM tags must reference the actual function name.
    @pytest.mark.unit
    def test_validate_python_source_rejects_mismatched_function_tag_name(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "mismatched_function_module.py"
        path.write_text(
            "# FILE: mismatched_function_module.py\n"
            "# SUMMARY: Invalid module.\n\n"
            "# CLASS: mismatched_function_module.Example\n"
            "# SUMMARY: Example class.\n"
            "class Example:\n"
            "    # FUNCTION: wrong_name\n"
            "    # SUMMARY: Execute the example.\n"
            "    def run(self) -> None:\n"
            "        return None\n",
            encoding="utf-8",
        )

        issues = validate_python_source(path)

        assert any(
            "Function 'run' has mismatched '# FUNCTION:' tag name 'wrong_name'" in issue.message
            for issue in issues
        )

    # FUNCTION: test_fix_python_source_rewrites_mismatched_class_and_function_tags
    # SUMMARY: Verify autofix mode rewrites only mismatched class-like and function tag names in place.
    @pytest.mark.unit
    def test_fix_python_source_rewrites_mismatched_class_and_function_tags(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "fixable_module.py"
        path.write_text(
            "# FILE: fixable_module.py\n"
            "# SUMMARY: Fixable module.\n\n"
            "# CLASS: fixable_module.WrongName\n"
            "# SUMMARY: Example class.\n"
            "class RightName:\n"
            "    # FUNCTION: wrong_name\n"
            "    # SUMMARY: Execute the example.\n"
            "    def run(self) -> None:\n"
            "        return None\n",
            encoding="utf-8",
        )

        changed = fix_python_source(path)

        assert changed is True
        assert validate_python_source(path) == []
        assert "# CLASS: fixable_module.RightName" in path.read_text(encoding="utf-8")
        assert "# FUNCTION: run" in path.read_text(encoding="utf-8")

    # FUNCTION: test_fix_python_source_is_noop_for_valid_file
    # SUMMARY: Verify autofix mode leaves already-correct CBM files untouched.
    @pytest.mark.unit
    def test_fix_python_source_is_noop_for_valid_file(self, tmp_path: Path) -> None:
        path = tmp_path / "valid_module.py"
        original = (
            "# FILE: valid_module.py\n"
            "# SUMMARY: Valid module.\n\n"
            "# CLASS: valid_module.Example\n"
            "# SUMMARY: Example class.\n"
            "class Example:\n"
            "    # FUNCTION: run\n"
            "    # SUMMARY: Execute the example.\n"
            "    def run(self) -> None:\n"
            "        return None\n"
        )
        path.write_text(original, encoding="utf-8")

        changed = fix_python_source(path)

        assert changed is False
        assert path.read_text(encoding="utf-8") == original

    # FUNCTION: test_validate_python_source_rejects_logic_step_outside_function
    # SUMMARY: Verify LOGIC_STEP comments are only allowed inside function bodies.
    @pytest.mark.unit
    def test_validate_python_source_rejects_logic_step_outside_function(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "logic_step_module.py"
        path.write_text(
            "# FILE: logic_step_module.py\n"
            "# SUMMARY: Invalid module.\n\n"
            "# **LOGIC_STEP**: Not inside a function.\n"
            "VALUE = 1\n",
            encoding="utf-8",
        )

        issues = validate_python_source(path)

        assert any("must appear inside a function body" in issue.message for issue in issues)

    # FUNCTION: test_validate_python_source_ignores_the_marker_inside_a_string_literal
    # SUMMARY: Verify a module that only names the marker as data is not reported as violating it.
    # NOTE: The rule used to select its lines by substring over the raw file text, so a keyword
    # table, a docstring about the convention or a test fixture read as a misplaced marker.
    # scripts/validate_cbm.py is itself such a module — the string below is the shape of its own
    # rule table — and scanning it without the `project/` scope filter reported it. That the
    # filter hid it is an accident of directory layout, not a property of the check.
    @pytest.mark.unit
    def test_validate_python_source_ignores_the_marker_inside_a_string_literal(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "marker_as_data_module.py"
        path.write_text(
            "# FILE: marker_as_data_module.py\n"
            "# SUMMARY: Module whose data happens to quote the marker.\n\n"
            "# ATTRIBUTE: _RULE_KEYWORDS (list[str])\n"
            "# SUMMARY: Message fragments this module classifies by.\n"
            "_RULE_KEYWORDS = [\n"
            "    \"'**LOGIC_STEP**:' must appear inside\",\n"
            "]\n",
            encoding="utf-8",
        )

        issues = validate_python_source(path)

        assert not any("must appear inside a function body" in issue.message for issue in issues)

    # FUNCTION: test_validate_python_source_ignores_the_marker_inside_a_docstring
    # SUMMARY: Verify prose explaining the convention inside a docstring is not read as a marker.
    @pytest.mark.unit
    def test_validate_python_source_ignores_the_marker_inside_a_docstring(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "marker_in_docstring_module.py"
        path.write_text(
            "# FILE: marker_in_docstring_module.py\n"
            "# SUMMARY: Module whose docstring quotes the convention.\n\n"
            "# FUNCTION: describe\n"
            "# SUMMARY: Names the convention in prose without annotating anything.\n"
            "def describe(value: int) -> int:\n"
            '    """Return the value.\n'
            "\n"
            "    Written the way the '# **LOGIC_STEP**: why' convention asks.\n"
            '    """\n'
            "    return value\n",
            encoding="utf-8",
        )

        issues = validate_python_source(path)

        assert issues == []

    # FUNCTION: test_this_validator_does_not_report_itself
    # SUMMARY: Verify the validator is clean against its own source, scope filter aside.
    # NOTE: The rule table in scripts/validate_cbm.py quotes this rule's own message as data. Under
    # the substring scan that was one reported violation on shipped, correct code; it stayed
    # invisible only because strict scanning covers `project/` and nothing else.
    @pytest.mark.unit
    def test_this_validator_does_not_report_itself(self) -> None:
        validator = Path(__file__).resolve().parents[2] / "scripts" / "validate_cbm.py"

        issues = validate_python_source(validator)

        assert [issue.message for issue in issues] == []

    # FUNCTION: test_fix_python_source_does_not_add_private_helper_tags
    # SUMMARY: Verify autofix mode does not synthesize missing tags for private helpers that remain optional detail.
    @pytest.mark.unit
    def test_fix_python_source_does_not_add_private_helper_tags(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "private_helper_module.py"
        path.write_text(
            "# FILE: private_helper_module.py\n"
            "# SUMMARY: Valid module.\n\n"
            "# CLASS: private_helper_module.Example\n"
            "# SUMMARY: Example class.\n"
            "class Example:\n"
            "    def _helper(self) -> None:\n"
            "        return None\n",
            encoding="utf-8",
        )

        changed = fix_python_source(path)

        assert changed is False
        assert "# FUNCTION:" not in path.read_text(encoding="utf-8")

    # FUNCTION: test_validate_python_source_emits_syntax_error_issue
    # SUMMARY: Regression guard: a syntactically broken file must surface as a structured ValidationIssue (rule_id 'cbm.syntax_error') rather than crashing the validator with a raw Python traceback.
    @pytest.mark.unit
    def test_validate_python_source_emits_syntax_error_issue(
        self,
        tmp_path: Path,
    ) -> None:
        from scripts.validate_cbm import classify_issue_rule_id

        path = tmp_path / "broken_module.py"
        path.write_text(
            "# FILE: broken_module.py\n# SUMMARY: Broken module.\n\ndef foo(:\n    pass\n",
            encoding="utf-8",
        )

        issues = validate_python_source(path)

        syntax_issues = [
            issue for issue in issues if classify_issue_rule_id(issue.message) == "cbm.syntax_error"
        ]
        assert len(syntax_issues) == 1
        assert "SyntaxError while parsing" in syntax_issues[0].message

    # FUNCTION: test_fix_python_source_returns_false_on_syntax_error
    # SUMMARY: Autofix on a broken file must short-circuit instead of crashing — validate run still surfaces the syntax error structurally.
    @pytest.mark.unit
    def test_fix_python_source_returns_false_on_syntax_error(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "broken_fix.py"
        path.write_text(
            "# FILE: broken_fix.py\n# SUMMARY: Broken.\n\ndef foo(:\n    pass\n",
            encoding="utf-8",
        )

        assert fix_python_source(path) is False

    # FUNCTION: test_main_rejects_unknown_flag_via_argv
    # SUMMARY: Regression guard for the argv-leak bug — running the validator with a typo'd flag must exit 2 (argparse 'unrecognized arguments') rather than silently exit 0 because main() was called without sys.argv[1:].
    @pytest.mark.unit
    def test_main_rejects_unknown_flag_via_argv(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/validate_cbm.py", "--not-a-real-flag"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "unrecognized arguments" in result.stderr

    # FUNCTION: test_default_repo_root_contains_the_scanned_package
    # SUMMARY: Regression guard: the root the CLI scans must be the repository, not scripts/.
    @pytest.mark.unit
    def test_default_repo_root_contains_the_scanned_package(self) -> None:
        # **LOGIC_STEP**: A single .parent here made repo_root point at scripts/, where no
        # relative path starts with "project/", so the strict-scope filter matched nothing
        # and the validator returned "passed" for any repository state.
        root = default_repo_root()

        assert (root / "project").is_dir()
        assert (root / "pyproject.toml").is_file()
        assert root.name != "scripts"

    # FUNCTION: test_collect_validation_issues_detects_defect_in_project_package
    # SUMMARY: Regression guard: a real CBM defect under project/ must surface, proving the walk reaches it.
    @pytest.mark.unit
    def test_collect_validation_issues_detects_defect_in_project_package(
        self,
        tmp_path: Path,
    ) -> None:
        package_dir = tmp_path / "project" / "core"
        package_dir.mkdir(parents=True)
        (package_dir / "broken.py").write_text(
            "# FILE: project/core/broken.py\n"
            "# SUMMARY: Module used to prove the validator reaches project/.\n"
            "\n"
            "\n"
            "# FUNCTION: build_dsn\n"
            "def build_dsn() -> str:\n"
            '    return ""\n',
            encoding="utf-8",
        )

        issues = collect_validation_issues(tmp_path)

        assert [issue.message for issue in issues] != []

    # FUNCTION: test_main_json_flag_emits_valid_json
    # SUMMARY: Regression guard: --json must yield parseable JSON, not the plain-text "CBM validation passed." that the argv-leak bug used to produce.
    @pytest.mark.unit
    def test_main_json_flag_emits_valid_json(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/validate_cbm.py", "--json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["status"] == "ok"
        assert isinstance(payload["issues"], list)


# CLASS: tests.application.test_validate_cbm.TestTheRuleTableStaysReachable
# SUMMARY: Verify every entry in _CBM_RULE_MAP still matches a message the validator can emit.
class TestTheRuleTableStaysReachable:
    # FUNCTION: test_every_keyword_appears_in_a_message_the_validator_builds
    # SUMMARY: Verify no rule became unreachable because its message was reworded.
    # **LOGIC_STEP**: Classification works by finding the keyword inside the finished message, so
    # the table and the messages are two copies of the same wording. Reword one and the rule
    # silently degrades to cbm.unknown — no test fails, the JSON output just stops naming the
    # rule. This compares the two copies instead of trusting them to stay in step.
    @pytest.mark.unit
    def test_every_keyword_appears_in_a_message_the_validator_builds(self) -> None:
        source = Path(validate_cbm.__file__).read_text(encoding="utf-8")
        table_start = source.index("_CBM_RULE_MAP: list[")
        table_end = source.index("# FUNCTION: classify_issue", table_start)
        messages = source[:table_start] + source[table_end:]

        unreachable = [
            keyword for keyword, _, _ in validate_cbm._CBM_RULE_MAP if keyword not in messages
        ]

        assert unreachable == [], (
            "these keywords no longer occur in any message the validator builds, so their rules "
            f"can never be reported: {unreachable}"
        )

    # FUNCTION: test_every_rule_id_is_namespaced
    # SUMMARY: Verify rule ids keep the cbm. prefix the playbook lookup requires.
    @pytest.mark.unit
    def test_every_rule_id_is_namespaced(self) -> None:
        assert [rid for _, rid, _ in validate_cbm._CBM_RULE_MAP if not rid.startswith("cbm.")] == []


# CLASS: tests.application.test_validate_cbm.TestTheFileTagNamesTheFile
# SUMMARY: Verify every `# FILE:` tag names the file it sits in, by path or by bare name.
# NOTE: The validator checks that the tag is present, and its playbook says to write "its own
# path" — but nothing compared the two. Three files spelled the tag as a dotted module
# (`tests.application.test_query_ai_context.py`), which resolves to nothing at all: an agent told
# to open the file the header names has no file to open. Both spellings that do resolve are
# accepted here, because the repository has always used the bare name inside `scripts/` and
# `ai_context/` and converting those is churn, not a fix.
class TestTheFileTagNamesTheFile:
    # FUNCTION: _declared_tags
    # SUMMARY: Read the `# FILE:` tag off line one of every tracked Python file.
    # OUTPUT: (list[tuple[str, str]]): Repository-relative path paired with the tag it declares.
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

    # FUNCTION: test_no_tag_names_something_that_is_not_the_file
    # SUMMARY: Verify no header points a reader at a path or module that does not exist.
    @pytest.mark.unit
    def test_no_tag_names_something_that_is_not_the_file(self) -> None:
        wrong = [
            (name, tag)
            for name, tag in self._declared_tags()
            if tag != name and tag != Path(name).name
        ]

        assert wrong == [], (
            "a `# FILE:` tag must be the file's own path or its bare name; these are neither: "
            f"{wrong}"
        )

    # FUNCTION: test_the_reader_finds_the_tags_it_is_meant_to_check
    # SUMMARY: Verify the collector returns the repository's files, so the guard cannot pass empty.
    # **LOGIC_STEP**: The test above is green for a reader that finds no tags at all. The kernel
    # ships well over a hundred marked-up modules, so a count this far below the truth means the
    # reader broke rather than the repository shrinking.
    @pytest.mark.unit
    def test_the_reader_finds_the_tags_it_is_meant_to_check(self) -> None:
        assert len(self._declared_tags()) > 100
