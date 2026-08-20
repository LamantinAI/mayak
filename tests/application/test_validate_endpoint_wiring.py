# FILE: tests/application/test_validate_endpoint_wiring.py
# SUMMARY: Unit tests for the endpoint-facing wiring contract validator.

from __future__ import annotations

from pathlib import Path
from typing import Any
import sys

import pytest

from scripts.validate_endpoint_wiring import (
    EndpointWiringIssue,
    collect_endpoint_wiring_issues,
    get_endpoint_rule_playbook,
    main,
)


# FUNCTION: _write_fixture
# SUMMARY: Write a Python fixture into a temporary repository layout.
# INPUT: path (Path): Target file path.
# INPUT: content (str): Fixture source content.
# OUTPUT: (None): None.
def _write_fixture(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# FUNCTION: _write_registration
# SUMMARY: Write a router_registration fixture that includes the named router on the app.
# INPUT: repo_root (Path): Temporary repository root.
# INPUT: module (str): Dotted endpoint module exporting the router.
# INPUT: router (str): Router variable name to include.
# OUTPUT: (None): None.
def _write_registration(repo_root: Path, module: str, router: str) -> None:
    _write_fixture(
        repo_root / "project" / "infrastructure" / "api" / "router_registration.py",
        "\n".join(
            [
                "from fastapi import FastAPI",
                f"from {module} import {router}",
                "",
                "",
                "def include_application_routers(app: FastAPI) -> None:",
                f"    app.include_router({router})",
            ]
        ),
    )


# FUNCTION: _context_map
# SUMMARY: Build a minimal context map stub for endpoint-wiring tests.
# OUTPUT: (dict[str, object]): Minimal service and alias registry payload.
def _context_map() -> dict[str, object]:
    return {
        "service_registry": {
            "business_service": {
                "class": "BusinessService",
                "module": "project.application.business_service.BusinessService",
            }
        },
        "dependency_registry": {
            "aliases": {
                "BusinessServiceDep": {
                    "getter": "get_business_service",
                    "service_key": "business_service",
                    "service_module": "project.application.business_service.BusinessService",
                    "service_type": "BusinessService",
                }
            }
        },
    }


# CLASS: tests.application.test_validate_endpoint_wiring.TestValidateEndpointWiring
# SUMMARY: Verify endpoint modules use typed dependency aliases instead of direct service access.
class TestValidateEndpointWiring:
    # FUNCTION: test_get_endpoint_rule_playbook_returns_shared_failure_guidance
    # SUMMARY: Verify endpoint rule playbooks are reusable by the query layer.
    @pytest.mark.unit
    def test_get_endpoint_rule_playbook_returns_shared_failure_guidance(self) -> None:
        playbook: dict[str, Any] | None = get_endpoint_rule_playbook(
            "endpoint.no_depends_without_alias"
        )

        assert playbook is not None
        assert "typed alias" in playbook["likely_fix_shape"]
        assert playbook["smallest_command_to_rerun"] == (
            "uv run python scripts/validate_endpoint_wiring.py"
        )
        assert "project/infrastructure/api/dependencies.py" in playbook["read_first"]

    # FUNCTION: test_validator_rejects_direct_service_import
    # SUMMARY: Verify endpoint modules cannot import application services directly.
    @pytest.mark.unit
    def test_validator_rejects_direct_service_import(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "api" / "endpoints" / "bad.py",
            "\n".join(
                [
                    "from fastapi import APIRouter",
                    "from project.application.business_service import BusinessService",
                    "",
                    "router = APIRouter()",
                    "",
                    "@router.get('/')",
                    "async def read_items(service: BusinessService) -> dict[str, str]:",
                    "    return {'status': 'ok'}",
                ]
            ),
        )

        issues = collect_endpoint_wiring_issues(tmp_path, context_map=_context_map())

        assert any(issue.rule_id == "endpoint.no_direct_service_import" for issue in issues)
        assert any(issue.rule_id == "endpoint.no_direct_service_annotation" for issue in issues)

    # FUNCTION: test_validator_emits_syntax_error_issue_on_broken_endpoint
    # SUMMARY: Regression guard: a syntactically broken endpoint module surfaces as a structured EndpointWiringIssue with rule_id 'endpoint.syntax_error', not a raw traceback.
    @pytest.mark.unit
    def test_validator_emits_syntax_error_issue_on_broken_endpoint(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "api" / "endpoints" / "broken.py",
            "from fastapi import APIRouter\n\ndef bad(:\n    pass\n",
        )

        issues = collect_endpoint_wiring_issues(tmp_path, context_map=_context_map())

        syntax_issues = [issue for issue in issues if issue.rule_id == "endpoint.syntax_error"]
        assert len(syntax_issues) == 1
        assert "SyntaxError while parsing" in syntax_issues[0].message
        assert get_endpoint_rule_playbook("endpoint.syntax_error") is not None

    # FUNCTION: test_validator_rejects_depends_without_alias
    # SUMMARY: Verify endpoint parameters that use Depends(...) directly are rejected.
    @pytest.mark.unit
    def test_validator_rejects_depends_without_alias(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "api" / "endpoints" / "bad.py",
            "\n".join(
                [
                    "from fastapi import APIRouter, Depends",
                    "from project.application.business_service import BusinessService",
                    "from project.infrastructure.api.dependencies import get_business_service",
                    "",
                    "router = APIRouter()",
                    "",
                    "@router.get('/')",
                    "async def read_items(service: BusinessService = Depends(get_business_service)) -> dict[str, str]:",
                    "    return {'status': 'ok'}",
                ]
            ),
        )

        issues = collect_endpoint_wiring_issues(tmp_path, context_map=_context_map())

        assert any(issue.rule_id == "endpoint.no_depends_without_alias" for issue in issues)

    # FUNCTION: test_validator_rejects_alias_chain_with_unknown_service_key
    # SUMMARY: Verify typed aliases still fail when they do not resolve to a known service key.
    @pytest.mark.unit
    def test_validator_rejects_alias_chain_with_unknown_service_key(
        self,
        tmp_path: Path,
    ) -> None:
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "api" / "endpoints" / "bad.py",
            "\n".join(
                [
                    "from fastapi import APIRouter",
                    "from project.infrastructure.api.dependencies import BusinessServiceDep",
                    "",
                    "router = APIRouter()",
                    "",
                    "@router.get('/')",
                    "async def read_items(service: BusinessServiceDep) -> dict[str, str]:",
                    "    return {'status': 'ok'}",
                ]
            ),
        )
        broken_context: dict[str, Any] = _context_map()
        broken_context["dependency_registry"]["aliases"]["BusinessServiceDep"]["service_key"] = (
            "missing_service"
        )

        issues = collect_endpoint_wiring_issues(tmp_path, context_map=broken_context)

        assert any(issue.rule_id == "endpoint.alias_chain_invalid" for issue in issues)

    # FUNCTION: test_validator_rejects_direct_service_construction
    # SUMMARY: Verify endpoint modules cannot instantiate services directly.
    @pytest.mark.unit
    def test_validator_rejects_direct_service_construction(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "api" / "endpoints" / "bad.py",
            "\n".join(
                [
                    "from fastapi import APIRouter",
                    "from project.application.business_service import BusinessService",
                    "",
                    "router = APIRouter()",
                    "",
                    "@router.get('/')",
                    "async def read_items() -> dict[str, str]:",
                    "    service = BusinessService()",
                    "    return {'status': str(service)}",
                ]
            ),
        )

        issues = collect_endpoint_wiring_issues(tmp_path, context_map=_context_map())

        assert any(issue.rule_id == "endpoint.no_direct_service_construction" for issue in issues)

    # FUNCTION: test_validator_allows_typed_dependency_alias_usage
    # SUMMARY: Verify endpoint modules that use typed aliases cleanly pass validation.
    @pytest.mark.unit
    def test_validator_allows_typed_dependency_alias_usage(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "api" / "endpoints" / "good.py",
            "\n".join(
                [
                    "from fastapi import APIRouter",
                    "from project.infrastructure.api.dependencies import BusinessServiceDep",
                    "",
                    "router = APIRouter()",
                    "",
                    "@router.get('/')",
                    "async def read_items(service: BusinessServiceDep) -> dict[str, str]:",
                    "    return {'status': str(service)}",
                ]
            ),
        )
        _write_registration(
            tmp_path,
            "project.infrastructure.api.endpoints.good",
            "router",
        )

        issues = collect_endpoint_wiring_issues(tmp_path, context_map=_context_map())

        assert issues == []

    # FUNCTION: test_validator_flags_router_that_is_never_included
    # SUMMARY: Verify a route handler on an unregistered router is reported instead of passing silently.
    @pytest.mark.unit
    def test_validator_flags_router_that_is_never_included(self, tmp_path: Path) -> None:
        # **LOGIC_STEP**: This is the dead-route case: the module is well-formed and uses a
        # typed alias, so every other rule is satisfied, yet the route answers 404 because
        # include_application_routers() never sees the router.
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "api" / "endpoints" / "orphan.py",
            "\n".join(
                [
                    "from fastapi import APIRouter",
                    "from project.infrastructure.api.dependencies import BusinessServiceDep",
                    "",
                    "orphan_router = APIRouter()",
                    "",
                    "@orphan_router.get('/orphan')",
                    "async def read_orphan(service: BusinessServiceDep) -> dict[str, str]:",
                    "    return {'status': str(service)}",
                ]
            ),
        )
        _write_registration(
            tmp_path,
            "project.infrastructure.api.endpoints.health",
            "health_router",
        )

        issues = collect_endpoint_wiring_issues(tmp_path, context_map=_context_map())

        assert [issue.rule_id for issue in issues] == ["endpoint.router_not_registered"]
        assert issues[0].line == 4

    # FUNCTION: test_validator_ignores_router_without_route_handlers
    # SUMMARY: Verify a declared-but-empty router is not reported, since it exposes no routes.
    @pytest.mark.unit
    def test_validator_ignores_router_without_route_handlers(self, tmp_path: Path) -> None:
        _write_fixture(
            tmp_path / "project" / "infrastructure" / "api" / "endpoints" / "empty.py",
            "\n".join(
                [
                    "from fastapi import APIRouter",
                    "",
                    "future_router = APIRouter()",
                ]
            ),
        )
        _write_registration(
            tmp_path,
            "project.infrastructure.api.endpoints.health",
            "health_router",
        )

        assert collect_endpoint_wiring_issues(tmp_path, context_map=_context_map()) == []

    # FUNCTION: test_main_json_output_includes_structured_issue_fields
    # SUMMARY: Verify JSON mode emits stable remediation metadata for endpoint-wiring issues.
    @pytest.mark.unit
    def test_main_json_output_includes_structured_issue_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        issue_path = Path.cwd() / "project" / "infrastructure" / "api" / "endpoints" / "bad.py"
        monkeypatch.setattr(
            "scripts.validate_endpoint_wiring.collect_endpoint_wiring_issues",
            lambda _repo_root: [
                EndpointWiringIssue(
                    path=issue_path,
                    line=10,
                    rule_id="endpoint.no_depends_without_alias",
                    message="Endpoint parameter uses Depends(...) without a typed alias.",
                )
            ],
        )
        monkeypatch.setattr(sys, "argv", ["scripts/validate_endpoint_wiring.py", "--json"])

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 1
        assert '"rule_id": "endpoint.no_depends_without_alias"' in captured.out
        assert '"suggested_fix"' in captured.out
