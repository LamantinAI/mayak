# FILE: tests/template/test_validate_module_sizes.py
# SUMMARY: Unit tests for the production module budget validator, including the guarantee that documentation is not charged against the budget.

from pathlib import Path

import pytest

import subprocess
import sys

from scripts.validate_module_sizes import (
    FUNCTION_RULE_ID,
    MAX_CODE_LINES,
    MAX_FUNCTION_CODE_LINES,
    READ_ERROR_RULE_ID,
    RULE_ID,
    collect_module_metrics,
    collect_module_size_issues,
)


def _write_module(path: Path, line_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(["x = 1"] * line_count) + "\n", encoding="utf-8")


def _write_source(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


class TestValidateModuleSizes:
    @pytest.mark.unit
    def test_collect_module_size_issues_flags_large_project_module(self, tmp_path: Path) -> None:
        _write_module(tmp_path / "project" / "core" / "big_module.py", MAX_CODE_LINES + 1)

        issues = collect_module_size_issues(tmp_path)

        assert len(issues) == 1
        assert issues[0].path == Path("project/core/big_module.py")
        assert issues[0].line_count == MAX_CODE_LINES + 1

    @pytest.mark.unit
    def test_collect_module_size_issues_ignores_non_project_modules(self, tmp_path: Path) -> None:
        _write_module(tmp_path / "tests" / "application" / "test_big.py", MAX_CODE_LINES + 20)

        issues = collect_module_size_issues(tmp_path)

        assert issues == []

    # The point of the whole validator — a module whose raw size is far over the budget passes when most of it is comments.
    @pytest.mark.unit
    def test_comments_do_not_count_towards_the_budget(self, tmp_path: Path) -> None:
        body = "\n".join(["# explanation line", "x = 1"] * (MAX_CODE_LINES - 1))
        _write_source(tmp_path / "project" / "core" / "documented.py", body + "\n")

        issues = collect_module_size_issues(tmp_path)

        assert issues == []
        metrics = collect_module_metrics(tmp_path)["project/core/documented.py"]
        assert metrics.raw_lines > MAX_CODE_LINES
        assert metrics.code_lines == MAX_CODE_LINES - 1
        assert metrics.comment_lines == MAX_CODE_LINES - 1

    @pytest.mark.unit
    def test_docstrings_do_not_count_towards_the_budget(self, tmp_path: Path) -> None:
        filler = "\n".join(["    still explaining"] * 40)
        source = f'"""Module docstring.\n{filler}\n"""\n\n\ndef handler() -> int:\n    """Docstring.\n{filler}\n    """\n    return 1\n'
        _write_source(tmp_path / "project" / "core" / "docstrings.py", source)

        metrics = collect_module_metrics(tmp_path)["project/core/docstrings.py"]

        assert metrics.docstring_lines >= 80
        assert metrics.code_lines == 2

    # Comment detection goes through the tokenizer, so a '#' inside a string literal stays code and cannot be used to hide logic from the budget.
    @pytest.mark.unit
    def test_hash_inside_a_string_is_not_a_comment(self, tmp_path: Path) -> None:
        source = 'URL = "https://example.test/#anchor"\nCOLOUR = "#ffffff"\n'
        _write_source(tmp_path / "project" / "core" / "strings.py", source)

        metrics = collect_module_metrics(tmp_path)["project/core/strings.py"]

        assert metrics.code_lines == 2
        assert metrics.comment_lines == 0

    # Per-function measurement also discounts comments, so documenting a function does not make it look longer.
    @pytest.mark.unit
    def test_longest_function_is_measured_in_code_lines(self, tmp_path: Path) -> None:
        commented_body = "\n".join(["    # why this step exists", "    total += 1"] * 30)
        source = f"def accumulate() -> int:\n    total = 0\n{commented_body}\n    return total\n"
        _write_source(tmp_path / "project" / "core" / "longest.py", source)

        metrics = collect_module_metrics(tmp_path)["project/core/longest.py"]
        longest = metrics.longest_function()

        assert longest is not None
        assert longest.qualified_name == "accumulate"
        # def + total = 0 + 30 increments + return
        assert longest.code_lines == 33
        assert metrics.raw_lines == 63

    # A module the tokenizer refuses must not slip under the budget just because its comments could not be discounted.
    @pytest.mark.unit
    def test_unparseable_module_falls_back_to_raw_lines(self, tmp_path: Path) -> None:
        broken = "\n".join(["# comment"] * (MAX_CODE_LINES + 5)) + '\ntext = "unterminated\n'
        _write_source(tmp_path / "project" / "core" / "broken.py", broken)

        issues = collect_module_size_issues(tmp_path)

        assert len(issues) == 1
        assert issues[0].path == Path("project/core/broken.py")

    # A file that tokenizes but does not parse still gets trustworthy line classification, so a broken edit does not produce a bogus budget violation.
    @pytest.mark.unit
    def test_syntax_error_module_is_still_measured(self, tmp_path: Path) -> None:
        source = "\n".join(["# comment"] * 50) + "\nx = 1 +\n"
        _write_source(tmp_path / "project" / "core" / "syntax.py", source)

        metrics = collect_module_metrics(tmp_path)["project/core/syntax.py"]

        assert metrics.status == "syntax_error"
        assert metrics.comment_lines == 50
        assert metrics.functions == []

    # A UTF-8 BOM must not turn a normal module into a read error.
    @pytest.mark.unit
    def test_bom_prefixed_module_is_measured(self, tmp_path: Path) -> None:
        path = tmp_path / "project" / "core" / "bom.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes("﻿# header\nx = 1\n".encode("utf-8"))

        metrics = collect_module_metrics(tmp_path)["project/core/bom.py"]

        assert metrics.status == "ok"
        assert metrics.code_lines == 1
        assert metrics.comment_lines == 1

    # An empty module must produce zero counts rather than an exception.
    @pytest.mark.unit
    def test_empty_module_is_measured(self, tmp_path: Path) -> None:
        _write_source(tmp_path / "project" / "core" / "empty.py", "")

        metrics = collect_module_metrics(tmp_path)["project/core/empty.py"]

        assert metrics.status == "ok"
        assert metrics.raw_lines == 0
        assert metrics.code_lines == 0

    # The second axis exists for exactly this shape — a module comfortably under its own budget that holds one function nobody can hold in their head. The measurement behind the threshold is in scripts/validate_module_sizes.py, next to the constant.
    @pytest.mark.unit
    def test_oversized_function_is_flagged_even_when_the_module_fits(self, tmp_path: Path) -> None:
        body = "\n".join(["    total += 1"] * (MAX_FUNCTION_CODE_LINES + 1))
        source = f"def accumulate() -> int:\n    total = 0\n{body}\n    return total\n"
        _write_source(tmp_path / "project" / "core" / "long_function.py", source)

        issues = collect_module_size_issues(tmp_path)

        assert [issue.rule_id for issue in issues] == [FUNCTION_RULE_ID]
        assert issues[0].symbol == "accumulate"
        assert issues[0].line == 1
        assert "accumulate" in issues[0].describe()

    # The same guarantee as the module budget, one level down: documenting a function must never push it over its limit.
    @pytest.mark.unit
    def test_comments_do_not_count_towards_the_function_budget(self, tmp_path: Path) -> None:
        body = "\n".join(["    # why this step exists", "    total += 1"] * 150)
        source = f"def accumulate() -> int:\n    total = 0\n{body}\n    return total\n"
        _write_source(tmp_path / "project" / "core" / "documented_function.py", source)

        issues = collect_module_size_issues(tmp_path)

        assert issues == []

    # Reporting one per run would turn a single refactor into a queue of gate failures.
    @pytest.mark.unit
    def test_every_oversized_function_is_reported_not_only_the_longest(
        self, tmp_path: Path
    ) -> None:
        block = "\n".join(["    x = 1"] * (MAX_FUNCTION_CODE_LINES + 1))
        source = f"def first() -> None:\n{block}\n\n\ndef second() -> None:\n{block}\n"
        _write_source(tmp_path / "project" / "core" / "two_long.py", source)

        issues = collect_module_size_issues(tmp_path)

        assert sorted(issue.symbol for issue in issues) == ["first", "second"]

    # A bare method name would be ambiguous in a module with several classes, so the report carries the enclosing class.
    @pytest.mark.unit
    def test_methods_are_reported_with_their_qualified_name(self, tmp_path: Path) -> None:
        block = "\n".join(["        x = 1"] * (MAX_FUNCTION_CODE_LINES + 1))
        source = f"class Runner:\n    def run(self) -> None:\n{block}\n"
        _write_source(tmp_path / "project" / "core" / "runner.py", source)

        issues = collect_module_size_issues(tmp_path)

        assert [issue.symbol for issue in issues] == ["Runner.run"]

    # The axes are independent, so a module that is both too large and holds an oversized function reports both — the agent needs to see the whole job, not half of it.
    @pytest.mark.unit
    def test_both_axes_can_fire_on_one_module(self, tmp_path: Path) -> None:
        long_function = "\n".join(["    x = 1"] * (MAX_FUNCTION_CODE_LINES + 1))
        filler = "\n".join([f"y{index} = {index}" for index in range(MAX_CODE_LINES)])
        source = f"def big() -> None:\n{long_function}\n\n\n{filler}\n"
        _write_source(tmp_path / "project" / "core" / "both.py", source)

        issues = collect_module_size_issues(tmp_path)

        assert sorted({issue.rule_id for issue in issues}) == sorted({RULE_ID, FUNCTION_RULE_ID})

    # Regression guard: a non-UTF-8 .py file under project/** must surface as a structured ModuleSizeIssue with rule_id 'module_size.read_error', not crash with raw UnicodeDecodeError.
    @pytest.mark.unit
    def test_collect_emits_read_error_for_undecodable_file(self, tmp_path: Path) -> None:
        path = tmp_path / "project" / "core" / "binary_module.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        # 0xFF is invalid as the first byte of a UTF-8 sequence.
        path.write_bytes(b"\xff\xfe\xfd# not utf-8\n")

        issues = collect_module_size_issues(tmp_path)

        assert len(issues) == 1
        assert issues[0].rule_id == READ_ERROR_RULE_ID
        assert issues[0].line_count == 0
        assert issues[0].message is not None
        assert "UnicodeDecodeError" in issues[0].message

    # Regression guard for the argv-leak bug — running the validator with a typo'd flag must exit 2 (argparse 'unrecognized arguments') rather than silently exit 0 because main() was called without sys.argv[1:].
    @pytest.mark.unit
    def test_main_rejects_unknown_flag_via_argv(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/validate_module_sizes.py", "--not-a-real-flag"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "unrecognized arguments" in result.stderr
