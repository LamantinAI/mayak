# FILE: tests/application/test_reference_task_vertical.py
# SUMMARY: Unit coverage for the reference vertical — service rules, static wiring, and the HTTP
# surface. The database is faked here on purpose; tests/functional/src/test_reference_tasks_api.py
# is what proves the same path against the real driver.

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from ai_context.extraction import extract_service_registry_entries
from project.application.reference_task_service import MAX_LIST_LIMIT, ReferenceTaskService
from project.domain.exceptions import NotFoundError, ValidationError
from project.domain.ports import ReferenceTaskRepositoryPort
from project.domain.reference_task import (
    ALLOWED_STATUSES,
    DEFAULT_STATUS,
    MAX_TITLE_LENGTH,
    ReferenceTask,
)
from tests.conftest import registered_paths

_REPO_ROOT = Path(__file__).resolve().parents[2]


# CLASS: tests.application.test_reference_task_vertical.InMemoryReferenceTaskRepository
# SUMMARY: Test double implementing ReferenceTaskRepositoryPort without a database.
class InMemoryReferenceTaskRepository:
    # FUNCTION: tests/application/test_reference_task_vertical/InMemoryReferenceTaskRepository/__init__
    # SUMMARY: Start with an empty store.
    def __init__(self) -> None:
        self.items: dict[str, ReferenceTask] = {}

    # FUNCTION: tests/application/test_reference_task_vertical/InMemoryReferenceTaskRepository/add
    # SUMMARY: Store one task.
    async def add(self, task: ReferenceTask) -> None:
        self.items[task.id] = task

    # FUNCTION: tests/application/test_reference_task_vertical/InMemoryReferenceTaskRepository/get
    # SUMMARY: Load one task by identifier.
    async def get(self, task_id: str) -> ReferenceTask | None:
        return self.items.get(task_id)

    # FUNCTION: tests/application/test_reference_task_vertical/InMemoryReferenceTaskRepository/list_by_status
    # SUMMARY: List tasks in one status, newest first.
    async def list_by_status(self, status: str, limit: int = 50) -> list[ReferenceTask]:
        matching = [task for task in self.items.values() if task.status == status]
        matching.sort(key=lambda task: task.created_at, reverse=True)
        return matching[:limit]


# FUNCTION: repository
# SUMMARY: Provide the in-memory repository, type-checked against the port it stands in for.
# OUTPUT: (InMemoryReferenceTaskRepository): Empty test double.
@pytest.fixture
def repository() -> InMemoryReferenceTaskRepository:
    # **LOGIC_STEP**: The annotation is the assertion. If the double drifts from the Protocol,
    # mypy fails in the gate rather than the tests passing against a shape nothing implements.
    double = InMemoryReferenceTaskRepository()
    _port: ReferenceTaskRepositoryPort = double
    return double


# FUNCTION: service
# SUMMARY: Provide the application service over the in-memory repository.
# OUTPUT: (ReferenceTaskService): Service under test.
@pytest.fixture
def service(repository: InMemoryReferenceTaskRepository) -> ReferenceTaskService:
    return ReferenceTaskService(repository=repository)


# FUNCTION: client
# SUMMARY: Provide a TestClient whose registry holds a database-free reference service.
# OUTPUT: (Generator[TestClient, None, None]): Client bound to the assembled application.
@pytest.fixture
def client(
    fastapi_app: FastAPI,
    service: ReferenceTaskService,
) -> Generator[TestClient, None, None]:
    # **LOGIC_STEP**: The routes were registered because the assembled registry held a real
    # service; swapping the instance afterwards keeps the routing table intact and takes the
    # pool out of the request path. The getter reads app.state.services per request, so the
    # substitution is visible without touching dependency_overrides.
    fastapi_app.state.services["reference_task_service"] = service
    with TestClient(fastapi_app) as test_client:
        yield test_client


