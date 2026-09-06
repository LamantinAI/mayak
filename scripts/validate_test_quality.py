#!/usr/bin/env python3
# FILE: validate_test_quality.py
# SUMMARY: Quality gate rejecting tests that cannot fail — constant assertions and assertion-free test bodies.

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ai_context.rendering import render_json
from ai_context.validator_contract import build_validator_issue_payload

# ATTRIBUTE: ROOT_DIR (Path)
# SUMMARY: Absolute repository root scanned by this validator.
ROOT_DIR = Path(__file__).resolve().parent.parent

# ATTRIBUTE: TESTS_DIRNAME (str)
# SUMMARY: Top-level directory holding every suite this validator inspects.
TESTS_DIRNAME = "tests"

# ATTRIBUTE: OPT_OUT_MARKER (str)
# SUMMARY: Comment marker that exempts one test from the assertion requirement, with a reason.
OPT_OUT_MARKER = "# no-assert-ok:"

# ATTRIBUTE: _ASSERTING_CALL_SUFFIXES (tuple[str, ...])
# SUMMARY: Method-name suffixes that count as an assertion when a test delegates to a helper or mock.
_ASSERTING_CALL_SUFFIXES = (
    "assert_called",
    "assert_called_once",
    "assert_any_call",
    "assert_has_calls",
)

# ATTRIBUTE: _ARGUMENTLESS_CALL_ASSERTIONS (frozenset[str])
# SUMMARY: Mock assertions that prove a call happened and say nothing about what was passed.
# Measured: a service that ignored the caller's `limit` and always sent its own value to the
# repository survived a full green suite, because the test asserted `assert_awaited_once()`
# and stopped there. The call happened, so the test passed; the argument never arrived, and nothing
# noticed. `assert_called_once_with(...)` is the same assertion with the missing half restored.
_ARGUMENTLESS_CALL_ASSERTIONS = frozenset(
    {
        "assert_called",
        "assert_called_once",
        "assert_awaited",
        "assert_awaited_once",
    }
)

