# FILE: tests/application/test_validate_test_quality.py
# SUMMARY: Unit tests for the validator that rejects tests which cannot fail.

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_test_quality import (
    collect_test_quality_issues,
    get_test_quality_rule_playbook,
    validate_test_module,
)


# FUNCTION: _write_test_module
# SUMMARY: Write a test module fixture and return its path.
# INPUT: tmp_path (Path): Temporary directory.
# INPUT: body (str): Module source.
# OUTPUT: (Path): Path of the written module.
def _write_test_module(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "test_probe.py"
    path.write_text(body, encoding="utf-8")
    return path


# CLASS: tests.application.test_validate_test_quality.TestConstantAssertions
# SUMMARY: Verify assertions that can never fail are reported.
class TestConstantAssertions:
    # FUNCTION: test_assert_true_is_reported
    # SUMMARY: Verify the canonical always-green assertion is caught.
    @pytest.mark.unit
    def test_assert_true_is_reported(self, tmp_path: Path) -> None:
        path = _write_test_module(tmp_path, "def test_green() -> None:\n    assert True\n")

        issues = validate_test_module(path)

        assert [issue.rule_id for issue in issues] == ["test.constant_assertion"]

    # FUNCTION: test_assert_not_false_is_reported
    # SUMMARY: Verify the negated-constant spelling is caught too.
    @pytest.mark.unit
    def test_assert_not_false_is_reported(self, tmp_path: Path) -> None:
        path = _write_test_module(tmp_path, "def test_green() -> None:\n    assert not False\n")

        assert [issue.rule_id for issue in validate_test_module(path)] == [
            "test.constant_assertion"
        ]

    # FUNCTION: test_real_assertion_is_accepted
    # SUMMARY: Verify an assertion about a computed value is not reported.
    @pytest.mark.unit
    def test_real_assertion_is_accepted(self, tmp_path: Path) -> None:
        path = _write_test_module(
            tmp_path,
            "def test_sum() -> None:\n    assert 1 + 1 == 2\n",
        )

        assert validate_test_module(path) == []

    # FUNCTION: test_value_compared_with_itself_is_reported
    # SUMMARY: Verify the tautology a literal check cannot see is caught.
    @pytest.mark.unit
    @pytest.mark.parametrize("operator", ["==", "is", ">=", "<="])
    def test_value_compared_with_itself_is_reported(self, tmp_path: Path, operator: str) -> None:
        # **LOGIC_STEP**: The operands are names, so the node is an ast.Compare and the literal
        # check returns False — this is the spelling that used to sail through the gate.
        path = _write_test_module(
            tmp_path,
            f"def test_green() -> None:\n    value = object()\n    assert value {operator} value\n",
        )

        assert [issue.rule_id for issue in validate_test_module(path)] == [
            "test.constant_assertion"
        ]

    # FUNCTION: test_attribute_compared_with_itself_is_reported
    # SUMMARY: Verify the check follows attribute chains, not only bare names.
    @pytest.mark.unit
    def test_attribute_compared_with_itself_is_reported(self, tmp_path: Path) -> None:
        path = _write_test_module(
            tmp_path,
            "def test_green(result: object) -> None:\n    assert result.status == result.status\n",
        )

        assert [issue.rule_id for issue in validate_test_module(path)] == [
            "test.constant_assertion"
        ]

    # FUNCTION: test_call_compared_with_itself_is_accepted
    # SUMMARY: Verify two identical calls are not treated as a tautology.
    @pytest.mark.unit
    def test_call_compared_with_itself_is_accepted(self, tmp_path: Path) -> None:
        # **LOGIC_STEP**: `next(it) == next(it)` reads the same in the tree and differs at runtime.
        # Reporting it would make the gate wrong in the direction that costs trust.
        path = _write_test_module(
            tmp_path,
            "def test_stable(counter: object) -> None:\n    assert counter.read() == counter.read()\n",
        )

        assert validate_test_module(path) == []

    # FUNCTION: test_inequality_with_itself_is_not_reported
    # SUMMARY: Verify an always-failing comparison is left to the suite, not this gate.
    @pytest.mark.unit
    def test_inequality_with_itself_is_not_reported(self, tmp_path: Path) -> None:
        path = _write_test_module(
            tmp_path,
            "def test_red() -> None:\n    value = 1\n    assert value != value\n",
        )

        assert validate_test_module(path) == []


# CLASS: tests.application.test_validate_test_quality.TestMissingAssertions
# SUMMARY: Verify assertion-free test bodies are reported unless deliberately marked.
class TestMissingAssertions:
    # FUNCTION: test_body_without_verification_is_reported
    # SUMMARY: Verify a test that only calls code is caught.
    @pytest.mark.unit
    def test_body_without_verification_is_reported(self, tmp_path: Path) -> None:
        path = _write_test_module(tmp_path, "def test_nothing() -> None:\n    value = 1 + 1\n")

        assert [issue.rule_id for issue in validate_test_module(path)] == ["test.no_assertion"]

    # FUNCTION: test_pytest_raises_counts_as_verification
    # SUMMARY: Verify a raises-based test is accepted without a bare assert.
    @pytest.mark.unit
    def test_pytest_raises_counts_as_verification(self, tmp_path: Path) -> None:
        path = _write_test_module(
            tmp_path,
            "import pytest\n\n\ndef test_raises() -> None:\n"
            "    with pytest.raises(ValueError):\n        raise ValueError('boom')\n",
        )

        assert validate_test_module(path) == []

    # FUNCTION: test_helper_named_assert_counts_as_verification
    # SUMMARY: Verify delegation to a local checker is accepted.
    @pytest.mark.unit
    def test_helper_named_assert_counts_as_verification(self, tmp_path: Path) -> None:
        path = _write_test_module(
            tmp_path,
            "def _assert_contract(payload: dict) -> None:\n    assert payload\n\n\n"
            "def test_contract() -> None:\n    _assert_contract({'a': 1})\n",
        )

        assert validate_test_module(path) == []

    # FUNCTION: test_helper_named_assert_that_checks_nothing_is_reported
    # SUMMARY: Verify a checker with an empty body no longer buys the test a pass.
    @pytest.mark.unit
    def test_helper_named_assert_that_checks_nothing_is_reported(self, tmp_path: Path) -> None:
        # **LOGIC_STEP**: Matching on the substring alone made this pass: the name says assert,
        # the body does nothing, and the test cannot fail. The helper is followed into its body.
        path = _write_test_module(
            tmp_path,
            "def _assert_ok(payload: dict) -> None:\n    pass\n\n\n"
            "def test_contract() -> None:\n    _assert_ok({'a': 1})\n",
        )

        assert [issue.rule_id for issue in validate_test_module(path)] == ["test.no_assertion"]

    # FUNCTION: test_helper_chain_is_followed_to_the_real_assertion
    # SUMMARY: Verify delegation through two helpers still counts as verification.
    @pytest.mark.unit
    def test_helper_chain_is_followed_to_the_real_assertion(self, tmp_path: Path) -> None:
        path = _write_test_module(
            tmp_path,
            "def _assert_inner(payload: dict) -> None:\n    assert payload\n\n\n"
            "def _assert_outer(payload: dict) -> None:\n    _assert_inner(payload)\n\n\n"
            "def test_contract() -> None:\n    _assert_outer({'a': 1})\n",
        )

        assert validate_test_module(path) == []

    # FUNCTION: test_recursive_helper_does_not_hang
    # SUMMARY: Verify a checker that calls itself terminates and reports the missing assertion.
    @pytest.mark.unit
    def test_recursive_helper_does_not_hang(self, tmp_path: Path) -> None:
        path = _write_test_module(
            tmp_path,
            "def _assert_loop(payload: dict) -> None:\n    _assert_loop(payload)\n\n\n"
            "def test_contract() -> None:\n    _assert_loop({'a': 1})\n",
        )

        assert [issue.rule_id for issue in validate_test_module(path)] == ["test.no_assertion"]

    # FUNCTION: test_helper_from_another_module_stays_trusted
    # SUMMARY: Verify an imported checker this module cannot see is still accepted.
    @pytest.mark.unit
    def test_helper_from_another_module_stays_trusted(self, tmp_path: Path) -> None:
        # **LOGIC_STEP**: conftest helpers are unresolvable from a single module's tree. Guessing
        # they check nothing would fail honest tests, so an unknown name keeps the old benefit.
        path = _write_test_module(
            tmp_path,
            "from tests.helpers import assert_contract\n\n\n"
            "def test_contract() -> None:\n    assert_contract({'a': 1})\n",
        )

        assert validate_test_module(path) == []

    # FUNCTION: test_opt_out_marker_above_decorators_is_honoured
    # SUMMARY: Verify a deliberate smoke test annotated in the comment block is accepted.
    @pytest.mark.unit
    def test_opt_out_marker_above_decorators_is_honoured(self, tmp_path: Path) -> None:
        path = _write_test_module(
            tmp_path,
            "import pytest\n\n\n"
            "# no-assert-ok: proves the call does not raise; it returns None.\n"
            "@pytest.mark.unit\n"
            "def test_smoke() -> None:\n    int('1')\n",
        )

        assert validate_test_module(path) == []

    # FUNCTION: test_fixture_named_like_a_test_is_ignored
    # SUMMARY: Verify a fixture named test_settings is not treated as a test.
    @pytest.mark.unit
    def test_fixture_named_like_a_test_is_ignored(self, tmp_path: Path) -> None:
        path = _write_test_module(
            tmp_path,
            "import pytest\n\n\n@pytest.fixture\ndef test_settings() -> int:\n    return 1\n",
        )

        assert validate_test_module(path) == []


# CLASS: tests.application.test_validate_test_quality.TestCallAssertionsWithoutArguments
# SUMMARY: Verify a test that only proves a call happened is reported.
# NOTE: Measured. A service that ignored the caller's `limit` and sent its own value to the
# repository survived a full green suite at 83 % coverage, because the test asserted
# `repository.list.assert_awaited_once()` and stopped there. The call happened; the argument never
# arrived. Coverage says nothing about this — the line ran.
class TestCallAssertionsWithoutArguments:
    # FUNCTION: test_argumentless_call_assertion_alone_is_reported
    # SUMMARY: Verify a body whose only verification is "it was called" is flagged.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "assertion",
        ["assert_called()", "assert_called_once()", "assert_awaited()", "assert_awaited_once()"],
    )
    def test_argumentless_call_assertion_alone_is_reported(
        self, tmp_path: Path, assertion: str
    ) -> None:
        path = _write_test_module(
            tmp_path,
            f"def test_wiring(repository: object) -> None:\n    repository.list.{assertion}\n",
        )

        issues = validate_test_module(path)

        assert [issue.rule_id for issue in issues] == ["test.call_assertion_without_arguments"]

    # FUNCTION: test_the_with_variant_is_accepted
    # SUMMARY: Verify naming the expected arguments satisfies the rule.
    @pytest.mark.unit
    def test_the_with_variant_is_accepted(self, tmp_path: Path) -> None:
        path = _write_test_module(
            tmp_path,
            "def test_wiring(repository: object) -> None:\n"
            "    repository.list.assert_awaited_once_with(limit=7)\n",
        )

        assert validate_test_module(path) == []

    # FUNCTION: test_the_with_variant_carrying_no_arguments_is_accepted
    # SUMMARY: Verify "called once with nothing" is a statement, not an omission.
    @pytest.mark.unit
    def test_the_with_variant_carrying_no_arguments_is_accepted(self, tmp_path: Path) -> None:
        # **LOGIC_STEP**: pool.close() takes no arguments, so assert_awaited_once_with() is the
        # complete contract — and it is stronger than the bare form, which would stay green if
        # someone later started passing one.
        path = _write_test_module(
            tmp_path,
            "def test_shutdown(pool: object) -> None:\n    pool.close.assert_awaited_once_with()\n",
        )

        assert validate_test_module(path) == []

    # FUNCTION: test_a_real_assertion_alongside_it_is_still_reported
    # SUMMARY: Verify the weak assertion is called out even when the test checks something else.
    @pytest.mark.unit
    def test_a_real_assertion_alongside_it_is_still_reported(self, tmp_path: Path) -> None:
        # **LOGIC_STEP**: Asserting the returned value does not cover the arguments the
        # collaborator received — these are two different claims, and the second one is missing.
        path = _write_test_module(
            tmp_path,
            "def test_wiring(repository: object) -> None:\n"
            "    result = 1 + 1\n"
            "    assert result == 2\n"
            "    repository.list.assert_awaited_once()\n",
        )

        issues = validate_test_module(path)

        assert [issue.rule_id for issue in issues] == ["test.call_assertion_without_arguments"]

    # FUNCTION: test_the_opt_out_marker_still_works
    # SUMMARY: Verify a deliberate call-only check can be declared with a reason.
    @pytest.mark.unit
    def test_the_opt_out_marker_still_works(self, tmp_path: Path) -> None:
        path = _write_test_module(
            tmp_path,
            "# no-assert-ok: the contract is the call itself; it takes no arguments.\n"
            "def test_wiring(repository: object) -> None:\n"
            "    repository.close.assert_awaited_once()\n",
        )

        assert validate_test_module(path) == []