# CLASS: tests.application.test_reference_task_vertical.TestServiceRules
# SUMMARY: Verify the business rules the service owns, independent of transport and storage.
class TestServiceRules:
    # FUNCTION: test_create_assigns_identity_status_and_timestamp
    # SUMMARY: Verify the service, not the caller and not the database, decides identity and time.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_assigns_identity_status_and_timestamp(
        self,
        service: ReferenceTaskService,
        repository: InMemoryReferenceTaskRepository,
    ) -> None:
        before = datetime.now(timezone.utc)

        task = await service.create_task(title="Write the plan", details="Both directions")

        assert task.id
        assert task.status == DEFAULT_STATUS
        assert task.created_at >= before
        assert task.created_at.tzinfo is not None
        assert repository.items[task.id] == task

    # FUNCTION: test_get_raises_not_found_for_unknown_identifier
    # SUMMARY: Verify absence is a domain exception, which exception_handlers maps to 404.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_raises_not_found_for_unknown_identifier(
        self,
        service: ReferenceTaskService,
    ) -> None:
        with pytest.raises(NotFoundError, match="does not exist"):
            await service.get_task(str(uuid4()))

    # FUNCTION: test_list_rejects_status_outside_the_domain_set
    # SUMMARY: Verify the closed status set is enforced in the service, not only in the query model.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_rejects_status_outside_the_domain_set(
        self,
        service: ReferenceTaskService,
    ) -> None:
        with pytest.raises(ValidationError, match="Unknown status"):
            await service.list_tasks(status="archived")

    # FUNCTION: test_list_rejects_limit_outside_the_allowed_range
    # SUMMARY: Verify a caller bypassing FastAPI cannot request an unbounded page.
    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit", [0, -1, MAX_LIST_LIMIT + 1])
    async def test_list_rejects_limit_outside_the_allowed_range(
        self,
        service: ReferenceTaskService,
        limit: int,
    ) -> None:
        with pytest.raises(ValidationError, match="limit must be between"):
            await service.list_tasks(status=DEFAULT_STATUS, limit=limit)

    # FUNCTION: test_create_rejects_a_title_longer_than_the_column
    # SUMMARY: Verify the length bound is enforced in the service, not only in the request DTO.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_rejects_a_title_longer_than_the_column(
        self,
        service: ReferenceTaskService,
        repository: InMemoryReferenceTaskRepository,
    ) -> None:
        # **LOGIC_STEP**: This is the caller the DTO cannot protect — a background job or another
        # service holding the same ReferenceTaskService. Before the guard the oversized title
        # reached varchar(200) and the driver error surfaced to the client as a 500.
        with pytest.raises(ValidationError, match="at most"):
            await service.create_task(title="x" * (MAX_TITLE_LENGTH + 1))

        assert repository.items == {}

    # FUNCTION: test_create_accepts_a_title_of_exactly_the_maximum_length
    # SUMMARY: Verify the bound is inclusive, so the guard does not reject a legal title.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_accepts_a_title_of_exactly_the_maximum_length(
        self,
        service: ReferenceTaskService,
    ) -> None:
        task = await service.create_task(title="x" * MAX_TITLE_LENGTH)

        assert len(task.title) == MAX_TITLE_LENGTH

    # FUNCTION: test_create_rejects_a_blank_title
    # SUMMARY: Verify whitespace alone is not a title for a caller that never meets the DTO.
    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.parametrize("title", ["", "   ", "\t\n"])
    async def test_create_rejects_a_blank_title(
        self,
        service: ReferenceTaskService,
        title: str,
    ) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            await service.create_task(title=title)

    # FUNCTION: test_list_returns_matching_tasks_newest_first
    # SUMMARY: Verify the service passes the filter through and preserves repository ordering.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_returns_matching_tasks_newest_first(
        self,
        service: ReferenceTaskService,
        repository: InMemoryReferenceTaskRepository,
    ) -> None:
        now = datetime.now(timezone.utc)
        older = ReferenceTask(
            id=str(uuid4()),
            title="Older",
            details=None,
            status=DEFAULT_STATUS,
            created_at=now - timedelta(minutes=5),
        )
        newer = ReferenceTask(
            id=str(uuid4()),
            title="Newer",
            details=None,
            status=DEFAULT_STATUS,
            created_at=now,
        )
        other = ReferenceTask(
            id=str(uuid4()),
            title="Other status",
            details=None,
            status="done",
            created_at=now,
        )
        for task in (older, newer, other):
            await repository.add(task)

        listed = await service.list_tasks(status=DEFAULT_STATUS)

        assert [item.id for item in listed] == [newer.id, older.id]

    # FUNCTION: test_default_status_belongs_to_the_allowed_set
    # SUMMARY: Verify the starting status is one the list query can actually filter by.
    @pytest.mark.unit
    def test_default_status_belongs_to_the_allowed_set(self) -> None:
        assert DEFAULT_STATUS in ALLOWED_STATUSES


