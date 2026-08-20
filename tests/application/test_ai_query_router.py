# FILE: tests/application/test_ai_query_router.py
# SUMMARY: Unit tests for the ai_query router dispatcher.

from argparse import Namespace
from typing import Any

import pytest

from ai_query.router import resolve_query


class TestAIQueryRouter:
    @pytest.mark.unit
    def test_resolve_query_dispatches_bootstrap(self) -> None:
        result = resolve_query(Namespace(command="bootstrap"))

        assert result.kind == "bootstrap"
        payload: dict[str, Any] = result.payload
        assert payload["read_first"][0] == "CLAUDE.md"

    @pytest.mark.unit
    def test_resolve_query_dispatches_failure_rule(self) -> None:
        result = resolve_query(
            Namespace(
                command="failure",
                subject_kind="rule",
                rule_id="runtime_ownership.env_access_restricted",
            )
        )

        assert result.kind == "failure"
        assert result.payload["smallest_command_to_rerun"] == (
            "uv run python scripts/validate_runtime_ownership.py"
        )
