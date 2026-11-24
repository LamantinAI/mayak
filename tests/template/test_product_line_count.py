# FILE: tests/template/test_product_line_count.py
# SUMMARY: The inherited-code ceiling of a project made from the template counts lines of code only.
# Before 1.3.0 `make check-product` charged every non-empty line, so a comment cost as much as a
# statement; and the shared classifier split lines where the tokenizer does not, shifting labels.

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_product_from_template import count_python_lines
from validation_support.line_metrics import compute_module_metrics

# Each case: source bytes, then the lines of code it must count as.
_CASES = {
    "header, shebang and comment lines are free": (
        b"#!/usr/bin/env python3\n# FILE: m.py\n# SUMMARY: m\n# noqa: E501\nx = 1\n",
        1,
    ),
    "a statement with a trailing comment is one line": (
        b"import os  # noqa: F401\ny = []  # type: list[int]\n# type: ignore\n",
        2,
    ),
    "a hash inside a string or an f-string is not a comment": (
        b"s = '# not a comment'\nf = f'{s} # neither'\n",
        2,
    ),
    "unicode in comments and strings": ("# комментарий 🦆\nx = 'é'\n".encode(), 1),
    "docstrings and blank lines are free": (
        b'"""Module.\n\nMore.\n"""\n\n\ndef f():\n    """Doc."""\n    return 1\n',
        2,
    ),
    "a literal that is assigned or passed is code": (
        b'X = """\n# looks like a comment\ntext\n"""\nprint(\n    "a",\n    "b",\n)\n',
        8,
    ),
    "a docstring followed by code on its line stays code": (b'"""d"""; x = 1\n', 1),
    "CRLF line endings": (b"# c\r\nx = 1\r\n\r\ny = 2  # z\r\n", 2),
    "a byte-order mark": (b"\xef\xbb\xbf# c\nx = 1\n", 1),
    # str.splitlines() breaks at a form feed, U+0085 and U+2028; the tokenizer does not. Before
    # 1.3.0 every label after such a character shifted by one row, and code was counted as comment.
    "a form feed line": (b"# c\n\x0c\nx = 1  # inline\n# d\n", 1),
    "U+2028 inside a comment": ("# a b\nx = 1\n# z\n".encode(), 1),
    "U+0085 inside a string": ("s = 'a\x85b'\n# z\ny = 2\n".encode(), 2),
}


@pytest.mark.unit
@pytest.mark.parametrize(("source", "code_lines"), _CASES.values(), ids=list(_CASES))
def test_only_lines_of_code_are_counted(source: bytes, code_lines: int) -> None:
    metrics = compute_module_metrics(source)

    assert metrics.status == "ok"
    assert metrics.raw_lines == source.count(b"\n")
    assert metrics.code_lines == code_lines
    assert (
        metrics.code_lines + metrics.comment_lines + metrics.docstring_lines + metrics.blank_lines
        == metrics.raw_lines
    )


# A tree of one documented module: the old count charged its seven non-empty lines, the new one the
# two statements. The non-empty figure is still reported beside it, for comparison with old reports.
@pytest.mark.unit
def test_the_product_count_ignores_comments_and_docstrings(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(
        '# FILE: m.py\n# SUMMARY: m\n"""Doc."""\n\n# why\nx = 1  # inline\ny = f"#{x}"\n# end\n',
        encoding="utf-8",
    )
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "dep.py").write_text("z = 1\n", encoding="utf-8")

    assert count_python_lines(tmp_path) == (2, 7)


# A file the classifier cannot read must stop the check by name: counting its raw length would
# charge comments again, and counting it as zero would let its code through.
@pytest.mark.unit
@pytest.mark.parametrize(
    "content",
    [b"x = '\xff'\n", b"x = (\n# never closed\n"],
    ids=["undecodable", "untokenizable"],
)
def test_an_unreadable_file_is_a_measurement_error(tmp_path: Path, content: bytes) -> None:
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "broken.py").write_bytes(content)

    with pytest.raises(SystemExit, match=r"cannot measure broken\.py: unparseable"):
        count_python_lines(tmp_path)