# CLASS: tests.application.test_reference_task_vertical.TestHttpSurface
# SUMMARY: Verify the routes, their status codes, and the shared error envelope.
class TestHttpSurface:
    # FUNCTION: test_create_returns_201_and_the_stored_task
    # SUMMARY: Verify the create route answers 201 with the generated identity.
    @pytest.mark.unit
    def test_create_returns_201_and_the_stored_task(self, client: TestClient) -> None:
        response = client.post("/reference-tasks", json={"title": "Draft the plan"})

        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "Draft the plan"
        assert body["status"] == DEFAULT_STATUS
        assert body["id"]

    # FUNCTION: test_created_task_is_readable_back
    # SUMMARY: Verify create and read agree on the identifier — the actual round trip.
    @pytest.mark.unit
    def test_created_task_is_readable_back(self, client: TestClient) -> None:
        created = client.post("/reference-tasks", json={"title": "Round trip"}).json()

        response = client.get(f"/reference-tasks/{created['id']}")

        assert response.status_code == 200
        assert response.json()["id"] == created["id"]

    # FUNCTION: test_unknown_identifier_returns_404_in_the_shared_envelope
    # SUMMARY: Verify NotFoundError reaches the client as the kernel's error shape, not a bespoke one.
    @pytest.mark.unit
    def test_unknown_identifier_returns_404_in_the_shared_envelope(
        self,
        client: TestClient,
    ) -> None:
        response = client.get(f"/reference-tasks/{uuid4()}")

        assert response.status_code == 404
        assert response.json()["error"]["status_code"] == 404

    # FUNCTION: test_unknown_status_returns_422_from_the_service_rule
    # SUMMARY: Verify ValidationError maps to 422 without the endpoint catching anything.
    @pytest.mark.unit
    def test_unknown_status_returns_422_from_the_service_rule(self, client: TestClient) -> None:
        response = client.get("/reference-tasks", params={"status": "archived"})

        assert response.status_code == 422
        assert "Unknown status" in response.json()["error"]["message"]

    # FUNCTION: test_blank_title_is_rejected_before_the_service_runs
    # SUMMARY: Verify the DTO bound produces 422 at the boundary.
    @pytest.mark.unit
    def test_blank_title_is_rejected_before_the_service_runs(
        self,
        client: TestClient,
        repository: InMemoryReferenceTaskRepository,
    ) -> None:
        response = client.post("/reference-tasks", json={"title": ""})

        assert response.status_code == 422
        assert repository.items == {}

    # FUNCTION: test_list_returns_an_envelope_with_a_count
    # SUMMARY: Verify the list route answers with items plus count rather than a bare array.
    @pytest.mark.unit
    def test_list_returns_an_envelope_with_a_count(self, client: TestClient) -> None:
        client.post("/reference-tasks", json={"title": "First"})
        client.post("/reference-tasks", json={"title": "Second"})

        response = client.get("/reference-tasks", params={"status": DEFAULT_STATUS})

        assert response.status_code == 200
        assert response.json()["count"] == 2
        assert len(response.json()["items"]) == 2

    # FUNCTION: test_limit_above_the_query_bound_is_rejected
    # SUMMARY: Verify the declared query bound answers 422 before the handler body runs.
    @pytest.mark.unit
    def test_limit_above_the_query_bound_is_rejected(self, client: TestClient) -> None:
        response = client.get("/reference-tasks", params={"limit": MAX_LIST_LIMIT + 1})

        assert response.status_code == 422


