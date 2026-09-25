# FILE: line_metrics.py
# SUMMARY: Classify every physical line of a Python module as code, comment, docstring, or blank, so size budgets can charge for logic instead of for documentation.
#
# Why this exists: the module-size budget used to be `len(source.splitlines())`, which charges for
# a comment exactly as it charges for a statement. The file that explains itself then hits the
# ceiling before the dense one does, and the cheapest way back under the limit is to delete the
# explanation — which is the opposite of what this kernel wants from an agent under a red gate.
#
# The effect is visible in this repository, but any specific figure quoted here goes stale as soon
# as the file it describes changes — re-measure instead of trusting a number:
#
#   uv run python -c "from pathlib import Path; from validation_support.line_metrics import \
#     compute_module_metrics as m; print(sorted(((len(f.read_bytes().decode().splitlines()), \
#     m(f.read_bytes()).code_lines, str(f)) for f in Path('project').rglob('*.py')), \
#     reverse=True)[:3])"
#
# Over `project/` — the only tree `scripts/validate_module_sizes.py` walks — a module with a lot
# of explanatory comment can sit comfortably inside the executable-line budget while being well
# over a raw-line budget of the same size; a raw budget would make deleting those comments the
# cheapest way back under the limit.

from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass, field


# Classification for a physical line that carries executable source.
LINE_CODE = "code"

# Classification for a physical line whose only content is a `#` comment.
LINE_COMMENT = "comment"

# Classification for a physical line occupied solely by a standalone string expression.
LINE_DOCSTRING = "docstring"

# Classification for a physical line that is empty or whitespace-only.
LINE_BLANK = "blank"

# Analysis status when both tokenizer and AST parser accepted the source.
STATUS_OK = "ok"

# Analysis status when the source could not be tokenised or decoded; metrics fall back to raw counts.
STATUS_UNPARSEABLE = "unparseable"

# Analysis status when tokenising succeeded but the AST did not parse; line classification is still trustworthy, per-function metrics are not.
STATUS_SYNTAX_ERROR = "syntax_error"


# One function or method with the number of code lines its body occupies.
@dataclass(slots=True)
class FunctionSpan:
    # Dotted name including enclosing classes and functions, e.g. `Cache.get.inner`.
    qualified_name: str

    # 1-based line of the `def` keyword, used to point the agent at the right place.
    line: int

    # Code lines between the `def` line and the end of the body, decorators excluded.
    code_lines: int


# Size metrics for a single Python module, separating logic from documentation.
@dataclass(slots=True)
class ModuleMetrics:
    # One of STATUS_OK, STATUS_SYNTAX_ERROR, STATUS_UNPARSEABLE.
    status: str

    # Physical line count, the metric the old budget used.
    raw_lines: int

    # Lines carrying executable source. This is what size budgets charge for.
    code_lines: int

    # Lines whose only content is a `#` comment, the file header included.
    comment_lines: int

    # Lines occupied solely by a standalone string expression.
    docstring_lines: int

    # Empty or whitespace-only lines.
    blank_lines: int

    # Every function and method found, in source order. Empty when the AST did not parse.
    functions: list[FunctionSpan] = field(default_factory=list)

    # Human-readable reason when status is not STATUS_OK; empty otherwise.
    detail: str = ""

    # Return the function with the most code lines, or None when the module declares none.
    # Returns: Largest function by code lines, ties broken by source order.
    def longest_function(self) -> FunctionSpan | None:
        if not self.functions:
            return None
        return max(self.functions, key=lambda span: (span.code_lines, -span.line))


# Decode module bytes to text, tolerating a UTF-8 BOM, and report failure instead of raising.
# Returns: Decoded text and an empty reason, or None and the failure reason.
def decode_source(data: bytes) -> tuple[str | None, str]:
    # utf-8-sig strips a leading BOM when present and is otherwise identical to
    # utf-8, so a BOM-prefixed module is measured rather than reported as unreadable.
    try:
        return data.decode("utf-8-sig"), ""
    except UnicodeDecodeError as error:
        return None, f"UnicodeDecodeError while reading source: {error.reason}"


# Label every physical line of a module as code, comment, docstring, or blank.
# Returns: Per-line labels (index 0 is line 1), the analysis status, and a detail string.
def classify_lines(source: str) -> tuple[list[str], str, str]:
    lines = source.splitlines()
    labels = [LINE_BLANK if not line.strip() else LINE_CODE for line in lines]
    if not lines:
        return labels, STATUS_OK, ""

    # The tokenizer is the only reliable way to tell a real comment from a `#`
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


# Measure one module, charging the size budget for executable lines only.
# Returns: Metrics with a status describing how far the analysis got.
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

    # When the tokenizer refused the file, comment detection is unreliable, so the
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


# Check whether everything before a column on a line is whitespace.
# lines: Module lines without terminators.
# row: 1-based line number.
# column: 0-based column where the token starts.
# Returns: True when the token is the first non-whitespace content on its line.
def _prefix_is_blank(lines: list[str], row: int, column: int) -> bool:
    if row < 1 or row > len(lines):
        return False
    return not lines[row - 1][:column].strip()


# Label the lines of every standalone string expression — module, class, and function docstrings plus free-floating string statements — as documentation rather than code.
# lines: Module lines without terminators.
# labels: Per-line labels, mutated in place — this is where the result lands.
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
        # Only claim the span when the string owns its first and last lines
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


# Walk the AST and record how many code lines each function or method body occupies.
# labels: Per-line labels produced by classify_lines.
# Returns: One span per function or method, in source order.
def _collect_function_spans(tree: ast.AST, labels: list[str]) -> list[FunctionSpan]:
    spans: list[FunctionSpan] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = getattr(child, "name", None)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = f"{prefix}{child.name}"
                end = child.end_lineno or child.lineno
                # Count from the `def` line, not from the first decorator, so a
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


# Count how many lines in an inclusive 1-based range are labelled as code.
# start: 1-based first line of the range.
# end: 1-based last line of the range.
def _count_code_lines(labels: list[str], start: int, end: int) -> int:
    lower = max(start, 1)
    upper = min(end, len(labels))
    return sum(1 for index in range(lower, upper + 1) if labels[index - 1] == LINE_CODE)


# Convenience wrapper that reads a file from disk and measures it.
# path: Anything with a `read_bytes()` method, normally a pathlib.Path.
# Returns: Metrics for the file, with STATUS_UNPARSEABLE when it cannot be read.
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
