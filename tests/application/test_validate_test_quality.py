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


# CLASS: tests.application.test_validate_test_quality.TestValidatorSurface
# SUMMARY: Verify the repository is clean and the rule playbooks are complete.
# CLASS: tests.application.test_validate_test_quality.TestASpanTestSaysWhatTheSpanReported
# SUMMARY: Verify the rule fires on a test that finds a span and never reads its outcome, and
# stays quiet on one that pins it — including through a helper that extracts `output` itself.
# NOTE: Measured on 2026-09-03: mutating a repository span to report `row_written = True`
# unconditionally left every gate green. The tests proved the span happened; none read what it
# said, and a trace that can lie is worse than no trace because it is believed.
class TestASpanTestSaysWhatTheSpanReported:
    # FUNCTION: test_a_test_that_only_finds_the_span_is_reported
    # SUMMARY: Verify a lookup with no assertion on output fails the rule.
    @pytest.mark.unit
    def test_a_test_that_only_finds_the_span_is_reported(self, tmp_path: Path) -> None:
        module = tmp_path / "test_span_unpinned.py"
        module.write_text(
            "def _finish_event(captured, name):\n"
            "    return [e for e in captured if e['event_id'] == 'span.finish'][0]\n"
            "\n"
            "def test_the_span_happens(log_capture) -> None:\n"
            "    finish = _finish_event(log_capture, 'db.thing.add')\n"
            "    assert finish['name'] == 'db.thing.add'\n",
            encoding="utf-8",
        )

        issues = validate_test_module(module)

        assert [issue.rule_id for issue in issues] == ["test.span_output_pinned"]

    # FUNCTION: test_a_test_that_states_the_output_is_left_alone
    # SUMMARY: Verify asserting the span's output satisfies the rule.
    @pytest.mark.unit
    def test_a_test_that_states_the_output_is_left_alone(self, tmp_path: Path) -> None:
        module = tmp_path / "test_span_pinned.py"
        module.write_text(
            "def _finish_event(captured, name):\n"
            "    return [e for e in captured if e['event_id'] == 'span.finish'][0]\n"
            "\n"
            "def test_the_span_reports_the_write(log_capture) -> None:\n"
            "    finish = _finish_event(log_capture, 'db.thing.add')\n"
            "    assert finish['data']['output'] == {'rows_written': 1}\n",
            encoding="utf-8",
        )

        assert validate_test_module(module) == []

    # FUNCTION: test_a_helper_that_extracts_the_output_counts_as_pinning_it
    # SUMMARY: Verify a locator that narrows to `output` itself does not make its callers fail.
    # **LOGIC_STEP**: Without this the rule fires on tests that pin the value perfectly well —
    # they just never spell "output", because their helper already did. A rule that fires on
    # correct code teaches people to reach for the opt-out marker, which is worse than no rule.
    @pytest.mark.unit
    def test_a_helper_that_extracts_the_output_counts_as_pinning_it(self, tmp_path: Path) -> None:
        module = tmp_path / "test_span_via_helper.py"
        module.write_text(
            "def _span_outputs(captured):\n"
            "    return [e['data']['output'] for e in captured "
            "if e['event_id'] == 'span.finish']\n"
            "\n"
            "def test_the_response_shape_is_reported(log_capture) -> None:\n"
            "    outputs = _span_outputs(log_capture)\n"
            "    assert outputs[-1]['response_type'] == 'streaming'\n",
            encoding="utf-8",
        )

        assert validate_test_module(module) == []

    # FUNCTION: test_building_an_event_is_not_looking_one_up
    # SUMMARY: Verify a fixture that constructs a span.finish line is not read as a span test.
    @pytest.mark.unit
    def test_building_an_event_is_not_looking_one_up(self, tmp_path: Path) -> None:
        module = tmp_path / "test_span_fixture.py"
        module.write_text(
            "def _log_line():\n"
            "    return {'event_id': 'span.finish', 'name': 'db.thing.add'}\n"
            "\n"
            "def test_the_formatter_renders_a_finish() -> None:\n"
            "    assert _log_line()['name'] == 'db.thing.add'\n",
            encoding="utf-8",
        )

        assert validate_test_module(module) == []

    # FUNCTION: test_a_test_that_only_proves_the_output_exists_is_reported
    # SUMMARY: Verify existence, truthiness and length checks do not count as pinning a value.
    # **LOGIC_STEP**: The first version of this rule asked only whether an assertion mentioned
    # `output`, so every shape below satisfied it while stating nothing about what the span
    # reported — the same mutation the rule was built to catch stayed green behind any of them.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "assertion",
        [
            "assert finish['data']['output'] is not None",
            "assert finish['data']['output']",
            "assert len(finish['data']['output']) > 0",
            "assert 'rows_written' in finish['data']['output']",
        ],
    )
    def test_a_test_that_only_proves_the_output_exists_is_reported(
        self, tmp_path: Path, assertion: str
    ) -> None:
        module = tmp_path / "test_span_existence.py"
        module.write_text(
            "def _finish_event(captured, name):\n"
            "    return [e for e in captured if e['event_id'] == 'span.finish'][0]\n"
            "\n"
            "def test_the_span_reports_something(log_capture) -> None:\n"
            "    finish = _finish_event(log_capture, 'db.thing.add')\n"
            f"    {assertion}\n",
            encoding="utf-8",
        )

        issues = validate_test_module(module)

        assert [issue.rule_id for issue in issues] == ["test.span_output_pinned"]

    # FUNCTION: test_a_helper_path_that_only_proves_existence_is_reported_too
    # SUMMARY: Verify the locator-helper exemption does not accept an existence check either.
    @pytest.mark.unit
    def test_a_helper_path_that_only_proves_existence_is_reported_too(self, tmp_path: Path) -> None:
        module = tmp_path / "test_span_helper_existence.py"
        module.write_text(
            "def _span_output(captured):\n"
            "    finish = [e for e in captured if e['event_id'] == 'span.finish'][0]\n"
            "    return finish['data']['output']\n"
            "\n"
            "def test_the_span_reports_something(log_capture) -> None:\n"
            "    assert _span_output(log_capture) is not None\n",
            encoding="utf-8",
        )

        issues = validate_test_module(module)

        assert [issue.rule_id for issue in issues] == ["test.span_output_pinned"]

    # FUNCTION: test_an_expectation_that_is_not_a_bare_literal_still_counts_as_pinning
    # SUMMARY: Verify approx, a dataclass, an f-string and a parametrised value are accepted.
    # **LOGIC_STEP**: Each of these states the expected output as plainly as a literal does, and
    # an earlier version of the rule refused all four. A rule that fires on correct work is not a
    # strict rule, it is a rule people learn to silence, so the shapes are listed here to keep
    # them accepted.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "assertion",
        [
            "assert finish['data']['output']['latency'] == pytest.approx(1.2345)",
            "assert finish['data']['output'] == SpanOutput(row_found=False)",
            "assert finish['data']['output'] == f'row:{row_id}'",
            "assert finish['data']['output'] == expected",
        ],
    )
    def test_an_expectation_that_is_not_a_bare_literal_still_counts_as_pinning(
        self, tmp_path: Path, assertion: str
    ) -> None:
        module = tmp_path / "test_span_expectation.py"
        module.write_text(
            "def _finish_event(captured, name):\n"
            "    return [e for e in captured if e['event_id'] == 'span.finish'][0]\n"
            "\n"
            "def test_the_span_reports_the_write(log_capture, expected, row_id) -> None:\n"
            "    finish = _finish_event(log_capture, 'db.thing.add')\n"
            f"    {assertion}\n",
            encoding="utf-8",
        )

        assert validate_test_module(module) == []

    # FUNCTION: test_an_output_compared_against_itself_is_reported
    # SUMMARY: Verify the one equality that states nothing is still refused.
    @pytest.mark.unit
    def test_an_output_compared_against_itself_is_reported(self, tmp_path: Path) -> None:
        module = tmp_path / "test_span_tautology.py"
        module.write_text(
            "def _finish_event(captured, name):\n"
            "    return [e for e in captured if e['event_id'] == 'span.finish'][0]\n"
            "\n"
            "def test_the_span_reports_what_it_reports(log_capture) -> None:\n"
            "    finish = _finish_event(log_capture, 'db.thing.add')\n"
            "    assert finish['data']['output'] == finish['data']['output']\n",
            encoding="utf-8",
        )

        assert "test.span_output_pinned" in [
            issue.rule_id for issue in validate_test_module(module)
        ]

    # FUNCTION: test_an_equality_that_states_nothing_is_still_reported
    # SUMMARY: Verify a wildcard and a tautology routed through a variable are both refused.
    # **LOGIC_STEP**: Widening the rule to accept any stated expectation opened two ways to state
    # nothing: `== ANY` matches whatever the span reported, and `expected = finish[...]["output"]`
    # one line above the assert makes both sides the same object while looking like two. Both were
    # found by an audit of the widening itself.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "body",
        [
            "    assert finish['data']['output'] == ANY",
            "    assert finish['data']['output'] == mock.ANY",
            "    assert finish['data']['output'] == RENAMED",
            "    sentinel = ANY\n    assert finish['data']['output'] == sentinel",
            "    expected = finish['data']['output']\n"
            "    assert finish['data']['output'] == expected",
            "    assert (out := finish['data']['output']) == out",
            "    expected, _ = finish['data']['output'], None\n"
            "    assert finish['data']['output'] == expected",
            "    for expected in [finish['data']['output']]:\n"
            "        pass\n"
            "    assert finish['data']['output'] == expected",
        ],
    )
    def test_an_equality_that_states_nothing_is_still_reported(
        self, tmp_path: Path, body: str
    ) -> None:
        module = tmp_path / "test_span_wildcard.py"
        module.write_text(
            "from unittest import mock\n"
            "from unittest.mock import ANY\n"
            "from unittest.mock import ANY as RENAMED\n"
            "\n"
            "def _finish_event(captured, name):\n"
            "    return [e for e in captured if e['event_id'] == 'span.finish'][0]\n"
            "\n"
            "def test_the_span_reports_the_write(log_capture) -> None:\n"
            "    finish = _finish_event(log_capture, 'db.thing.add')\n"
            f"{body}\n",
            encoding="utf-8",
        )

        issues = [issue.rule_id for issue in validate_test_module(module)]

        assert "test.span_output_pinned" in issues

    # FUNCTION: test_a_fixture_that_finds_the_span_is_read_like_a_helper
    # SUMMARY: Verify moving the lookup into a fixture does not hide the test from the rule.
    # **LOGIC_STEP**: A fixture arrives by name in the signature and is never called, so reading
    # calls alone missed it: the whole rule was escapable by extracting the lookup into one. The
    # pinning fixture is here beside it, because a rule that catches the escape by refusing the
    # correct version too would be no better.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("assertion", "expected_issues"),
        [
            ("assert span_output is not None", ["test.span_output_pinned"]),
            ("assert span_output == {'rows_written': 1}", []),
        ],
    )
    def test_a_fixture_that_finds_the_span_is_read_like_a_helper(
        self, tmp_path: Path, assertion: str, expected_issues: list[str]
    ) -> None:
        module = tmp_path / "test_span_fixture_lookup.py"
        module.write_text(
            "import pytest\n"
            "\n"
            "@pytest.fixture\n"
            "def span_output(log_capture):\n"
            "    finish = [e for e in log_capture if e['event_id'] == 'span.finish'][0]\n"
            "    return finish['kwargs']['data']['output']\n"
            "\n"
            "def test_the_span_reports_the_write(span_output) -> None:\n"
            f"    {assertion}\n",
            encoding="utf-8",
        )

        assert [issue.rule_id for issue in validate_test_module(module)] == expected_issues

    # FUNCTION: test_membership_in_a_literal_set_still_counts_as_pinning
    # SUMMARY: Verify a value checked against a closed set of literals is accepted.
    @pytest.mark.unit
    def test_membership_in_a_literal_set_still_counts_as_pinning(self, tmp_path: Path) -> None:
        module = tmp_path / "test_span_membership.py"
        module.write_text(
            "def _finish_event(captured, name):\n"
            "    return [e for e in captured if e['event_id'] == 'span.finish'][0]\n"
            "\n"
            "def test_the_span_reports_a_known_status(log_capture) -> None:\n"
            "    finish = _finish_event(log_capture, 'db.thing.add')\n"
            "    assert finish['data']['output']['status'] in {'pending', 'done'}\n",
            encoding="utf-8",
        )

        assert validate_test_module(module) == []