_TEST_QUALITY_RULE_PLAYBOOKS: dict[str, dict[str, object]] = {
    "test.span_output_unpinned": {
        "meaning": (
            "A span in project/ records an outcome under span.output, and no test anywhere names "
            "that span. test.span_output_pinned only inspects tests that already look a span up, "
            "so a span nobody looks up is invisible to it — and that is exactly where a span "
            "reporting the wrong answer survives every gate."
        ),
        "suggested_fix": (
            "Add a test that finds the span's finish event by name and asserts its output, once "
            "per outcome the span can report. The sibling rule then keeps that assertion honest."
        ),
        "read_first": [
            "the module and line named in the message",
            "any test that already asserts a span's output, for the shape",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_test_quality.py",
        "likely_fix_shape": (
            "One test per outcome, asserting finish['kwargs']['data']['output'] against the value "
            "the span should report."
        ),
        "next_checks": [
            "uv run python scripts/validate_test_quality.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Every span that writes an output is named by at least one test."
        ),
    },
    "test.span_output_pinned": {
        "meaning": (
            "A test finds a span's finish event and never states what the span said. Spans "
            "carry the outcome an operator reads — row_found, row_count, row_written — and a "
            "test that only proves the span happened passes while that outcome is wrong."
        ),
        "suggested_fix": (
            "Assert the value: `assert finish['kwargs']['data']['output'] == {'row_found': "
            "False}`. Pin the whole mapping rather than one key, so a field appearing or "
            "disappearing is a failure too. `is not None`, a bare truthiness check, a length "
            "and a comparison against a variable do not count — each of them stays green on a "
            "span reporting the wrong answer, which is the defect this rule is about."
        ),
        "read_first": [
            "the test named in the message",
            "project/core/logging/logger.py",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_test_quality.py",
        "likely_fix_shape": (
            "State the span's output in an assertion, or delete the span lookup if the test is "
            "not about the span at all."
        ),
        "next_checks": [
            "uv run python scripts/validate_test_quality.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop once every test that looks up a span's finish event asserts its output."
        ),
    },
    "test.constant_assertion": {
        "meaning": (
            "A test makes an assertion that cannot fail — a constant (assert True, assert 1, "
            "assert 'text') or a value compared with itself (assert result == result). It passes "
            "no matter what the code does, so it contributes coverage and confidence without "
            "contributing verification."
        ),
        "suggested_fix": (
            "Assert the actual observable result of the code under test, or delete the test if "
            "there is nothing to observe yet."
        ),
        "read_first": [
            "the file referenced by the issue",
            "docs/agent_rules.md",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_test_quality.py",
        "likely_fix_shape": "Replace the constant with an assertion about the returned value or raised error.",
        "next_checks": [
            "uv run python scripts/validate_test_quality.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop once the test asserts a value produced by the code under test."
        ),
    },
    "test.no_assertion": {
        "meaning": (
            "A test function contains no assertion, no pytest.raises, no pytest.fail and no mock "
            "assert_* call. It can only fail by raising, so most regressions leave it green."
        ),
        "suggested_fix": (
            "Add an assertion about the observable outcome. If the test exists only to prove that "
            "a call does not raise, say so explicitly with a "
            f"'{OPT_OUT_MARKER} <reason>' comment on the def line."
        ),
        "read_first": [
            "the file referenced by the issue",
            "docs/agent_rules.md",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_test_quality.py",
        "likely_fix_shape": "Add one assertion, or annotate the deliberate smoke test with the opt-out marker.",
        "next_checks": [
            "uv run python scripts/validate_test_quality.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop once every reported test either asserts something or carries the opt-out marker."
        ),
    },
    "test.sql_constant_round_trip": {
        "meaning": (
            "A test hands the query constant back to the assertion that checks the query — "
            "`cursor.execute.assert_awaited_once_with(_SELECT_BY_ID, ...)` — and nowhere pins any "
            "clause of that constant as literal text. Both sides move together: edit the WHERE "
            "clause, reverse the ORDER BY, drop the DISTINCT, and this test still passes."
        ),
        "suggested_fix": (
            "Add one assertion that states the clause as text, e.g. "
            "`assert _SELECT_BY_ID.split(' WHERE ', 1)[1] == 'id = %s'`. Keep the round-trip "
            "assertion — it proves the parameters travelled; the literal proves the SQL is the "
            "SQL you meant."
        ),
        "read_first": [
            "the file referenced by the issue",
            ".agents/skills/add-vertical/SKILL.md",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_test_quality.py",
        "likely_fix_shape": (
            "One added assert comparing a split or slice of the query constant against a string "
            "literal, next to the existing execute assertion."
        ),
        "next_checks": [
            "uv run python scripts/validate_test_quality.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop once every query constant the module asserts against also has one clause pinned "
            "as literal text."
        ),
    },
    "test.call_assertion_without_arguments": {
        "meaning": (
            "The only thing a test verifies is that a collaborator was called — assert_called_once() "
            "or assert_awaited_once() with no arguments. It proves the wiring exists and says "
            "nothing about what travelled through it, so a handler that drops or replaces the "
            "caller's arguments keeps this test green."
        ),
        "suggested_fix": (
            "Name the arguments: assert_awaited_once_with(limit=7). If the call itself is the whole "
            f"contract, say so explicitly with a '{OPT_OUT_MARKER} <reason>' comment on the def line."
        ),
        "read_first": [
            "the file referenced by the issue",
            ".agents/skills/add-vertical/SKILL.md",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_test_quality.py",
        "likely_fix_shape": (
            "Replace the argumentless assertion with the *_with variant carrying the expected "
            "arguments, or add a separate assertion about the observable result."
        ),
        "next_checks": [
            "uv run python scripts/validate_test_quality.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop once each reported test states what was passed, or carries the opt-out marker."
        ),
    },
}


# DATACLASS: validate_test_quality.TestQualityIssue
# SUMMARY: One test that cannot fail, located precisely enough to fix.
@dataclass(slots=True)
class TestQualityIssue:
    # ATTRIBUTE: path (Path)
    # SUMMARY: Absolute path of the test module.
    path: Path

    # ATTRIBUTE: line (int)
    # SUMMARY: 1-based line of the offending construct.
    line: int

    # ATTRIBUTE: rule_id (str)
    # SUMMARY: Stable rule identifier.
    rule_id: str

    # ATTRIBUTE: message (str)
    # SUMMARY: Human-readable description.
    message: str

    # ATTRIBUTE: category (str)
    # SUMMARY: Top-level issue category used in structured validator output.
    category: str = "test_quality"


# FUNCTION: get_test_quality_rule_playbook
# SUMMARY: Return the shared remediation playbook for a stable test-quality rule ID.
# OUTPUT: (dict[str, object] | None): Remediation metadata, or None when the rule is unknown.
def get_test_quality_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _TEST_QUALITY_RULE_PLAYBOOKS.get(rule_id)
    return dict(playbook) if playbook is not None else None


# FUNCTION: _is_pure_reference
# SUMMARY: Report whether an expression is a plain reference that reads the same twice in a row.
# OUTPUT: (bool): True for names, attributes, subscripts and literals built only from those.
def _is_pure_reference(node: ast.expr) -> bool:
    # **LOGIC_STEP**: Self-comparison is only provably vacuous when both sides are the same
    # side-effect-free read. `f() == f()` looks identical in the tree but may legitimately differ,
    # so calls, comprehensions and walrus assignments disqualify the expression.
    if isinstance(node, (ast.Name, ast.Constant)):
        return True
    if isinstance(node, ast.Attribute):
        return _is_pure_reference(node.value)
    if isinstance(node, ast.Subscript):
        return _is_pure_reference(node.value) and _is_pure_reference(node.slice)
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(_is_pure_reference(element) for element in node.elts)
    return False


# FUNCTION: _vacuous_assertion_reason
# SUMMARY: Classify an assertion that passes no matter what the code under test does.
# OUTPUT: (str | None): Short reason for the report, or None when the assertion can fail.
def _vacuous_assertion_reason(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant):
        return "asserts a constant" if node.value else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        if isinstance(node.operand, ast.Constant) and not node.operand.value:
            return "asserts a constant"
        return None
    # **LOGIC_STEP**: `assert value == value` is the tautology a constant check never sees: the
    # operands are expressions, not literals, so the node is an ast.Compare and the literal check
    # above returns False. Only always-true operators count — `!=` on identical operands is a
    # test that always fails, which the suite surfaces on its own.
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        (operator,) = node.ops
        (right,) = node.comparators
        if not isinstance(operator, (ast.Eq, ast.Is, ast.GtE, ast.LtE)):
            return None
        if not _is_pure_reference(node.left) or not _is_pure_reference(right):
            return None
        if ast.dump(node.left) == ast.dump(right):
            return "compares a value with itself"
    return None


# FUNCTION: _call_is_assertion
# SUMMARY: Report whether a call counts as verification (pytest.raises, pytest.fail, mock assert_*).
# INPUT: helpers (dict[str, ast.FunctionDef | ast.AsyncFunctionDef]): Functions defined in the same
#        module, used to look inside a local checker the test delegates to.
# INPUT: seen (set[str]): Helper names already being followed, so a cycle terminates.
# OUTPUT: (bool): True when the call verifies something.
def _call_is_assertion(
    node: ast.Call,
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    seen: set[str],
) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        name = func.attr
        return (
            name in {"raises", "fail", "warns"}
            or name.startswith("assert_")
            or name.endswith(_ASSERTING_CALL_SUFFIXES)
        )
    if isinstance(func, ast.Name):
        if func.id in {"fail", "raises"}:
            return True
        if "assert" not in func.id:
            return False
        # **LOGIC_STEP**: A test may delegate to a local checker (_assert_contract(payload)).
        # That is verification, just factored out — but only if the checker actually checks.
        # Matching the name alone made `def _assert_ok(): pass` count as a passed gate, so the
        # helper is followed into its body. A helper this module does not define (imported from
        # conftest, say) is unresolvable here and stays trusted.
        target = helpers.get(func.id)
        if target is None:
            return True
        if func.id in seen:
            return False
        return _function_verifies_something(target, helpers, seen | {func.id})
    return False


# FUNCTION: _is_argumentless_call_assertion
# SUMMARY: Report whether a call only proves that a collaborator was called, with no expected arguments.
# OUTPUT: (bool): True for mock.assert_called_once() and friends invoked with no arguments at all.
def _is_argumentless_call_assertion(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in _ARGUMENTLESS_CALL_ASSERTIONS:
        return False
    # **LOGIC_STEP**: The *_with variants carry the expectation in their arguments and are exactly
    # what this rule asks for, so anything with an argument is left alone.
    return not node.args and not node.keywords


# FUNCTION: _verification_strength
# SUMMARY: Split a test body's verification into "states an expectation" and "only proves a call happened".
# INPUT: helpers (dict[str, ast.FunctionDef | ast.AsyncFunctionDef]): Sibling functions, so a
#        delegated checker can be followed into its body.
# OUTPUT: (tuple[bool, ast.Call | None]): Whether a real expectation exists, and the first
#         argumentless call assertion found.
def _verification_strength(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> tuple[bool, ast.Call | None]:
    # **LOGIC_STEP**: The whole body is walked with no early exit. Returning on the first real
    # assertion looked equivalent and was not: ast.walk visits an `assert` on line 3 before the
    # weak call on line 4, so the weak call was never seen in exactly the mixed case this rule
    # exists to report.
    states_expectation = False
    weak_call: ast.Call | None = None
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            states_expectation = True
            continue
        if not isinstance(child, ast.Call):
            continue
        if _is_argumentless_call_assertion(child):
            weak_call = weak_call or child
            continue
        if _call_is_assertion(child, helpers, set()):
            states_expectation = True
    return states_expectation, weak_call


# FUNCTION: _carries_opt_out_marker
# SUMMARY: Report whether the comment block above a test carries the explicit opt-out marker.
# INPUT: source_lines (list[str]): The module's lines, used to read the block above the definition.
# OUTPUT: (bool): True when the author stated a reason for the missing verification.
def _carries_opt_out_marker(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
) -> bool:
    decorator_start = min(
        (decorator.lineno for decorator in node.decorator_list),
        default=node.lineno,
    )
    # **LOGIC_STEP**: Walk up through the contiguous comment/decorator block above the
    # definition, because the CBM header comments sit above the decorators, and that is
    # where a reader naturally writes the opt-out reason.
    block_start = decorator_start - 1
    while block_start > 0:
        previous = source_lines[block_start - 1].strip()
        if not previous.startswith(("#", "@")):
            break
        block_start -= 1
    return any(OPT_OUT_MARKER in line for line in source_lines[block_start : node.lineno])


# FUNCTION: _is_fixture
# SUMMARY: Report whether a function is a pytest fixture rather than a test.
# OUTPUT: (bool): True when any decorator is a pytest fixture.
def _is_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return True
        if isinstance(target, ast.Name) and target.id == "fixture":
            return True
    return False


# FUNCTION: _function_verifies_something
# SUMMARY: Report whether a test body contains any form of verification.
# INPUT: helpers (dict[str, ast.FunctionDef | ast.AsyncFunctionDef]): Functions defined in the same
#        module, so a delegated checker can be followed into its body.
# INPUT: seen (set[str]): Helper names already being followed, so a cycle terminates.
# OUTPUT: (bool): True when at least one assert or asserting call is present.
def _function_verifies_something(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    seen: set[str] | None = None,
) -> bool:
    followed = seen or set()
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            return True
        if isinstance(child, ast.Call) and _call_is_assertion(child, helpers, followed):
            return True
    return False


# FUNCTION: _module_functions
# SUMMARY: Index every function defined in a module by name, including methods on test classes.
# OUTPUT: (dict[str, ast.FunctionDef | ast.AsyncFunctionDef]): Name to definition.
def _module_functions(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


# ATTRIBUTE: _SQL_STATEMENT_PATTERN (re.Pattern[str])
# SUMMARY: Marks a module constant whose value is a SQL statement rather than some other string.
_SQL_STATEMENT_PATTERN = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)

# ATTRIBUTE: _CONSTANT_NAME_PATTERN (re.Pattern[str])
# SUMMARY: Screaming-snake names, with or without the leading underscore marking a private one.
_CONSTANT_NAME_PATTERN = re.compile(r"^_?[A-Z][A-Z0-9_]*$")

# ATTRIBUTE: _EXPECTATION_CALL_NAMES (frozenset[str])
# SUMMARY: Mock assertions that carry the expected call arguments, beyond the `*_with` family.
_EXPECTATION_CALL_NAMES = frozenset({"assert_any_call", "assert_has_calls"})


# FUNCTION: _literal_text
# SUMMARY: Return the fixed text of a string expression, ignoring interpolated parts.
# OUTPUT: (str): Concatenated literal segments, empty when the expression carries no text.
# NOTE: The shipped queries are f-strings — `f"SELECT {_COLUMNS} FROM reference_tasks WHERE id = %s"`
# — so a Constant-only check found no SQL anywhere and this rule silently applied to nothing. An
# f-string parses as ast.JoinedStr whose FormattedValue parts are code, not text; only the literal
# halves are read here, which is exactly the part a SQL keyword can appear in.
def _literal_text(node: ast.expr | None) -> str:
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    if isinstance(node, ast.JoinedStr):
        return "".join(_literal_text(value) for value in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_text(node.left) + _literal_text(node.right)
    return ""


# FUNCTION: _sql_constant_names
# SUMMARY: Return the module-level constants in one source file whose value is a SQL statement.
# OUTPUT: (set[str]): Constant names, empty when the file is unreadable or holds no SQL.
def _sql_constant_names(module_path: Path) -> set[str]:
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()

    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if not _SQL_STATEMENT_PATTERN.search(_literal_text(node.value)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


# FUNCTION: _imported_sql_constants
# SUMMARY: Return the local names a test module imported that hold a SQL statement in their own module.
# INPUT: repo_root (Path): Repository root, used to resolve a dotted `project.` module to a file.
# OUTPUT: (set[str]): Local names as written in this test module, including `as` aliases.
def _imported_sql_constants(tree: ast.AST, repo_root: Path) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None or node.level:
            continue
        if not node.module.startswith("project."):
            continue
        module_path = repo_root.joinpath(*node.module.split(".")).with_suffix(".py")
        if not module_path.is_file():
            continue
        sql_names = _sql_constant_names(module_path)
        for alias in node.names:
            local = alias.asname or alias.name
            if alias.name in sql_names and _CONSTANT_NAME_PATTERN.match(local):
                imported.add(local)
    return imported


# FUNCTION: _referenced_names
# SUMMARY: Return every plain name read anywhere inside one expression.
# OUTPUT: (set[str]): Identifiers, so `_SELECT.split(" WHERE ", 1)[1]` reports `_SELECT`.
def _referenced_names(node: ast.expr) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


# FUNCTION: _is_expectation_call
# SUMMARY: Report whether a call is a mock assertion carrying the expected arguments.
# OUTPUT: (bool): True for the `*_with` family, assert_any_call and assert_has_calls.
def _is_expectation_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    return func.attr.endswith("_with") or func.attr in _EXPECTATION_CALL_NAMES


# FUNCTION: _query_constant_round_trips
# SUMMARY: Find where a test hands a query constant to the assertion that is supposed to check the query.
# OUTPUT: (dict[str, int]): Constant name to the first line that compares it with itself.
def _query_constant_round_trips(tree: ast.AST, constants: set[str]) -> dict[str, int]:
    lines: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_expectation_call(node):
            arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
            for argument in arguments:
                for name in _referenced_names(argument) & constants:
                    lines.setdefault(name, node.lineno)
            continue
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.Eq):
            continue
        (right,) = node.comparators
        for expression, other in ((node.left, right), (right, node.left)):
            # **LOGIC_STEP**: A comparison against a string literal is the pin this rule asks for,
            # not a round trip; it is counted by _query_constants_pinned_as_text below.
            if isinstance(other, ast.Constant):
                continue
            for name in _referenced_names(expression) & constants:
                lines.setdefault(name, node.lineno)
    return lines


# FUNCTION: _query_constants_pinned_as_text
# SUMMARY: Find the query constants the module states as literal text at least once.
# OUTPUT: (set[str]): Constants compared, whole or sliced, against a string literal.
def _query_constants_pinned_as_text(tree: ast.AST, constants: set[str]) -> set[str]:
    pinned: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.Eq):
            continue
        (right,) = node.comparators
        for expression, other in ((node.left, right), (right, node.left)):
            if isinstance(other, ast.Constant) and isinstance(other.value, str):
                pinned |= _referenced_names(expression) & constants
    return pinned


# FUNCTION: _query_round_trip_issues
# SUMMARY: Report every query constant this module checks only against itself.
# INPUT: repo_root (Path): Repository root, used to resolve the module the constant came from.
# OUTPUT: (list[TestQualityIssue]): One issue per unpinned constant, at its first round trip.
# NOTE: Measured 2026-08-13 by injecting four SQL defects — INNER JOIN for LEFT, COUNT for
# COUNT(DISTINCT), an inverted close condition, `<` for `<=` on a week boundary. All four were
# caught, and the control probe showed what caught them: not a gate, but one sentence in
# .agents/skills/add-vertical telling the author to
# pin the clause as text. Remove those assertions and three of the four sail through
# `make quality-gates`, red only in `make test-e2e`. This rule is that sentence, machine-checked —
# it cannot see that the SQL is wrong, but it can see that nothing in the module would notice.
def _query_round_trip_issues(
    tree: ast.AST,
    path: Path,
    repo_root: Path,
) -> list[TestQualityIssue]:
    constants = _imported_sql_constants(tree, repo_root)
    if not constants:
        return []

    pinned = _query_constants_pinned_as_text(tree, constants)
    issues: list[TestQualityIssue] = []
    for name, line in sorted(_query_constant_round_trips(tree, constants).items()):
        if name in pinned:
            continue
        issues.append(
            TestQualityIssue(
                path=path,
                line=line,
                rule_id="test.sql_constant_round_trip",
                message=(
                    f"This module checks the query against {name} itself and never states any "
                    f"clause of {name} as literal text, so editing the query keeps every "
                    f"assertion here green. Add e.g. "
                    f"assert {name}.split(' WHERE ', 1)[1] == 'id = %s'."
                ),
            )
        )
    return issues


# ATTRIBUTE: _SPAN_FINISH_EVENT_ID (str)
# SUMMARY: The event a span writes when it closes; the only one that ever carries `output`.
_SPAN_FINISH_EVENT_ID = "span.finish"


# FUNCTION: _reads_event_id
# SUMMARY: Report whether an expression reads the `event_id` field off a captured log record.
# OUTPUT: (bool): True for `entry.get("event_id")` and `entry["event_id"]`.
def _reads_event_id(node: ast.expr) -> bool:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    ):
        return bool(node.args[0].value == "event_id")
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return bool(node.slice.value == "event_id")
    return False


# FUNCTION: _looks_up_a_span_finish
# SUMMARY: Report whether a function body filters captured events down to a span's finish.
# OUTPUT: (bool): True when it compares an event_id read against the span.finish literal.
# NOTE: Deliberately shallow: a helper that CONSTRUCTS an event carrying that id — a fixture
# building a log line for the trace formatter's tests — is not looking one up, and must not be
# read as a test about a span.
def _looks_up_a_span_finish(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Compare) or len(child.ops) != 1:
            continue
        if not isinstance(child.ops[0], ast.Eq):
            continue
        sides = (child.left, child.comparators[0])
        reads_id = any(_reads_event_id(side) for side in sides)
        names_finish = any(
            isinstance(side, ast.Constant) and side.value == _SPAN_FINISH_EVENT_ID for side in sides
        )
        if reads_id and names_finish:
            return True
    return False


# FUNCTION: _touches_the_output_key
# SUMMARY: Report whether an expression reads the `output` field of a captured span event.
# OUTPUT: (bool): True for `event["output"]`, `event.output` and `event.get("output")`.
def _touches_the_output_key(expression: ast.AST) -> bool:
    for inner in ast.walk(expression):
        if isinstance(inner, ast.Subscript) and isinstance(inner.slice, ast.Constant):
            if inner.slice.value == "output":
                return True
        if isinstance(inner, ast.Attribute) and inner.attr == "output":
            return True
        if (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "get"
            and inner.args
            and isinstance(inner.args[0], ast.Constant)
            and inner.args[0].value == "output"
        ):
            return True
    return False


# FUNCTION: _is_none
# SUMMARY: Report whether an expression is the literal None.
def _is_none(expression: ast.AST) -> bool:
    return isinstance(expression, ast.Constant) and expression.value is None


# ATTRIBUTE: _WILDCARD_NAMES (frozenset[str])
# SUMMARY: Objects that compare equal to anything, so an equality against one states nothing.
_WILDCARD_NAMES = frozenset({"ANY"})


# FUNCTION: _wildcard_aliases
# SUMMARY: Every local name in a module that refers to mock.ANY.
# OUTPUT: (set[str]): "ANY" plus any name it was imported or assigned as.
# NOTE: `from unittest.mock import ANY as WHATEVER` is visible right here in the module's imports,
# so following it costs nothing. What is not followed is a wildcard arriving from another module
# or built at runtime; the rule's note says so rather than claiming to catch every spelling.
def _wildcard_aliases(tree: ast.AST) -> set[str]:
    aliases = set(_WILDCARD_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if imported.name in _WILDCARD_NAMES and imported.asname:
                    aliases.add(imported.asname)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
            if node.value.id in aliases:
                aliases.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return aliases


# FUNCTION: _is_a_wildcard
# SUMMARY: Report whether an expression is mock.ANY under a name this module gave it.
def _is_a_wildcard(expression: ast.AST, aliases: set[str] | None = None) -> bool:
    known = aliases or _WILDCARD_NAMES
    if isinstance(expression, ast.Name):
        return expression.id in known
    if isinstance(expression, ast.Attribute):
        return expression.attr in _WILDCARD_NAMES
    return False


# FUNCTION: _bound_names
# SUMMARY: Every name a binding target introduces, including tuple and list unpacking.
def _bound_names(target: ast.AST) -> list[str]:
    return [node.id for node in ast.walk(target) if isinstance(node, ast.Name)]


# FUNCTION: _names_holding_the_output
# SUMMARY: Local names a function bound to the span's output, however they were bound.
# OUTPUT: (set[str]): Every name that holds the value the span reported.
# NOTE: The tautology guard below compares the two sides of an equality, and a tautology survives
# one `expected = finish[...]["output"]` line above the assert: neither side reads the key twice,
# so a purely syntactic guard sees two different expressions. Following the binding is what makes
# the guard about the value rather than about the spelling — through an assignment, a walrus, a
# loop variable, a `with ... as`, and unpacking, because a guard that covers only the first of
# those is a guard anybody can walk around by accident.
def _names_holding_the_output(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    held: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Assign) and _touches_the_output_key(child.value):
            for target in child.targets:
                held.update(_bound_names(target))
        elif (
            isinstance(child, ast.AnnAssign)
            and child.value is not None
            and _touches_the_output_key(child.value)
        ):
            held.update(_bound_names(child.target))
        elif isinstance(child, ast.NamedExpr) and _touches_the_output_key(child.value):
            held.update(_bound_names(child.target))
        elif isinstance(child, (ast.For, ast.AsyncFor)) and _touches_the_output_key(child.iter):
            held.update(_bound_names(child.target))
        elif isinstance(child, (ast.With, ast.AsyncWith)):
            for item in child.items:
                if item.optional_vars is not None and _touches_the_output_key(item.context_expr):
                    held.update(_bound_names(item.optional_vars))
    return held


# FUNCTION: _reads_the_output
# SUMMARY: Report whether an expression reads the span's output, directly or through a local name.
def _reads_the_output(expression: ast.AST, held: set[str]) -> bool:
    if _touches_the_output_key(expression):
        return True
    return any(isinstance(inner, ast.Name) and inner.id in held for inner in ast.walk(expression))


# FUNCTION: _pins_a_value
# SUMMARY: Report whether an expression states what a value IS, rather than that it exists.
# OUTPUT: (bool): True for an equality against anything stated, or membership in a literal set.
# NOTE: What is refused here is the existence check — `is not None`, a bare truthiness assert,
# `len(...) > 0`, a key `in` the mapping. Each satisfies "the test mentions output" while proving
# nothing about what the span reported, which is the whole defect this rule exists to catch.
#
# What is accepted is deliberately wide: `== pytest.approx(1.2)`, `== SpanOutput(row_found=False)`,
# `== f"row:{row_id}"` and a value from a parametrize table are all explicit statements of the
# expected output, and an earlier version that demanded a bare literal refused every one of them.
# A rule that fires on correct work teaches people to reach for the opt-out marker.
#
# Two equalities are still refused, because neither states anything. `== ANY` compares equal to
# whatever the span reported, under any name this module gave it. And the tautology — both sides
# reading the same span, whether spelled out twice or bound to a local name first — is the shape
# test.sql_constant_round_trip refuses for the same reason: both sides move together.
#
# The refusals are read off this module's own syntax, so an assertion assembled elsewhere gets
# through: a wildcard handed in by a fixture, an expected value computed by a helper out of the
# same record, a comparison built at runtime. Those take deliberate work to write. This rule is a
# check for the presence of a real assertion, the way test.sql_constant_round_trip is a check for
# the presence of a trap — neither can tell you the assertion is the right one.
def _pins_a_value(
    expression: ast.AST,
    held: set[str] | None = None,
    wildcards: set[str] | None = None,
) -> bool:
    held = held or set()
    collections = (ast.Dict, ast.List, ast.Tuple, ast.Set)
    for inner in ast.walk(expression):
        if not isinstance(inner, ast.Compare):
            continue
        for operator, right in zip(inner.ops, inner.comparators):
            if isinstance(operator, (ast.Eq, ast.NotEq)):
                if _is_none(inner.left) or _is_none(right):
                    continue
                if _is_a_wildcard(inner.left, wildcards) or _is_a_wildcard(right, wildcards):
                    continue
                if _reads_the_output(inner.left, held) and _reads_the_output(right, held):
                    continue
                return True
            if isinstance(operator, (ast.In, ast.NotIn)) and isinstance(right, collections):
                return True
    return False


# FUNCTION: _states_a_span_output
# SUMMARY: Report whether one assertion both reads the span's output and says what it holds.
# OUTPUT: (bool): True when the same assert touches `output` and pins a value in it.
def _states_a_span_output(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    wildcards: set[str] | None = None,
) -> bool:
    held = _names_holding_the_output(node)
    for child in ast.walk(node):
        if isinstance(child, ast.Assert) and _touches_the_output_key(child.test):
            if _pins_a_value(child.test, held, wildcards):
                return True
    return False


# FUNCTION: _reads_output_key
# SUMMARY: Report whether a function reads the `output` field of a captured event.
# OUTPUT: (bool): True when it subscripts or gets "output" anywhere in its body.
def _reads_output_key(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Subscript) and isinstance(child.slice, ast.Constant):
            if child.slice.value == "output":
                return True
        if isinstance(child, ast.Attribute) and child.attr == "output":
            return True
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "get"
            and child.args
            and isinstance(child.args[0], ast.Constant)
            and child.args[0].value == "output"
        ):
            return True
    return False


# FUNCTION: _compares_to_a_literal
# SUMMARY: Report whether some assertion states a concrete value rather than mere truthiness.
# OUTPUT: (bool): True when an assert compares against a literal.
def _compares_to_a_literal(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    wildcards: set[str] | None = None,
) -> bool:
    held = _names_holding_the_output(node)
    return any(
        isinstance(child, ast.Assert) and _pins_a_value(child.test, held, wildcards)
        for child in ast.walk(node)
    )


# FUNCTION: _argument_names
# SUMMARY: Every parameter name of a function, including keyword-only ones.
def _argument_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    arguments = node.args
    return [
        argument.arg
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    ]


# FUNCTION: _span_output_issues
# SUMMARY: Report tests that find a span and never say what it reported.
# INPUT: helpers (dict[str, ast.FunctionDef | ast.AsyncFunctionDef]): Module-level functions, so a
#        test calling a locator defined beside it counts as looking a span up.
# OUTPUT: (list[TestQualityIssue]): One issue per unpinned test.
# NOTE: A span's `output` is the only part of it an operator reads for an answer — whether a row
# was found, how many came back, whether a write happened. Measured on 2026-09-03: mutating a
# repository span to report `row_written = True` unconditionally left every gate green, because
# the tests proved the span existed and never read what it said. A trace that can lie is worse
# than no trace, because it is believed.
def _span_output_issues(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    path: Path,
    wildcards: set[str] | None = None,
) -> list[TestQualityIssue]:
    looks_up = _looks_up_a_span_finish(node)
    # **LOGIC_STEP**: A helper that already narrows to `output` hands the test the payload
    # directly, so the test asserts on a plain name and mentions `output` nowhere. Reading the
    # helper is what tells the two apart — without it this rule fires on tests that pin the value
    # perfectly well, which is the fastest way to teach everyone to add the opt-out marker.
    helper_extracts_output = False
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            helper = helpers.get(child.func.id)
            if helper is None:
                continue
            if _looks_up_a_span_finish(helper):
                looks_up = True
                if _reads_output_key(helper):
                    helper_extracts_output = True
    # **LOGIC_STEP**: A fixture reaches the test by name in its signature, never as a call, so
    # reading calls alone missed it entirely: moving the span lookup into a fixture and asserting
    # `is not None` on what it returned passed this rule with nothing else changed. Fixtures
    # declared in a conftest are still out of reach — this validator reads one module at a time —
    # and the message says what the rule cannot see rather than implying it saw everything.
    for argument in _argument_names(node):
        fixture = helpers.get(argument)
        if fixture is None or not _looks_up_a_span_finish(fixture):
            continue
        looks_up = True
        if _reads_output_key(fixture):
            helper_extracts_output = True
    if not looks_up:
        return []
    if _states_a_span_output(node, wildcards) or (
        helper_extracts_output and _compares_to_a_literal(node, wildcards)
    ):
        return []
    return [
        TestQualityIssue(
            path=path,
            line=node.lineno,
            rule_id="test.span_output_pinned",
            message=(
                f"Test '{node.name}' finds a span's finish event and never states what its "
                "output holds. A span reporting the wrong outcome passes this test. An "
                "existence check is not a statement: compare the output to a literal."
            ),
        )
    ]


# FUNCTION: validate_test_module
# SUMMARY: Inspect one test module for tests that cannot fail.
# INPUT: path (Path): Absolute path of the test module.
# INPUT: repo_root (Path | None): Repository root used to resolve imported query constants.
# OUTPUT: (list[TestQualityIssue]): Issues found in this module.
def validate_test_module(path: Path, repo_root: Path | None = None) -> list[TestQualityIssue]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        # **LOGIC_STEP**: Unreadable or unparseable test modules are already reported by ruff and
        # by pytest collection itself; this validator stays silent rather than duplicating them.
        return []

    source_lines = source.splitlines()
    issues: list[TestQualityIssue] = _query_round_trip_issues(tree, path, repo_root or ROOT_DIR)
    helpers = _module_functions(tree)
    wildcards = _wildcard_aliases(tree)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        # **LOGIC_STEP**: A fixture may legitimately be named test_settings. pytest decides what
        # is a test by the fixture decorator, not by the name, so this validator must too.
        if _is_fixture(node):
            continue

        for child in ast.walk(node):
            if not isinstance(child, ast.Assert):
                continue
            reason = _vacuous_assertion_reason(child.test)
            if reason is None:
                continue
            issues.append(
                TestQualityIssue(
                    path=path,
                    line=child.lineno,
                    rule_id="test.constant_assertion",
                    message=(
                        f"Test '{node.name}' {reason}, so it passes regardless of "
                        "the behaviour under test."
                    ),
                )
            )

        issues.extend(_span_output_issues(node, helpers, path, wildcards))

        states_expectation, weak_call = _verification_strength(node, helpers)

        # **LOGIC_STEP**: A test whose whole verification is "the collaborator was called" passes
        # while the arguments are dropped or replaced. It is reported separately from a test with
        # no verification at all, because the fix is different: name the expected arguments.
        if states_expectation:
            if weak_call is not None and not _carries_opt_out_marker(node, source_lines):
                issues.append(
                    TestQualityIssue(
                        path=path,
                        line=weak_call.lineno,
                        rule_id="test.call_assertion_without_arguments",
                        message=(
                            f"Test '{node.name}' asserts that a call happened without saying what "
                            "was passed. Use the *_with variant, or mark it "
                            f"'{OPT_OUT_MARKER} <reason>'."
                        ),
                    )
                )
            continue

        if weak_call is not None and not _carries_opt_out_marker(node, source_lines):
            issues.append(
                TestQualityIssue(
                    path=path,
                    line=weak_call.lineno,
                    rule_id="test.call_assertion_without_arguments",
                    message=(
                        f"Test '{node.name}' verifies nothing but the fact that a call happened. "
                        "Use the *_with variant, or mark it "
                        f"'{OPT_OUT_MARKER} <reason>'."
                    ),
                )
            )
            continue

        # **LOGIC_STEP**: A deliberate "this must not raise" smoke test is legitimate, but it has
        # to say so — otherwise an assertion-free body is indistinguishable from an unfinished one.
        if _carries_opt_out_marker(node, source_lines):
            continue

        issues.append(
            TestQualityIssue(
                path=path,
                line=node.lineno,
                rule_id="test.no_assertion",
                message=(
                    f"Test '{node.name}' contains no assertion, no pytest.raises and no "
                    f"assert_* call. Add one, or mark it '{OPT_OUT_MARKER} <reason>'."
                ),
            )
        )

    return issues


# ATTRIBUTE: PROJECT_DIRNAME (str)
# SUMMARY: The application package scanned for spans that record an outcome.
PROJECT_DIRNAME = "project"


# FUNCTION: _span_name_of
# SUMMARY: The literal name a `with logger.span("...") as span:` statement opens.
# OUTPUT: (tuple[str, str] | None): (span name, the variable it is bound to), or None.
def _span_name_of(node: ast.With | ast.AsyncWith) -> tuple[str, str] | None:
    for item in node.items:
        call = item.context_expr
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        if call.func.attr != "span" or not call.args:
            continue
        name = call.args[0]
        if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
            continue
        if not isinstance(item.optional_vars, ast.Name):
            continue
        return name.value, item.optional_vars.id
    return None


# FUNCTION: _spans_recording_an_outcome
# SUMMARY: Every span in one module that assigns to `span.output[...]`.
# OUTPUT: (list[tuple[str, int]]): (span name, line of the assignment).
def _spans_recording_an_outcome(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        opened = _span_name_of(node)
        if opened is None:
            continue
        span_name, variable = opened
        # **LOGIC_STEP**: The span's own name and any local alias of it, because `handle = span`
        # one line down is an ordinary thing to write and the write through it is the same write.
        aliases = {variable}
        # **LOGIC_STEP**: Repeated until it stops growing. ast.walk visits breadth-first, so a
        # single pass missed `b = a` whenever `a = span` sat one level deeper — the chain was read
        # out of order and the write through `b` disappeared.
        growing = True
        while growing:
            growing = False
            for child in ast.walk(node):
                if not isinstance(child, ast.Assign) or not isinstance(child.value, ast.Name):
                    continue
                if child.value.id not in aliases:
                    continue
                for target in child.targets:
                    if isinstance(target, ast.Name) and target.id not in aliases:
                        aliases.add(target.id)
                        growing = True
        for child in ast.walk(node):
            if not isinstance(child, ast.Assign):
                continue
            for target in child.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and target.value.attr == "output"
                    and isinstance(target.value.value, ast.Name)
                    and target.value.value.id in aliases
                ):
                    found.append((span_name, child.lineno))
    return found


# FUNCTION: _span_names_named_by_tests
# SUMMARY: Span names written in a test module that also looks a span's finish event up.
# NOTE: The module has to be about spans for its strings to count. Reading every string literal
# under tests/ meant a span name mentioned in a docstring, or listed for documentation, satisfied
# this rule while nothing exercised the span — the same silence the rule was added to break.
# Still deliberately loose within such a module: the point here is to notice a span nobody thought
# about, and test.span_output_pinned is what makes the test that names it prove something.
def _span_names_named_by_tests(tests_dir: Path) -> set[str]:
    named: set[str] = set()
    for path in sorted(tests_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        if not _looks_up_a_span_finish(tree):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                named.add(node.value)
    return named


# FUNCTION: _unpinned_span_output_issues
# SUMMARY: Report a span that records an outcome no test ever looks at.
# NOTE: test.span_output_pinned reads tests and asks whether the one that found a span said what
# the span reported. It is blind by construction to a span no test mentions — and that blindness
# shipped: `db.reference_task.update` recorded whether the row was still there, no test named it,
# and replacing that value with an unconditional True left all thirteen validators and 1010 tests
# green. Measured on 2026-09-06. This rule is the other half: production says which spans carry an
# outcome, and each of them has to be named somewhere in tests/.
#
# What this rule cannot see, stated rather than implied: a span opened under a computed name, and
# an output written by a helper function called from inside the span rather than in the block
# itself. Both need the write and the `with` in one place to be recognised. A write through a
# local alias of the span variable is seen.
def _unpinned_span_output_issues(repo_root: Path) -> list[TestQualityIssue]:
    project_dir = repo_root / PROJECT_DIRNAME
    tests_dir = repo_root / TESTS_DIRNAME
    if not project_dir.is_dir() or not tests_dir.is_dir():
        return []

    named = _span_names_named_by_tests(tests_dir)
    issues: list[TestQualityIssue] = []
    for path in sorted(project_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for span_name, line in _spans_recording_an_outcome(tree):
            if span_name in named:
                continue
            issues.append(
                TestQualityIssue(
                    path=path,
                    line=line,
                    rule_id="test.span_output_unpinned",
                    message=(
                        f"Span '{span_name}' records an outcome under span.output and no test "
                        "names it, so a span reporting the wrong answer passes every gate."
                    ),
                )
            )
    return issues


# FUNCTION: collect_test_quality_issues
# SUMMARY: Inspect every test module in the repository.
# INPUT: repo_root (Path): Repository root containing the tests/ tree.
# OUTPUT: (list[TestQualityIssue]): All issues found, ordered by path.
def collect_test_quality_issues(repo_root: Path) -> list[TestQualityIssue]:
    tests_dir = repo_root / TESTS_DIRNAME
    if not tests_dir.is_dir():
        return []

    issues: list[TestQualityIssue] = _unpinned_span_output_issues(repo_root)
    for path in sorted(tests_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        issues.extend(validate_test_module(path, repo_root))
    return issues


# FUNCTION: _issue_to_payload
# SUMMARY: Convert one issue into a JSON-serializable remediation payload.
# INPUT: repo_root (Path): Repository root used for relative-path rendering.
def _issue_to_payload(issue: TestQualityIssue, repo_root: Path) -> dict[str, object]:
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category=issue.category,
        file=str(issue.path.relative_to(repo_root)),
        line=issue.line,
        message=issue.message,
        playbook=get_test_quality_rule_playbook(issue.rule_id),
    )


# FUNCTION: main
# SUMMARY: Run test-quality validation and return a process exit code.
# OUTPUT: (int): Zero when every test can fail for a reason, non-zero otherwise.
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate that tests can actually fail.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured machine-readable validation results.",
    )
    args = parser.parse_args([] if argv is None else argv)

    issues = collect_test_quality_issues(ROOT_DIR)
    if not issues:
        if args.json:
            print(render_json({"status": "ok", "issues": []}), end="")
        else:
            print("Test quality validation passed.")
        return 0

    if args.json:
        print(
            render_json(
                {
                    "status": "error",
                    "issues": [_issue_to_payload(issue, ROOT_DIR) for issue in issues],
                }
            ),
            end="",
        )
        return 1

    for issue in issues:
        print(f"{issue.path.relative_to(ROOT_DIR)}:{issue.line}: [{issue.rule_id}] {issue.message}")
    return 1


# FUNCTION: __main__
# SUMMARY: Script entrypoint for the test-quality validator.
if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