# FUNCTION: _write_repository_fixture
# SUMMARY: Build a miniature repository whose persistence module holds one f-string SQL constant.
# INPUT: tmp_path (Path): Temporary directory standing in for the repository root.
# OUTPUT: (Path): The repository root the validator should resolve imports against.
def _write_repository_fixture(tmp_path: Path) -> Path:
    module = tmp_path / "project" / "infrastructure" / "persistence" / "probe_repository.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    # **LOGIC_STEP**: An f-string on purpose. The shipped queries interpolate a column list, and a
    # Constant-only scan found no SQL in them at all — the rule applied to nothing while looking
    # like it worked.
    module.write_text(
        '_COLUMNS = "id, title"\n_SELECT_BY_ID = f"SELECT {_COLUMNS} FROM probes WHERE id = %s"\n',
        encoding="utf-8",
    )
    return tmp_path


# CLASS: tests.application.test_validate_test_quality.TestQueryConstantRoundTrip
# SUMMARY: Verify a test that checks the query against the query constant is reported unless one clause is pinned as text.
class TestQueryConstantRoundTrip:
    # FUNCTION: test_round_trip_without_a_pinned_clause_is_reported
    # SUMMARY: Regression guard: measured on the seventh run, three of four injected SQL defects passed every gate once the pinning assertions were removed.
    @pytest.mark.unit
    def test_round_trip_without_a_pinned_clause_is_reported(self, tmp_path: Path) -> None:
        repo_root = _write_repository_fixture(tmp_path)
        path = _write_test_module(
            tmp_path,
            "from project.infrastructure.persistence.probe_repository import _SELECT_BY_ID\n"
            "\n"
            "def test_get_runs_the_query() -> None:\n"
            "    cursor.execute.assert_awaited_once_with(_SELECT_BY_ID, ('id',))\n"
            "    assert 'id' not in _SELECT_BY_ID\n",
        )

        issues = validate_test_module(path, repo_root)

        assert [issue.rule_id for issue in issues] == ["test.sql_constant_round_trip"]
        assert "_SELECT_BY_ID" in issues[0].message

    # FUNCTION: test_a_pinned_clause_clears_the_module
    # SUMMARY: Verify the prescribed assertion — one clause stated as literal text — satisfies the rule.
    @pytest.mark.unit
    def test_a_pinned_clause_clears_the_module(self, tmp_path: Path) -> None:
        repo_root = _write_repository_fixture(tmp_path)
        path = _write_test_module(
            tmp_path,
            "from project.infrastructure.persistence.probe_repository import _SELECT_BY_ID\n"
            "\n"
            "def test_get_runs_the_query() -> None:\n"
            "    cursor.execute.assert_awaited_once_with(_SELECT_BY_ID, ('id',))\n"
            "    assert _SELECT_BY_ID.split(' WHERE ', 1)[1] == 'id = %s'\n",
        )

        assert validate_test_module(path, repo_root) == []

    # FUNCTION: test_a_constant_that_is_not_a_query_is_left_alone
    # SUMMARY: Verify the rule reads the constant's own value and ignores every non-SQL import.
    @pytest.mark.unit
    def test_a_constant_that_is_not_a_query_is_left_alone(self, tmp_path: Path) -> None:
        module = tmp_path / "project" / "domain" / "probe.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text('MAX_TITLE_LENGTH = 200\nSTATUS_OPEN = "open"\n', encoding="utf-8")
        path = _write_test_module(
            tmp_path,
            "from project.domain.probe import STATUS_OPEN\n"
            "\n"
            "def test_status_travels() -> None:\n"
            "    repository.save.assert_called_once_with(STATUS_OPEN)\n",
        )

        assert validate_test_module(path, tmp_path) == []

    # FUNCTION: test_an_identity_round_trip_without_a_pinned_clause_is_reported
    # SUMMARY: Verify `sql is _CONSTANT` is caught as the same tautology `==` is — `is` states
    # object identity, which the constant always has with itself, whatever its text says.
    @pytest.mark.unit
    def test_an_identity_round_trip_without_a_pinned_clause_is_reported(
        self, tmp_path: Path
    ) -> None:
        repo_root = _write_repository_fixture(tmp_path)
        path = _write_test_module(
            tmp_path,
            "from project.infrastructure.persistence.probe_repository import _SELECT_BY_ID\n"
            "\n"
            "def test_get_runs_the_query() -> None:\n"
            "    sql = _SELECT_BY_ID\n"
            "    assert sql is _SELECT_BY_ID\n",
        )

        issues = validate_test_module(path, repo_root)

        assert [issue.rule_id for issue in issues] == ["test.sql_constant_round_trip"]
        assert "_SELECT_BY_ID" in issues[0].message

    # FUNCTION: test_a_pinned_clause_via_membership_clears_the_module
    # SUMMARY: Verify `"clause text" in _CONSTANT` pins the constant the same way a sliced `==`
    # does — the ordinary way to spell "this substring is really in there" without slicing first.
    @pytest.mark.unit
    def test_a_pinned_clause_via_membership_clears_the_module(self, tmp_path: Path) -> None:
        repo_root = _write_repository_fixture(tmp_path)
        path = _write_test_module(
            tmp_path,
            "from project.infrastructure.persistence.probe_repository import _SELECT_BY_ID\n"
            "\n"
            "def test_get_runs_the_query() -> None:\n"
            "    cursor.execute.assert_awaited_once_with(_SELECT_BY_ID, ('id',))\n"
            "    assert 'WHERE id = %s' in _SELECT_BY_ID\n",
        )

        assert validate_test_module(path, repo_root) == []

    # FUNCTION: test_the_constant_on_the_left_of_in_does_not_count_as_pinned
    # SUMMARY: Verify `_CONSTANT in something` — the constant as the haystack's member, not the
    # text pin — is left exactly where it was: a round trip, not a pin. `in` only pins one way
    # round, and this proves the branch does not fire on the direction that states nothing.
    @pytest.mark.unit
    def test_the_constant_on_the_left_of_in_does_not_count_as_pinned(self, tmp_path: Path) -> None:
        repo_root = _write_repository_fixture(tmp_path)
        path = _write_test_module(
            tmp_path,
            "from project.infrastructure.persistence.probe_repository import _SELECT_BY_ID\n"
            "\n"
            "def test_get_runs_the_query() -> None:\n"
            "    cursor.execute.assert_awaited_once_with(_SELECT_BY_ID, ('id',))\n"
            "    queries = [_SELECT_BY_ID]\n"
            "    assert _SELECT_BY_ID in queries\n",
        )

        issues = validate_test_module(path, repo_root)

        assert [issue.rule_id for issue in issues] == ["test.sql_constant_round_trip"]

    # FUNCTION: test_a_one_word_needle_does_not_count_as_pinned
    # SUMMARY: Verify `assert "SELECT" in _CONSTANT` — true of every query — pins nothing.
    # NOTE: Measured on 2026-09-08 by a second independent review: with a one-word needle accepted,
    # reversing `WHERE id = %s` to `WHERE status = %s` left the module reported clean, where the
    # version before the `in` branch existed had reported it. A widening that makes a rule easier
    # to silence than it was is a regression even when the widening itself was right.
    @pytest.mark.unit
    def test_a_one_word_needle_does_not_count_as_pinned(self, tmp_path: Path) -> None:
        repo_root = _write_repository_fixture(tmp_path)
        path = _write_test_module(
            tmp_path,
            "from project.infrastructure.persistence.probe_repository import _SELECT_BY_ID\n"
            "\n"
            "def test_get_runs_the_query() -> None:\n"
            "    cursor.execute.assert_awaited_once_with(_SELECT_BY_ID, ('id',))\n"
            "    assert 'SELECT' in _SELECT_BY_ID\n",
        )

        issues = validate_test_module(path, repo_root)

        assert [issue.rule_id for issue in issues] == ["test.sql_constant_round_trip"]

    # FUNCTION: test_an_empty_needle_does_not_count_as_pinned
    # SUMMARY: Verify `assert "" in _CONSTANT` — true of every string — pins nothing.
    # NOTE: Found by an independent review of this branch on 2026-09-08, right after the `in`
    # branch was added: the shortest possible way to silence the rule for a whole module was one
    # assertion that cannot fail.
    @pytest.mark.unit
    def test_an_empty_needle_does_not_count_as_pinned(self, tmp_path: Path) -> None:
        repo_root = _write_repository_fixture(tmp_path)
        path = _write_test_module(
            tmp_path,
            "from project.infrastructure.persistence.probe_repository import _SELECT_BY_ID\n"
            "\n"
            "def test_get_runs_the_query() -> None:\n"
            "    cursor.execute.assert_awaited_once_with(_SELECT_BY_ID, ('id',))\n"
            "    assert '' in _SELECT_BY_ID\n"
            "    assert '   ' in _SELECT_BY_ID\n",
        )

        issues = validate_test_module(path, repo_root)

        assert [issue.rule_id for issue in issues] == ["test.sql_constant_round_trip"]

    # FUNCTION: test_the_constant_used_as_a_lookup_key_does_not_count_as_pinned
    # SUMMARY: Verify a constant that appears only as a dictionary key states nothing about the
    # SQL and does not silence the rule for the module.
    # NOTE: Both branches used to ask `_referenced_names` whether the expression mentions the
    # constant at all, which `results[_SELECT_BY_ID]` does — as the key doing the looking-up, with
    # no claim about the query text anywhere. One such assertion marked the constant pinned and
    # the rule went quiet for the whole module, which is the silence it exists to break. Found by
    # an independent review of this branch on 2026-09-08.
    @pytest.mark.unit
    def test_the_constant_used_as_a_lookup_key_does_not_count_as_pinned(
        self, tmp_path: Path
    ) -> None:
        repo_root = _write_repository_fixture(tmp_path)
        path = _write_test_module(
            tmp_path,
            "from project.infrastructure.persistence.probe_repository import _SELECT_BY_ID\n"
            "\n"
            "def test_get_runs_the_query() -> None:\n"
            "    cursor.execute.assert_awaited_once_with(_SELECT_BY_ID, ('id',))\n"
            "    results = {_SELECT_BY_ID: 'ok'}\n"
            "    assert results[_SELECT_BY_ID] == 'ok'\n"
            "    assert 'ok' in results[_SELECT_BY_ID]\n",
        )

        issues = validate_test_module(path, repo_root)

        assert [issue.rule_id for issue in issues] == ["test.sql_constant_round_trip"]


# CLASS: tests.application.test_validate_test_quality.TestValidatorSurface
# SUMMARY: Verify the repository is clean and the rule playbooks are complete.
class TestValidatorSurface:
    # FUNCTION: test_repository_has_no_unfailable_tests
    # SUMMARY: Verify the shipped suites satisfy the rule this validator enforces.
    @pytest.mark.unit
    def test_repository_has_no_unfailable_tests(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]

        assert collect_test_quality_issues(repo_root) == []

    # FUNCTION: test_every_rule_has_a_playbook
    # SUMMARY: Verify every rule identifier carries remediation guidance.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "rule_id",
        [
            "test.constant_assertion",
            "test.no_assertion",
            "test.call_assertion_without_arguments",
            "test.sql_constant_round_trip",
        ],
    )
    def test_every_rule_has_a_playbook(self, rule_id: str) -> None:
        playbook = get_test_quality_rule_playbook(rule_id)

        assert playbook is not None
        assert playbook["stop_widening_condition"]