# CLASS: tests.application.test_validate_test_quality.TestASpanNobodyLooksAtIsReported
# SUMMARY: Verify a span that records an outcome must be named by at least one test.
# NOTE: The sibling rule reads tests and asks whether the one that found a span said what it
# reported; it is blind by construction to a span no test mentions. That blindness shipped —
# `db.reference_task.update` recorded whether the row was still there, no test named it, and
# replacing that value with an unconditional True left every gate green on 2026-09-06.
class TestASpanNobodyLooksAtIsReported:
    # FUNCTION: _write_project
    # SUMMARY: Build a throwaway checkout with one span writing an output and one test module.
    @staticmethod
    def _write_project(root: Path, test_body: str) -> None:
        module = root / "project" / "infrastructure"
        module.mkdir(parents=True)
        (module / "store.py").write_text(
            "def save(logger, row):\n"
            "    with logger.span('db.thing.save') as span:\n"
            "        span.output['row_written'] = row is not None\n",
            encoding="utf-8",
        )
        tests = root / "tests" / "infrastructure"
        tests.mkdir(parents=True)
        (tests / "test_store.py").write_text(test_body, encoding="utf-8")

    # FUNCTION: test_a_span_no_test_names_is_reported
    # SUMMARY: Verify the rule fires when nothing under tests/ mentions the span.
    @pytest.mark.unit
    def test_a_span_no_test_names_is_reported(self, tmp_path: Path) -> None:
        self._write_project(
            tmp_path,
            "def test_the_row_is_saved() -> None:\n    assert True is True\n",
        )

        issues = collect_test_quality_issues(tmp_path)

        assert "test.span_output_unpinned" in [issue.rule_id for issue in issues]

    # FUNCTION: test_a_span_a_test_names_is_left_to_the_sibling_rule
    # SUMMARY: Verify naming the span satisfies this rule, whatever the test then asserts.
    @pytest.mark.unit
    def test_a_span_a_test_names_is_left_to_the_sibling_rule(self, tmp_path: Path) -> None:
        self._write_project(
            tmp_path,
            "def _finish(captured):\n"
            "    return [e for e in captured if e['event_id'] == 'span.finish'][0]\n"
            "\n"
            "def test_the_save_span_reports_the_write(log_capture) -> None:\n"
            "    finish = _finish(log_capture)\n"
            "    assert finish['name'] == 'db.thing.save'\n"
            "    assert finish['data']['output'] == {'row_written': True}\n",
        )

        issues = collect_test_quality_issues(tmp_path)

        assert [issue.rule_id for issue in issues if "span_output" in issue.rule_id] == []

    # FUNCTION: test_an_alias_bound_in_a_branch_is_still_followed
    # SUMMARY: Verify the alias chain is read to the end whatever depth its links sit at.
    # **LOGIC_STEP**: ast.walk is breadth-first, so a single pass read `b = a` before `a = span`
    # whenever the first link sat one level deeper, and the write through `b` disappeared. The
    # chain is followed to a fixpoint now.
    @pytest.mark.unit
    def test_an_alias_bound_in_a_branch_is_still_followed(self, tmp_path: Path) -> None:
        module = tmp_path / "project" / "infrastructure"
        module.mkdir(parents=True)
        (module / "store.py").write_text(
            "def save(logger, row, flag):\n"
            "    with logger.span('db.thing.save') as span:\n"
            "        if flag:\n"
            "            a = span\n"
            "        b = a\n"
            "        b.output['row_written'] = row is not None\n",
            encoding="utf-8",
        )
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_store.py").write_text(
            "def test_nothing() -> None:\n    assert 1 + 1 == 2\n", encoding="utf-8"
        )

        issues = collect_test_quality_issues(tmp_path)

        assert "test.span_output_unpinned" in [issue.rule_id for issue in issues]

    # FUNCTION: test_a_name_mentioned_outside_a_span_test_does_not_vouch_for_it
    # SUMMARY: Verify only a module that looks a span up can satisfy this rule.
    # **LOGIC_STEP**: Matching every string under tests/ meant a span name in a docstring, or in a
    # list kept for documentation, satisfied the rule while nothing exercised the span — the same
    # silence the rule was added to break.
    @pytest.mark.unit
    def test_a_name_mentioned_outside_a_span_test_does_not_vouch_for_it(
        self, tmp_path: Path
    ) -> None:
        self._write_project(
            tmp_path,
            "def test_something_else() -> None:\n"
            '    """This module mentions db.thing.save in prose only."""\n'
            "    assert 1 + 1 == 2\n",
        )

        issues = collect_test_quality_issues(tmp_path)

        assert "test.span_output_unpinned" in [issue.rule_id for issue in issues]

    # FUNCTION: test_an_output_written_through_an_alias_is_still_seen
    # SUMMARY: Verify renaming the span variable does not hide the write from this rule.
    @pytest.mark.unit
    def test_an_output_written_through_an_alias_is_still_seen(self, tmp_path: Path) -> None:
        module = tmp_path / "project" / "infrastructure"
        module.mkdir(parents=True)
        (module / "store.py").write_text(
            "def save(logger, row):\n"
            "    with logger.span('db.thing.save') as span:\n"
            "        handle = span\n"
            "        handle.output['row_written'] = row is not None\n",
            encoding="utf-8",
        )
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_store.py").write_text(
            "def test_nothing() -> None:\n    assert 1 + 1 == 2\n", encoding="utf-8"
        )

        issues = collect_test_quality_issues(tmp_path)

        assert "test.span_output_unpinned" in [issue.rule_id for issue in issues]

    # FUNCTION: test_a_span_that_records_nothing_is_not_demanded_of
    # SUMMARY: Verify a span with no output is outside this rule entirely.
    # **LOGIC_STEP**: Most spans carry only a name and a duration, and demanding a test for each
    # would fire on every correct module in the repository.
    @pytest.mark.unit
    def test_a_span_that_records_nothing_is_not_demanded_of(self, tmp_path: Path) -> None:
        module = tmp_path / "project" / "infrastructure"
        module.mkdir(parents=True)
        (module / "store.py").write_text(
            "def save(logger, row):\n    with logger.span('db.thing.save'):\n        pass\n",
            encoding="utf-8",
        )
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_store.py").write_text(
            "def test_nothing() -> None:\n    assert 1 + 1 == 2\n", encoding="utf-8"
        )

        assert collect_test_quality_issues(tmp_path) == []


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
            "test.span_output_pinned",
            "test.span_output_unpinned",
        ],
    )
    def test_every_rule_has_a_playbook(self, rule_id: str) -> None:
        playbook = get_test_quality_rule_playbook(rule_id)

        assert playbook is not None
        assert playbook["stop_widening_condition"]
