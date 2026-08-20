# FILE: tests/application/test_validate_runtime_ownership.py
# SUMMARY: Unit tests for the runtime ownership validator.

from pathlib import Path
from typing import Any
import sys

import pytest

from scripts.validate_runtime_ownership import (
    RuntimeOwnershipIssue,
    collect_runtime_ownership_issues,
    get_runtime_ownership_rule_playbook,
    main,
)


def _write_fixture(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestValidateRuntimeOwnership:
    @pytest.mark.unit
    def test_get_runtime_ownership_rule_playbook_returns_failure_guidance(self) -> None:
        playbook: dict[str, Any] | None = get_runtime_ownership_rule_playbook(
            "runtime_ownership.env_access_restricted"
        )

        assert playbook is not None
        assert "project/core/config.py" in playbook["read_first"]
        assert playbook["smallest_command_to_rerun"] == (
            "uv run python scripts/validate_runtime_ownership.py"
        )

    @pytest.mark.unit
    def test_validator_rejects_env_access_outside_allowlist(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "application" / "bad_module.py",
            "\n".join(
                [
                    "import os",
                    "",
                    "def build() -> str:",
                    "    return os.getenv('APP_DEBUG', 'false')",
                ]
            ),
        )

        issues = collect_runtime_ownership_issues(tmp_path)

        assert any(issue.rule_id == "runtime_ownership.env_access_restricted" for issue in issues)

    @pytest.mark.unit
    def test_validator_allows_env_access_in_allowlist_path(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "core" / "config_runtime.py",
            "\n".join(
                [
                    "import os",
                    "",
                    "def build() -> str:",
                    "    return os.getenv('APP_DEBUG', 'false')",
                ]
            ),
        )

        issues = collect_runtime_ownership_issues(tmp_path)

        assert issues == []

    # **LOGIC_STEP**: These four spellings all read the process environment. The validator used to
    # match the literal `os.getenv` / `os.environ` text, so every from-import form disabled the
    # rule for the whole file — and `from os import getenv` is ordinary style, not an evasion.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("import_line", "access"),
        [
            ("import os", "os.getenv('APP_DEBUG', 'false')"),
            ("from os import getenv", "getenv('APP_DEBUG', 'false')"),
            ("from os import getenv as read_env", "read_env('APP_DEBUG', 'false')"),
            ("from os import environ as env_map", "env_map.get('APP_DEBUG', 'false')"),
        ],
    )
    def test_validator_rejects_env_access_whatever_the_import_spelling(
        self,
        tmp_path: Path,
        import_line: str,
        access: str,
    ) -> None:
        _write_fixture(
            tmp_path / "project" / "application" / "bad_module.py",
            "\n".join([import_line, "", "def build() -> str:", f"    return {access}"]),
        )

        issues = collect_runtime_ownership_issues(tmp_path)

        assert [issue.rule_id for issue in issues] == ["runtime_ownership.env_access_restricted"]

    @pytest.mark.unit
    def test_validator_rejects_aliased_shared_resource_construction(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "application" / "bad_pool.py",
            "\n".join(
                [
                    "from psycopg_pool import AsyncConnectionPool as Pool",
                    "",
                    "def build() -> object:",
                    "    return Pool('postgresql://localhost/db')",
                ]
            ),
        )

        issues = collect_runtime_ownership_issues(tmp_path)

        assert [issue.rule_id for issue in issues] == [
            "runtime_ownership.shared_resource_creation_restricted"
        ]

    @pytest.mark.unit
    def test_validator_does_not_confuse_a_local_name_with_the_import(self, tmp_path: Path) -> None:
        # **LOGIC_STEP**: A parameter named `environ` is not os.environ. Resolution goes through
        # the import map, so an unimported name resolves to nothing and the rule stays quiet.
        _write_fixture(
            tmp_path / "project" / "application" / "innocent.py",
            "\n".join(
                [
                    "def build(environ: dict[str, str]) -> str:",
                    "    return environ.get('APP_DEBUG', 'false')",
                ]
            ),
        )

        assert collect_runtime_ownership_issues(tmp_path) == []

    @pytest.mark.unit
    def test_validator_rejects_app_state_services_write_outside_composition_root(
        self,
        tmp_path: Path,
    ) -> None:
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "api" / "bad.py",
            "\n".join(
                [
                    "def bind(app) -> None:",
                    "    app.state.services = {}",
                ]
            ),
        )

        issues = collect_runtime_ownership_issues(tmp_path)

        assert any(
            issue.rule_id == "runtime_ownership.app_state_services_write_restricted"
            for issue in issues
        )

    @pytest.mark.unit
    def test_validator_rejects_shared_resource_creation_outside_allowed_paths(
        self,
        tmp_path: Path,
    ) -> None:
        _write_fixture(
            tmp_path / "project" / "application" / "bad_module.py",
            "\n".join(
                [
                    "from psycopg_pool import AsyncConnectionPool",
                    "",
                    "def build() -> object:",
                    "    return AsyncConnectionPool('postgresql://example')",
                ]
            ),
        )

        issues = collect_runtime_ownership_issues(tmp_path)

        assert any(
            issue.rule_id == "runtime_ownership.shared_resource_creation_restricted"
            for issue in issues
        )

    @pytest.mark.unit
    def test_validator_allows_shared_resource_creation_in_composition_root(
        self,
        tmp_path: Path,
    ) -> None:
        _write_fixture(
            tmp_path / "project" / "core" / "composition_root.py",
            "\n".join(
                [
                    "from psycopg_pool import AsyncConnectionPool",
                    "",
                    "def build() -> object:",
                    "    return AsyncConnectionPool('postgresql://example')",
                ]
            ),
        )

        issues = collect_runtime_ownership_issues(tmp_path)

        assert issues == []

    @pytest.mark.unit
    def test_validator_rejects_shared_engine_creation_outside_composition_root(
        self,
        tmp_path: Path,
    ) -> None:
        # create_async_engine owns a connection pool — must originate from composition_root.
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "agents" / "leak.py",
            "\n".join(
                [
                    "from sqlalchemy.ext.asyncio import create_async_engine",
                    "",
                    "def build() -> object:",
                    "    return create_async_engine('postgresql+asyncpg://h/db')",
                ]
            ),
        )

        issues = collect_runtime_ownership_issues(tmp_path)

        assert any(
            issue.rule_id == "runtime_ownership.shared_resource_creation_restricted"
            for issue in issues
        ), (
            f"Expected shared-resource flag for create_async_engine, got: {[i.message for i in issues]}"
        )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("import_line", "construction"),
        [
            ("import httpx", "httpx.Client()"),
            ("from httpx import Client", "Client()"),
        ],
    )
    def test_validator_rejects_synchronous_http_client_outside_composition_root(
        self,
        tmp_path: Path,
        import_line: str,
        construction: str,
    ) -> None:
        # **LOGIC_STEP**: A synchronous httpx.Client in an async path opens a fresh TCP+TLS
        # connection per call and blocks the event loop, and ruff's ASYNC210 does not see it —
        # that rule only knows module-level httpx.get / requests.get. This validator is the only
        # gate that can catch it, in either import spelling.
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "agents" / "sync_client.py",
            "\n".join(
                [
                    import_line,
                    "",
                    "async def fetch() -> object:",
                    f"    return {construction}.get('https://example.com')",
                ]
            ),
        )

        issues = collect_runtime_ownership_issues(tmp_path)

        assert any(
            issue.rule_id == "runtime_ownership.shared_resource_creation_restricted"
            for issue in issues
        ), f"Expected shared-resource flag for {construction}, got: {[i.message for i in issues]}"

    @pytest.mark.unit
    def test_validator_emits_syntax_error_issue_on_broken_file(
        self,
        tmp_path: Path,
    ) -> None:
        # **LOGIC_STEP**: Regression guard for raw-traceback regression in collect_runtime_ownership_issues.
        _write_fixture(
            tmp_path / "project" / "application" / "broken.py",
            "def foo(:\n    pass\n",
        )

        issues = collect_runtime_ownership_issues(tmp_path)

        syntax_issues = [
            issue for issue in issues if issue.rule_id == "runtime_ownership.syntax_error"
        ]
        assert len(syntax_issues) == 1
        assert "SyntaxError while parsing" in syntax_issues[0].message
        assert get_runtime_ownership_rule_playbook("runtime_ownership.syntax_error") is not None

    @pytest.mark.unit
    def test_validator_allows_shared_engine_creation_in_composition_root(
        self,
        tmp_path: Path,
    ) -> None:
        _write_fixture(
            tmp_path / "project" / "core" / "composition_root.py",
            "\n".join(
                [
                    "from sqlalchemy.ext.asyncio import create_async_engine",
                    "",
                    "def build() -> object:",
                    "    return create_async_engine('postgresql+asyncpg://h/db')",
                ]
            ),
        )

        issues = collect_runtime_ownership_issues(tmp_path)

        assert issues == []

    @pytest.mark.unit
    def test_main_json_output_includes_structured_issue_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        issue_path = Path.cwd() / "project" / "application" / "bad_module.py"
        monkeypatch.setattr(
            "scripts.validate_runtime_ownership.collect_runtime_ownership_issues",
            lambda _repo_root: [
                RuntimeOwnershipIssue(
                    path=issue_path,
                    line=5,
                    rule_id="runtime_ownership.env_access_restricted",
                    message="Direct os.getenv access is restricted to configuration allowlist paths.",
                )
            ],
        )
        monkeypatch.setattr(sys, "argv", ["scripts/validate_runtime_ownership.py", "--json"])

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 1
        assert '"rule_id": "runtime_ownership.env_access_restricted"' in captured.out
        assert '"suggested_fix"' in captured.out
