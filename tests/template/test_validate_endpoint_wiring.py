# FILE: tests/template/test_validate_endpoint_wiring.py
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
    read_wiring_facts,
)

# The wiring files as a vertical writes them, in the shapes the kernel ships: a service declared
# None and built inside a branch, a three-argument _get_service getter, an Annotated alias.
_REGISTRY = """\
from typing import Any

from project.application.business_service import BusinessService


def build_business_services(enabled: bool) -> dict[str, Any]:
    business_service: BusinessService | None = None
    if enabled:
        business_service = BusinessService()
    services: dict[str, Any] = {"business_service": business_service}
    return services
"""

_DEPENDENCIES = """\
from typing import Annotated

from fastapi import Depends, Request

from project.application.business_service import BusinessService


def get_business_service(request: Request) -> BusinessService:
    return _get_service(request, "business_service", BusinessService)


BusinessServiceDep = Annotated[BusinessService, Depends(get_business_service)]
"""

_GOOD_ENDPOINT = """\
from fastapi import APIRouter
from project.infrastructure.api.dependencies import BusinessServiceDep

router = APIRouter()

@router.get('/')
async def read_items(service: BusinessServiceDep) -> dict[str, str]:
    return {'status': str(service)}
"""


def _write_fixture(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _wire(repo_root: Path, registry: str = _REGISTRY, dependencies: str = _DEPENDENCIES) -> None:
    _write_fixture(repo_root / "project" / "core" / "service_registration.py", registry)
    _write_fixture(
        repo_root / "project" / "infrastructure" / "api" / "dependencies.py", dependencies
    )


def _write_endpoint(repo_root: Path, name: str, content: str) -> None:
    _write_fixture(repo_root / "project" / "infrastructure" / "api" / "endpoints" / name, content)


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


def _rule_ids(repo_root: Path) -> list[str]:
    return [issue.rule_id for issue in collect_endpoint_wiring_issues(repo_root)]


class TestValidateEndpointWiring:
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

    @pytest.mark.unit
    def test_validator_rejects_direct_service_import(self, tmp_path: Path) -> None:
        _wire(tmp_path)
        _write_endpoint(
            tmp_path,
            "bad.py",
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

        rule_ids = _rule_ids(tmp_path)

        assert "endpoint.no_direct_service_import" in rule_ids
        assert "endpoint.no_direct_service_annotation" in rule_ids

    # Regression guard: a syntactically broken endpoint module surfaces as a structured EndpointWiringIssue with rule_id 'endpoint.syntax_error', not a raw traceback.
    @pytest.mark.unit
    def test_validator_emits_syntax_error_issue_on_broken_endpoint(self, tmp_path: Path) -> None:
        _wire(tmp_path)
        _write_endpoint(
            tmp_path, "broken.py", "from fastapi import APIRouter\n\ndef bad(:\n    pass\n"
        )

        issues = collect_endpoint_wiring_issues(tmp_path)

        syntax_issues = [issue for issue in issues if issue.rule_id == "endpoint.syntax_error"]
        assert len(syntax_issues) == 1
        assert "SyntaxError while parsing" in syntax_issues[0].message
        assert get_endpoint_rule_playbook("endpoint.syntax_error") is not None

    @pytest.mark.unit
    def test_a_broken_wiring_file_is_an_issue_not_a_traceback(self, tmp_path: Path) -> None:
        _wire(tmp_path, dependencies="def get_business_service(:\n")

        issues = collect_endpoint_wiring_issues(tmp_path)

        assert [(issue.rule_id, issue.path.name) for issue in issues] == [
            ("endpoint.syntax_error", "dependencies.py")
        ]

    @pytest.mark.unit
    def test_validator_rejects_depends_without_alias(self, tmp_path: Path) -> None:
        _wire(tmp_path)
        _write_endpoint(
            tmp_path,
            "bad.py",
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

        assert "endpoint.no_depends_without_alias" in _rule_ids(tmp_path)

    @pytest.mark.unit
    def test_validator_rejects_direct_service_construction(self, tmp_path: Path) -> None:
        _wire(tmp_path)
        _write_endpoint(
            tmp_path,
            "bad.py",
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

        assert "endpoint.no_direct_service_construction" in _rule_ids(tmp_path)

    @pytest.mark.unit
    def test_validator_allows_typed_dependency_alias_usage(self, tmp_path: Path) -> None:
        _wire(tmp_path)
        _write_endpoint(tmp_path, "good.py", _GOOD_ENDPOINT)
        _write_registration(tmp_path, "project.infrastructure.api.endpoints.good", "router")

        assert collect_endpoint_wiring_issues(tmp_path) == []

    @pytest.mark.unit
    def test_validator_flags_router_that_is_never_included(self, tmp_path: Path) -> None:
        # This is the dead-route case: the module is well-formed and uses a
        # typed alias, so every other rule is satisfied, yet the route answers 404 because
        # include_application_routers() never sees the router.
        _wire(tmp_path)
        _write_endpoint(
            tmp_path,
            "orphan.py",
            _GOOD_ENDPOINT.replace("router", "orphan_router"),
        )
        _write_endpoint(tmp_path, "health.py", "")
        _write_registration(
            tmp_path, "project.infrastructure.api.endpoints.health", "health_router"
        )

        issues = collect_endpoint_wiring_issues(tmp_path)

        assert [issue.rule_id for issue in issues] == ["endpoint.router_not_registered"]
        assert issues[0].line == 4

    @pytest.mark.unit
    def test_validator_ignores_router_without_route_handlers(self, tmp_path: Path) -> None:
        _wire(tmp_path)
        _write_endpoint(
            tmp_path, "empty.py", "from fastapi import APIRouter\n\nfuture_router = APIRouter()\n"
        )
        _write_registration(tmp_path, "project.infrastructure.api.endpoints.empty", "future_router")

        assert collect_endpoint_wiring_issues(tmp_path) == []

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


# The links the generated context map used to check (ai_context/integrity.py) until the map went:
# each breaks the application at startup or on the first request, so each is its own rule here.
class TestWiringChain:
    @pytest.mark.unit
    def test_a_getter_returning_an_unregistered_key_is_reported_before_any_route_uses_it(
        self, tmp_path: Path
    ) -> None:
        _wire(
            tmp_path, dependencies=_DEPENDENCIES.replace('"business_service"', '"busines_service"')
        )

        issues = collect_endpoint_wiring_issues(tmp_path)

        assert [(issue.rule_id, issue.path.name, issue.line) for issue in issues] == [
            ("endpoint.wiring_chain_broken", "dependencies.py", 8)
        ]
        assert "'busines_service'" in issues[0].message

    @pytest.mark.unit
    def test_a_route_using_an_alias_with_an_unregistered_key_is_reported_at_the_route(
        self, tmp_path: Path
    ) -> None:
        _wire(tmp_path, registry=_REGISTRY.replace('"business_service"', '"other_service"'))
        _write_endpoint(tmp_path, "good.py", _GOOD_ENDPOINT)
        _write_registration(tmp_path, "project.infrastructure.api.endpoints.good", "router")

        assert _rule_ids(tmp_path) == [
            "endpoint.wiring_chain_broken",
            "endpoint.alias_chain_invalid",
        ]

    @pytest.mark.unit
    def test_an_alias_naming_a_getter_that_is_not_defined_is_reported(self, tmp_path: Path) -> None:
        _wire(
            tmp_path,
            dependencies=_DEPENDENCIES.replace(
                "Depends(get_business_service)", "Depends(get_busines_service)"
            ),
        )

        issues = collect_endpoint_wiring_issues(tmp_path)

        assert [(issue.rule_id, issue.line) for issue in issues] == [
            ("endpoint.wiring_chain_broken", 12)
        ]
        assert "'get_busines_service'" in issues[0].message

    @pytest.mark.unit
    def test_an_imported_endpoint_module_that_does_not_exist_is_reported(
        self, tmp_path: Path
    ) -> None:
        _wire(tmp_path)
        _write_registration(tmp_path, "project.infrastructure.api.endpoints.gone", "gone_router")

        issues = collect_endpoint_wiring_issues(tmp_path)

        assert [(issue.rule_id, issue.path.name, issue.line) for issue in issues] == [
            ("endpoint.router_module_missing", "router_registration.py", 2)
        ]


class TestWiringFacts:
    @pytest.mark.unit
    def test_services_are_read_from_both_registries(self, tmp_path: Path) -> None:
        _wire(tmp_path)
        _write_fixture(
            tmp_path / "project" / "core" / "composition_root.py",
            "from project.infrastructure.agents.llm_service import LLMService\n\n\n"
            "def build() -> dict[str, object]:\n"
            "    services = {'llm_service': LLMService()}\n"
            "    return services\n",
        )

        facts = read_wiring_facts(tmp_path)

        assert facts.services == {
            "llm_service": "project.infrastructure.agents.llm_service.LLMService",
            "business_service": "project.application.business_service.BusinessService",
        }
        assert facts.service_classes() == {"project.application.business_service.BusinessService"}

    # The context map's extractor walked an If as test -> body -> orelse, so `x = None` in an
    # `else` overwrote the class it had recorded, and the kernel had to forbid the shape.
    @pytest.mark.unit
    def test_a_service_built_in_one_branch_and_none_in_the_else_still_resolves(
        self, tmp_path: Path
    ) -> None:
        registry = _REGISTRY.replace(
            "    business_service: BusinessService | None = None\n", ""
        ).replace(
            "        business_service = BusinessService()\n",
            "        business_service = BusinessService()\n    else:\n        business_service = None\n",
        )
        _wire(tmp_path, registry=registry)

        facts = read_wiring_facts(tmp_path)

        assert facts.services["business_service"] == (
            "project.application.business_service.BusinessService"
        )

    @pytest.mark.unit
    def test_a_nullable_getter_resolves_through_services_get(self, tmp_path: Path) -> None:
        dependencies = _DEPENDENCIES.replace(
            "def get_business_service(request: Request) -> BusinessService:\n"
            '    return _get_service(request, "business_service", BusinessService)\n',
            "def get_business_service(request: Request) -> BusinessService | None:\n"
            "    services = request.app.state.services\n"
            '    return services.get("business_service")\n',
        )
        _wire(tmp_path, dependencies=dependencies)

        facts = read_wiring_facts(tmp_path)

        assert facts.alias_service_key("BusinessServiceDep") == "business_service"

    # Validated through the editable install, a copy of the template read the template's
    # wiring. The facts come from the directory passed in, whatever is installed.
    @pytest.mark.unit
    def test_facts_come_from_the_repository_checked_not_the_one_installed(
        self, tmp_path: Path
    ) -> None:
        assert read_wiring_facts(tmp_path).services == {}
