# FILE: line_metrics.py
# SUMMARY: Classify every physical line of a Python module as code, comment, docstring, or blank, so size budgets can charge for logic instead of for documentation.
#
# Why this exists: the module-size budget used to be `len(source.splitlines())`, which charges for
# a comment exactly as it charges for a statement. The file that explains itself then hits the
# ceiling before the dense one does, and the cheapest way back under the limit is to delete the
# explanation — which is the opposite of what this kernel wants from an agent under a red gate.
#
# The effect is visible in this repository. Six modules here exceed 600 raw lines; five of them are
# genuinely large and stay over the budget once documentation is subtracted, and one is not:
# `project/core/logging/trace_formatter.py` is 646 raw lines and 423 executable ones, a third of it
# prose. Under a raw limit that file is a violation to be trimmed. Under this one it is fine, and
# the five that remain over are the ones actually worth splitting. Rerun the metric for current
# figures — the point is the shape of the difference, not the particular numbers.

from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass, field


# ATTRIBUTE: LINE_CODE (str)
# SUMMARY: Classification for a physical line that carries executable source.
LINE_CODE = "code"

# ATTRIBUTE: LINE_COMMENT (str)
# SUMMARY: Classification for a physical line whose only content is a `#` comment.
LINE_COMMENT = "comment"

# ATTRIBUTE: LINE_DOCSTRING (str)
# SUMMARY: Classification for a physical line occupied solely by a standalone string expression.
LINE_DOCSTRING = "docstring"

# ATTRIBUTE: LINE_BLANK (str)
# SUMMARY: Classification for a physical line that is empty or whitespace-only.
LINE_BLANK = "blank"

# ATTRIBUTE: STATUS_OK (str)
# SUMMARY: Analysis status when both tokenizer and AST parser accepted the source.
STATUS_OK = "ok"

# ATTRIBUTE: STATUS_UNPARSEABLE (str)
# SUMMARY: Analysis status when the source could not be tokenised or decoded; metrics fall back to raw counts.
STATUS_UNPARSEABLE = "unparseable"

# ATTRIBUTE: STATUS_SYNTAX_ERROR (str)
# SUMMARY: Analysis status when tokenising succeeded but the AST did not parse; line classification is still trustworthy, per-function metrics are not.
STATUS_SYNTAX_ERROR = "syntax_error"


# DATACLASS: ai_context.line_metrics.FunctionSpan
# SUMMARY: One function or method with the number of code lines its body occupies.
@dataclass(slots=True)
class FunctionSpan:
    # ATTRIBUTE: qualified_name (str)
    # SUMMARY: Dotted name including enclosing classes and functions, e.g. `Cache.get.inner`.
    qualified_name: str

    # ATTRIBUTE: line (int)
    # SUMMARY: 1-based line of the `def` keyword, used to point the agent at the right place.
    line: int

    # ATTRIBUTE: code_lines (int)
    # SUMMARY: Code lines between the `def` line and the end of the body, decorators excluded.
    code_lines: int


# DATACLASS: ai_context.line_metrics.ModuleMetrics
# SUMMARY: Size metrics for a single Python module, separating logic from documentation.
@dataclass(slots=True)
class ModuleMetrics:
    # ATTRIBUTE: status (str)
    # SUMMARY: One of STATUS_OK, STATUS_SYNTAX_ERROR, STATUS_UNPARSEABLE.
    status: str

    # ATTRIBUTE: raw_lines (int)
    # SUMMARY: Physical line count, the metric the old budget used.
    raw_lines: int

    # ATTRIBUTE: code_lines (int)
    # SUMMARY: Lines carrying executable source. This is what size budgets charge for.
    code_lines: int

    # ATTRIBUTE: comment_lines (int)
    # SUMMARY: Lines whose only content is a `#` comment, including all CBM markup.
    comment_lines: int

    # ATTRIBUTE: docstring_lines (int)
    # SUMMARY: Lines occupied solely by a standalone string expression.
    docstring_lines: int

    # ATTRIBUTE: blank_lines (int)
    # SUMMARY: Empty or whitespace-only lines.
    blank_lines: int

    # ATTRIBUTE: functions (list[FunctionSpan])
    # SUMMARY: Every function and method found, in source order. Empty when the AST did not parse.
    functions: list[FunctionSpan] = field(default_factory=list)

    # ATTRIBUTE: detail (str)
    # SUMMARY: Human-readable reason when status is not STATUS_OK; empty otherwise.
    detail: str = ""

    # FUNCTION: ai_context.line_metrics.ModuleMetrics.longest_function
    # SUMMARY: Return the function with the most code lines, or None when the module declares none.
    # OUTPUT: (FunctionSpan | None): Largest function by code lines, ties broken by source order.
    def longest_function(self) -> FunctionSpan | None:
        if not self.functions:
            return None
        return max(self.functions, key=lambda span: (span.code_lines, -span.line))