# CLASS: tests.application.test_reference_task_vertical.TestWiring
# SUMMARY: Verify the vertical stays visible to static analysis and honours POSTGRES_ENABLED.
class TestWiring:
    # FUNCTION: test_service_resolves_to_its_class_in_the_static_registry
    # SUMMARY: Verify the generated context map names the service instead of degrading to null.
    @pytest.mark.unit
    def test_service_resolves_to_its_class_in_the_static_registry(self) -> None:
        # **LOGIC_STEP**: Same trap as db_pool in composition_root.py. ast.walk visits an If node
        # as test -> body -> orelse, so moving the assignment into an `else` makes the extractor
        # record class=null / confidence=low and docs/ai_context_map.json starts lying about the
        # vertical. Measured there, guarded here.
        entries = extract_service_registry_entries(
            _REPO_ROOT / "project" / "core" / "service_registration.py",
            _REPO_ROOT,
            "vertical",
        )

        assert entries["reference_task_service"]["class"] == "ReferenceTaskService"
        assert entries["reference_task_service"]["confidence"] == "high"
        assert entries["reference_task_service"]["resolution_status"] == "resolved"

    # FUNCTION: test_service_registration_has_no_else_branch
    # SUMMARY: Verify nobody reintroduces the branch shape that breaks extraction.
    @pytest.mark.unit
    def test_service_registration_has_no_else_branch(self) -> None:
        # **LOGIC_STEP**: Parsed, not grepped. A substring search for "else:" also matches the
        # explanatory comment that tells the next author why the branch is forbidden, so the
        # first version of this test failed on the very prose that documents it.
        source = (_REPO_ROOT / "project" / "core" / "service_registration.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)

        branches_with_else = [
            node for node in ast.walk(tree) if isinstance(node, ast.If) and node.orelse
        ]

        assert branches_with_else == []

    # FUNCTION: test_routes_are_registered_when_the_store_is_enabled
    # SUMMARY: Verify the vertical is reachable in the default configuration.
    @pytest.mark.unit
    def test_routes_are_registered_when_the_store_is_enabled(self, fastapi_app: FastAPI) -> None:
        paths = registered_paths(fastapi_app)

        assert "/reference-tasks" in paths
        assert "/reference-tasks/{task_id}" in paths

    # FUNCTION: test_routes_are_absent_when_the_store_is_disabled
    # SUMMARY: Verify POSTGRES_ENABLED=false leaves no route that cannot work.
    @pytest.mark.unit
    def test_routes_are_absent_when_the_store_is_disabled(
        self,
        app_without_postgres: FastAPI,
    ) -> None:
        # **LOGIC_STEP**: Asserting on the routing table rather than on a 404 response, because a
        # 404 is also what an unknown identifier returns — the weaker assertion would keep passing
        # if the routes came back and the service were broken instead.
        paths = registered_paths(app_without_postgres)

        assert not any(path.startswith("/reference-tasks") for path in paths)

    # FUNCTION: test_service_is_none_when_the_store_is_disabled
    # SUMMARY: Verify the registry keeps the key so the alias chain still resolves.
    @pytest.mark.unit
    def test_service_is_none_when_the_store_is_disabled(
        self,
        app_without_postgres: FastAPI,
    ) -> None:
        services = app_without_postgres.state.services

        assert services["reference_task_service"] is None
        assert "reference_task_service" in services
