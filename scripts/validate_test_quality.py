#!/usr/bin/env python3
# FILE: validate_test_quality.py
# Quality gate rejecting tests that cannot fail — constant assertions and assertion-free test bodies.

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from validation_support.rendering import render_json
from validation_support.validator_contract import build_validator_issue_payload

# Absolute repository root scanned by this validator.
ROOT_DIR = Path(__file__).resolve().parent.parent

# Top-level directory holding every suite this validator inspects.
TESTS_DIRNAME = "tests"

# Comment marker that exempts one test from the assertion requirement, with a reason.
OPT_OUT_MARKER = "# no-assert-ok:"

# Method-name suffixes that count as an assertion when a test delegates to a helper or mock.
_ASSERTING_CALL_SUFFIXES = (
    "assert_called",
    "assert_called_once",
    "assert_any_call",
    "assert_has_calls",
)

# Mock assertions that prove a call happened and say nothing about what was passed.
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

# This module does not check whether a test LOOKS like it pins a span's output — whether a
# test that found a span's finish event states a value, or whether every outcome-bearing span is
# named by some test. That is a heuristic about presence, not about correctness: a new test style
# would keep needing the check widened to recognise it, and each widening opens a gap an unrelated
# shape could walk through — `assert x is not None and len(events) == 2` satisfies "the test
# mentions the span and makes an assertion" without proving anything about what the span reported.
# Span behaviour is what the direct trace tests in tests/infrastructure/ and tests/functional/
# exist to pin; this module checks only that a test's own assertions can fail, not what shape a
# test happens to take.
_TEST_QUALITY_RULE_PLAYBOOKS: dict[str, dict[str, object]] = {
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
            "Test the query where it runs: a tests/db test that stores rows and reads them back "
            "catches a wrong WHERE, ORDER BY or LIMIT by its result. If this mock test stays, add "
            "one assertion that states a clause as text, e.g. "
            "`assert _SELECT_BY_ID.split(' WHERE ', 1)[1] == 'id = %s'` — the literal proves the "
            "SQL is the SQL you meant; the round trip only proves the parameters travelled."
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


# One test that cannot fail, located precisely enough to fix.
@dataclass(slots=True)
class TestQualityIssue:
    # Absolute path of the test module.
    path: Path

    # 1-based line of the offending construct.
    line: int

    # Stable rule identifier.
    rule_id: str

    # Human-readable description.
    message: str

    # Top-level issue category used in structured validator output.
    category: str = "test_quality"


# Return the shared remediation playbook for a stable test-quality rule ID.
# Returns: Remediation metadata, or None when the rule is unknown.
def get_test_quality_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _TEST_QUALITY_RULE_PLAYBOOKS.get(rule_id)
    return dict(playbook) if playbook is not None else None


# Report whether an expression is a plain reference that reads the same twice in a row.
# Returns: True for names, attributes, subscripts and literals built only from those.
def _is_pure_reference(node: ast.expr) -> bool:
    # Self-comparison is only provably vacuous when both sides are the same
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


# Classify an assertion that passes no matter what the code under test does.
# Returns: Short reason for the report, or None when the assertion can fail.
def _vacuous_assertion_reason(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant):
        return "asserts a constant" if node.value else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        if isinstance(node.operand, ast.Constant) and not node.operand.value:
            return "asserts a constant"
        return None
    # `assert value == value` is the tautology a constant check never sees: the
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


# Report whether a call counts as verification (pytest.raises, pytest.fail, mock assert_*).
# helpers: Functions defined in the same
# module, used to look inside a local checker the test delegates to.
# seen: Helper names already being followed, so a cycle terminates.
# Returns: True when the call verifies something.
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
        # A test may delegate to a local checker (_assert_contract(payload)).
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


# Report whether a call only proves that a collaborator was called, with no expected arguments.
# Returns: True for mock.assert_called_once() and friends invoked with no arguments at all.
def _is_argumentless_call_assertion(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in _ARGUMENTLESS_CALL_ASSERTIONS:
        return False
    # The *_with variants carry the expectation in their arguments and are exactly
    # what this rule asks for, so anything with an argument is left alone.
    return not node.args and not node.keywords


# Split a test body's verification into "states an expectation" and "only proves a call happened".
# helpers: Sibling functions, so a
# delegated checker can be followed into its body.
# Returns: Whether a real expectation exists, and the first
# argumentless call assertion found.
def _verification_strength(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> tuple[bool, ast.Call | None]:
    # The whole body is walked with no early exit. Returning on the first real
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


# Report whether the comment block above a test carries the explicit opt-out marker.
# source_lines: The module's lines, used to read the block above the definition.
# Returns: True when the author stated a reason for the missing verification.
def _carries_opt_out_marker(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
) -> bool:
    decorator_start = min(
        (decorator.lineno for decorator in node.decorator_list),
        default=node.lineno,
    )
    # Walk up through the contiguous comment/decorator block above the
    # definition, because comments about a test sit above its decorators, and that is
    # where a reader naturally writes the opt-out reason.
    block_start = decorator_start - 1
    while block_start > 0:
        previous = source_lines[block_start - 1].strip()
        if not previous.startswith(("#", "@")):
            break
        block_start -= 1
    return any(OPT_OUT_MARKER in line for line in source_lines[block_start : node.lineno])


# Report whether a function is a pytest fixture rather than a test.
# Returns: True when any decorator is a pytest fixture.
def _is_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return True
        if isinstance(target, ast.Name) and target.id == "fixture":
            return True
    return False


# Report whether a test body contains any form of verification.
# helpers: Functions defined in the same
# module, so a delegated checker can be followed into its body.
# seen: Helper names already being followed, so a cycle terminates.
# Returns: True when at least one assert or asserting call is present.
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


# Index every function defined in a module by name, including methods on test classes.
# Returns: Name to definition.
def _module_functions(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


# Marks a module constant whose value is a SQL statement rather than some other string.
_SQL_STATEMENT_PATTERN = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)

# Screaming-snake names, with or without the leading underscore marking a private one.
_CONSTANT_NAME_PATTERN = re.compile(r"^_?[A-Z][A-Z0-9_]*$")

# Mock assertions that carry the expected call arguments, beyond the `*_with` family.
_EXPECTATION_CALL_NAMES = frozenset({"assert_any_call", "assert_has_calls"})


# Return the fixed text of a string expression, ignoring interpolated parts.
# Returns: Concatenated literal segments, empty when the expression carries no text.
# The shipped queries are f-strings — `f"SELECT {_COLUMNS} FROM reference_tasks WHERE id = %s"`
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


# Return the module-level constants in one source file whose value is a SQL statement.
# Returns: Constant names, empty when the file is unreadable or holds no SQL.
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


# Return the local names a test module imported that hold a SQL statement in their own module.
# repo_root: Repository root, used to resolve a dotted `project.` module to a file.
# Returns: Local names as written in this test module, including `as` aliases.
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


# Return every plain name read anywhere inside one expression.
# Returns: Identifiers, so `_SELECT.split(" WHERE ", 1)[1]` reports `_SELECT`.
def _referenced_names(node: ast.expr) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


# Return the names of an expression whose own text is what the comparison is about.
# node: The side of a comparison that is not the string literal.
# Returns: Identifiers reached through slicing, calls and attributes, but never a name
# used only as a subscript key.
# `_referenced_names` answers "does this expression mention the constant at all", which is
# the right question for a round trip and the wrong one for a pin. `results[_SELECT_BY_ID] ==
# "ok"` mentions the constant as a dictionary key and states nothing about the SQL, yet it used to
# mark the constant pinned and silence the rule for the whole module — the rule exists to notice
# exactly that silence. `_SELECT_BY_ID.split(" WHERE ", 1)[1] == "id = %s"` still counts: there
# the constant is the value being sliced, not the key doing the looking-up.
def _names_stated_as_text(node: ast.expr) -> set[str]:
    keys: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Subscript):
            keys |= _referenced_names(child.slice)
    return _referenced_names(node) - keys


# Report whether a call is a mock assertion carrying the expected arguments.
# Returns: True for the `*_with` family, assert_any_call and assert_has_calls.
def _is_expectation_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    return func.attr.endswith("_with") or func.attr in _EXPECTATION_CALL_NAMES


# Find where a test hands a query constant to the assertion that is supposed to check the query.
# Returns: Constant name to the first line that compares it with itself.
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
        # `is` states the identical tautology `==` does — both sides are the same
        # object, so the comparison passes for any query text. Reading only ast.Eq here would let
        # `assert sql is _SELECT_BY_STATUS` sail through as an unrecognised comparison instead of
        # the round trip it is.
        if not isinstance(node.ops[0], (ast.Eq, ast.Is)):
            continue
        (right,) = node.comparators
        for expression, other in ((node.left, right), (right, node.left)):
            # A comparison against a string literal is the pin this rule asks for,
            # not a round trip; it is counted by _query_constants_pinned_as_text below.
            if isinstance(other, ast.Constant):
                continue
            for name in _referenced_names(expression) & constants:
                lines.setdefault(name, node.lineno)
    return lines


# Whether a literal is specific enough to be a clause of a query rather than a word in one.
# value: The left operand of an `in` comparison, which need not be a string.
# Returns: True for a stripped literal carrying more than one whitespace-separated token.
def _is_clause_shaped(value: object) -> bool:
    return isinstance(value, str) and len(value.split()) > 1


# Find the query constants the module states as literal text at least once.
# Returns: Constants compared, whole or sliced, against a string literal.
def _query_constants_pinned_as_text(tree: ast.AST, constants: set[str]) -> set[str]:
    pinned: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        op = node.ops[0]
        (right,) = node.comparators
        if isinstance(op, ast.Eq):
            for expression, other in ((node.left, right), (right, node.left)):
                if isinstance(other, ast.Constant) and isinstance(other.value, str):
                    pinned |= _names_stated_as_text(expression) & constants
        elif isinstance(op, ast.In):
            # `"WHERE id = %s" in _SELECT_BY_ID` pins the same fact a sliced `==`
            # does. Reading only ast.Eq would leave this exact assertion — a correct pin, just
            # spelled with `in` — reported as an unpinned round trip. Only one direction makes
            # sense for `in`: the literal is the (necessarily shorter) needle, never the query
            # itself, so this is not mirrored the way `==` is above.
            # The needle has to be a clause, not a word. `assert "" in _SELECT`
            # is true of every string ever written and `assert "SELECT" in _SELECT` of every query,
            # so either would let one meaningless line silence the rule for a whole module — the
            # same silence, reached by a shorter road than the round trip this rule was built to
            # notice. With a one-word needle accepted, reversing `WHERE id = %s` to
            # `WHERE status = %s` would leave the module reported clean, where the unpinned base
            # is reported. Two words is the line: `ORDER BY created_at DESC` and `id = %s` move
            # when the clause moves; `SELECT` does not. This still only asks whether a trap is
            # present, never whether the text it pins is the right text.
            if isinstance(node.left, ast.Constant) and _is_clause_shaped(node.left.value):
                pinned |= _names_stated_as_text(right) & constants
    return pinned


# Report every query constant this module checks only against itself.
# repo_root: Repository root, used to resolve the module the constant came from.
# Returns: One issue per unpinned constant, at its first round trip.
# Measured 2026-08-13 by injecting four SQL defects — INNER JOIN for LEFT, COUNT for
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


# Inspect one test module for tests that cannot fail.
# path: Absolute path of the test module.
# repo_root: Repository root used to resolve imported query constants.
# Returns: Issues found in this module.
def validate_test_module(path: Path, repo_root: Path | None = None) -> list[TestQualityIssue]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        # Unreadable or unparseable test modules are already reported by ruff and
        # by pytest collection itself; this validator stays silent rather than duplicating them.
        return []

    source_lines = source.splitlines()
    issues: list[TestQualityIssue] = _query_round_trip_issues(tree, path, repo_root or ROOT_DIR)
    helpers = _module_functions(tree)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        # A fixture may legitimately be named test_settings. pytest decides what
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

        states_expectation, weak_call = _verification_strength(node, helpers)

        # A test whose whole verification is "the collaborator was called" passes
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

        # A deliberate "this must not raise" smoke test is legitimate, but it has
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


# Inspect every test module in the repository.
# repo_root: Repository root containing the tests/ tree.
# Returns: All issues found, ordered by path.
def collect_test_quality_issues(repo_root: Path) -> list[TestQualityIssue]:
    tests_dir = repo_root / TESTS_DIRNAME
    if not tests_dir.is_dir():
        return []

    issues: list[TestQualityIssue] = []
    for path in sorted(tests_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        issues.extend(validate_test_module(path, repo_root))
    return issues


# Convert one issue into a JSON-serializable remediation payload.
# repo_root: Repository root used for relative-path rendering.
def _issue_to_payload(issue: TestQualityIssue, repo_root: Path) -> dict[str, object]:
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category=issue.category,
        file=str(issue.path.relative_to(repo_root)),
        line=issue.line,
        message=issue.message,
        playbook=get_test_quality_rule_playbook(issue.rule_id),
    )


# Run test-quality validation and return a process exit code.
# Returns: Zero when every test can fail for a reason, non-zero otherwise.
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


# Script entrypoint for the test-quality validator.
if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