# FUNCTION: decode_source
# SUMMARY: Decode module bytes to text, tolerating a UTF-8 BOM, and report failure instead of raising.
# OUTPUT: (tuple[str | None, str]): Decoded text and an empty reason, or None and the failure reason.
def decode_source(data: bytes) -> tuple[str | None, str]:
    # **LOGIC_STEP**: utf-8-sig strips a leading BOM when present and is otherwise identical to
    # utf-8, so a BOM-prefixed module is measured rather than reported as unreadable.
    try:
        return data.decode("utf-8-sig"), ""
    except UnicodeDecodeError as error:
        return None, f"UnicodeDecodeError while reading source: {error.reason}"


# FUNCTION: classify_lines
# SUMMARY: Label every physical line of a module as code, comment, docstring, or blank.
# OUTPUT: (tuple[list[str], str, str]): Per-line labels (index 0 is line 1), the analysis status, and a detail string.
def classify_lines(source: str) -> tuple[list[str], str, str]:
    lines = source.splitlines()
    labels = [LINE_BLANK if not line.strip() else LINE_CODE for line in lines]
    if not lines:
        return labels, STATUS_OK, ""

    # **LOGIC_STEP**: The tokenizer is the only reliable way to tell a real comment from a `#`
    # inside a string literal, so comment detection runs through it rather than through str.startswith.
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.COMMENT:
                continue
            row = token.start[0]
            if _prefix_is_blank(lines, row, token.start[1]):
                labels[row - 1] = LINE_COMMENT
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError) as error:
        return labels, STATUS_UNPARSEABLE, f"tokenize failed: {error}"

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
        return labels, STATUS_SYNTAX_ERROR, f"ast.parse failed: {error}"

    _mark_standalone_strings(tree, lines, labels)
    return labels, STATUS_OK, ""


# FUNCTION: compute_module_metrics
# SUMMARY: Measure one module, charging the size budget for executable lines only.
# OUTPUT: (ModuleMetrics): Metrics with a status describing how far the analysis got.
def compute_module_metrics(data: bytes) -> ModuleMetrics:
    source, decode_reason = decode_source(data)
    if source is None:
        return ModuleMetrics(
            status=STATUS_UNPARSEABLE,
            raw_lines=0,
            code_lines=0,
            comment_lines=0,
            docstring_lines=0,
            blank_lines=0,
            detail=decode_reason,
        )

    labels, status, detail = classify_lines(source)
    raw_lines = len(source.splitlines())

    # **LOGIC_STEP**: When the tokenizer refused the file, comment detection is unreliable, so the
    # budget falls back to raw lines. A file the tools cannot read must not slip under the limit
    # just because its comments could not be discounted.
    if status == STATUS_UNPARSEABLE:
        return ModuleMetrics(
            status=status,
            raw_lines=raw_lines,
            code_lines=raw_lines,
            comment_lines=0,
            docstring_lines=0,
            blank_lines=0,
            detail=detail,
        )

    metrics = ModuleMetrics(
        status=status,
        raw_lines=raw_lines,
        code_lines=labels.count(LINE_CODE),
        comment_lines=labels.count(LINE_COMMENT),
        docstring_lines=labels.count(LINE_DOCSTRING),
        blank_lines=labels.count(LINE_BLANK),
        detail=detail,
    )
    if status == STATUS_OK:
        metrics.functions = _collect_function_spans(ast.parse(source), labels)
    return metrics


