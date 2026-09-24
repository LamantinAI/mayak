# FILE: tests/application/test_reference_task_vertical.py
# SUMMARY: The reference vertical's rules decided before anything is written, and its wiring — no database.
# What needs a write — the stored row, the update token, a conflict, a filter — is proved
# against PostgreSQL in tests/db/, not here against an imitation of it. What stays here is what the
# domain's checks and the service decide on their own: bounds, closed sets, an empty or null patch.

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from sqlalchemy import String, Table

from ai_context.extraction import extract_service_registry_entries
from project.application.reference_task_service import MAX_LIST_LIMIT, ReferenceTaskService
from project.domain.exceptions import ValidationError
from project.domain.ports import ReferenceTaskRepositoryPort
from project.domain.reference_task import DEFAULT_STATUS, MAX_TITLE_LENGTH, ReferenceTask
from project.infrastructure.persistence.orm_models import ReferenceTaskORM
from tests.conftest import registered_paths

_REPO_ROOT = Path(__file__).resolve().parents[2]


# The port for rules decided before a write: one stored task to read, every write recorded.
# Not a repository — it stores nothing and filters nothing, so no test here can pass on
# storage behaviour the real one does not have. A write it receives is echoed back unchanged.
class _Port:
    def __init__(self, task: ReferenceTask | None = None) -> None:
        self.task = task
        self.writes: list[tuple[ReferenceTask, datetime | None]] = []

    async def add(self, task: ReferenceTask) -> None:
        self.writes.append((task, None))

    async def get(self, task_id: str) -> ReferenceTask | None:
        return self.task

    async def list_by_status(self, status: str, limit: int = 50) -> list[ReferenceTask]:
        return []

    async def update(self, task: ReferenceTask, expected_updated_at: datetime) -> ReferenceTask:
        self.writes.append((task, expected_updated_at))
        return task


_STORED = ReferenceTask(
    id="00000000-0000-0000-0000-000000000001",
    title="Original",
    details="Keep me",
    status=DEFAULT_STATUS,
    created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
)


# The service over a _Port holding the stored task; the annotation is checked by mypy.
def _service(port: _Port) -> ReferenceTaskService:
    checked: ReferenceTaskRepositoryPort = port
    return ReferenceTaskService(repository=checked)


@pytest.mark.unit
@pytest.mark.parametrize("title", ["", " \t\n", "x" * (MAX_TITLE_LENGTH + 1)])
async def test_create_refuses_a_blank_or_overlong_title_before_writing(title: str) -> None:
    port = _Port()

    with pytest.raises(ValidationError):
        await _service(port).create_task(title=title)

    assert port.writes == []


@pytest.mark.unit
async def test_create_accepts_a_title_of_exactly_the_maximum_length() -> None:
    port = _Port()

    task = await _service(port).create_task(title="x" * MAX_TITLE_LENGTH)

    assert port.writes == [(task, None)]
    assert task.status == DEFAULT_STATUS and task.created_at == task.updated_at


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "limit"),
    [("archived", 50), (DEFAULT_STATUS, 0), (DEFAULT_STATUS, MAX_LIST_LIMIT + 1)],
)
async def test_list_refuses_an_unknown_status_or_a_page_out_of_bounds(
    status: str, limit: int
) -> None:
    with pytest.raises(ValidationError):
        await _service(_Port()).list_tasks(status=status, limit=limit)


@pytest.mark.unit
@pytest.mark.parametrize(
    "patch",
    [
        {},
        {"title": None},
        {"status": None},
        {"status": "archived"},
        {"title": "x" * (MAX_TITLE_LENGTH + 1)},
    ],
)
async def test_update_refuses_an_empty_null_or_out_of_bounds_patch_before_writing(
    patch: dict[str, Any],
) -> None:
    port = _Port(_STORED)

    with pytest.raises(ValidationError):
        await _service(port).update_task(_STORED.id, **patch)

    assert port.writes == []


# The last two lines are the lost-update guard as the service sees it: passing the new
# timestamp as the condition, or not moving it, still stores the row and returns it. tests/db shows
# what either does to a second writer; this names the argument that decides it.
@pytest.mark.unit
async def test_update_writes_the_patch_conditioned_on_the_timestamp_it_read() -> None:
    port = _Port(_STORED)

    cleared = await _service(port).update_task(_STORED.id, details=None)
    kept = await _service(port).update_task(_STORED.id, title="x" * MAX_TITLE_LENGTH)

    assert cleared.details is None and cleared.title == "Original" and kept.details == "Keep me"
    assert [condition for _, condition in port.writes] == [_STORED.updated_at] * 2
    assert all(written.updated_at > _STORED.updated_at for written, _ in port.writes)


# 200 as a literal, once: every other test builds its strings from the constant. The column is read
# off the metadata, so `String(200)` written in place of `String(MAX_TITLE_LENGTH)` is caught the day
# the constant moves and the column does not; `alembic check` in tests/db holds the migration to it.
@pytest.mark.unit
def test_the_title_bound_is_the_width_of_the_column() -> None:
    column = cast(Table, ReferenceTaskORM.__table__).columns["title"].type

    assert MAX_TITLE_LENGTH == 200
    assert isinstance(column, String) and column.length == MAX_TITLE_LENGTH


class TestWiring:
    @pytest.mark.unit
    def test_service_resolves_to_its_class_in_the_static_registry(self) -> None:
        # ast.walk visits an If node as test -> body -> orelse, so an assignment
        # moved into an `else` makes the extractor record class=null and the context map lie.
        registration = _REPO_ROOT / "project" / "core" / "service_registration.py"
        entries = extract_service_registry_entries(registration, _REPO_ROOT, "vertical")

        assert entries["reference_task_service"]["class"] == "ReferenceTaskService"
        assert entries["reference_task_service"]["resolution_status"] == "resolved"
        tree = ast.parse(registration.read_text(encoding="utf-8"))
        assert not [node for node in ast.walk(tree) if isinstance(node, ast.If) and node.orelse]

    @pytest.mark.unit
    def test_routes_exist_only_when_the_store_is_enabled(
        self, fastapi_app: FastAPI, app_without_postgres: FastAPI
    ) -> None:
        assert {"/reference-tasks", "/reference-tasks/{task_id}"} <= registered_paths(fastapi_app)
        assert not any(
            p.startswith("/reference-tasks") for p in registered_paths(app_without_postgres)
        )
        assert app_without_postgres.state.services["reference_task_service"] is None