# FUNCTION: _prefix_is_blank
# SUMMARY: Check whether everything before a column on a line is whitespace.
# INPUT: lines (list[str]): Module lines without terminators.
# INPUT: row (int): 1-based line number.
# INPUT: column (int): 0-based column where the token starts.
# OUTPUT: (bool): True when the token is the first non-whitespace content on its line.
def _prefix_is_blank(lines: list[str], row: int, column: int) -> bool:
    if row < 1 or row > len(lines):
        return False
    return not lines[row - 1][:column].strip()


# FUNCTION: _mark_standalone_strings
# SUMMARY: Label the lines of every standalone string expression — module, class, and function docstrings plus free-floating string statements — as documentation rather than code.
# INPUT: lines (list[str]): Module lines without terminators.
# INPUT: labels (list[str]): Per-line labels, mutated in place — this is where the result lands.
def _mark_standalone_strings(tree: ast.AST, lines: list[str], labels: list[str]) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        end_lineno = node.end_lineno
        end_col_offset = node.end_col_offset
        if end_lineno is None or end_col_offset is None:
            continue
        # **LOGIC_STEP**: Only claim the span when the string owns its first and last lines
        # outright. `x = 1; "note"` must stay code, and a trailing comment after the closing
        # quotes is tolerated because the comment already has its own label.
        if not _prefix_is_blank(lines, node.lineno, node.col_offset):
            continue
        tail = lines[end_lineno - 1][end_col_offset:].strip()
        if tail and not tail.startswith("#"):
            continue
        for row in range(node.lineno, end_lineno + 1):
            if labels[row - 1] == LINE_CODE:
                labels[row - 1] = LINE_DOCSTRING


# FUNCTION: _collect_function_spans
# SUMMARY: Walk the AST and record how many code lines each function or method body occupies.
# INPUT: labels (list[str]): Per-line labels produced by classify_lines.
# OUTPUT: (list[FunctionSpan]): One span per function or method, in source order.
def _collect_function_spans(tree: ast.AST, labels: list[str]) -> list[FunctionSpan]:
    spans: list[FunctionSpan] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = getattr(child, "name", None)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = f"{prefix}{child.name}"
                end = child.end_lineno or child.lineno
                # **LOGIC_STEP**: Count from the `def` line, not from the first decorator, so a
                # function does not grow in the report every time someone adds a decorator.
                spans.append(
                    FunctionSpan(
                        qualified_name=qualified,
                        line=child.lineno,
                        code_lines=_count_code_lines(labels, child.lineno, end),
                    )
                )
                visit(child, f"{qualified}.")
            elif isinstance(child, ast.ClassDef) and name:
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    spans.sort(key=lambda span: span.line)
    return spans


# FUNCTION: _count_code_lines
# SUMMARY: Count how many lines in an inclusive 1-based range are labelled as code.
# INPUT: start (int): 1-based first line of the range.
# INPUT: end (int): 1-based last line of the range.
def _count_code_lines(labels: list[str], start: int, end: int) -> int:
    lower = max(start, 1)
    upper = min(end, len(labels))
    return sum(1 for index in range(lower, upper + 1) if labels[index - 1] == LINE_CODE)


# FUNCTION: measure_path
# SUMMARY: Convenience wrapper that reads a file from disk and measures it.
# INPUT: path (object): Anything with a `read_bytes()` method, normally a pathlib.Path.
# OUTPUT: (ModuleMetrics): Metrics for the file, with STATUS_UNPARSEABLE when it cannot be read.
def measure_path(path: object) -> ModuleMetrics:
    read_bytes = getattr(path, "read_bytes", None)
    if read_bytes is None:
        raise TypeError("measure_path expects an object exposing read_bytes()")
    try:
        data = read_bytes()
    except OSError as error:
        return ModuleMetrics(
            status=STATUS_UNPARSEABLE,
            raw_lines=0,
            code_lines=0,
            comment_lines=0,
            docstring_lines=0,
            blank_lines=0,
            detail=f"OSError while reading source: {error}",
        )
    return compute_module_metrics(data)
