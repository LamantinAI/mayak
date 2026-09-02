# FILE: tests/infrastructure/test_persistence_models.py
# SUMMARY: Verify the kernel placeholder ORM (ReferenceTaskORM) declares the expected schema.

from __future__ import annotations

import pytest
from typing import cast

from sqlalchemy import String, Table

from project.domain.reference_task import MAX_TITLE_LENGTH
from project.infrastructure.persistence.orm_models import Base, ReferenceTaskORM


# CLASS: tests.infrastructure.test_persistence_models.TestReferenceTaskORMSchema
# SUMMARY: Schema invariants for the kernel placeholder ORM.
class TestReferenceTaskORMSchema:
    # FUNCTION: test_reference_task_orm_table_name
    @pytest.mark.unit
    def test_reference_task_orm_table_name(self) -> None:
        assert ReferenceTaskORM.__tablename__ == "reference_tasks"

    # FUNCTION: test_reference_task_orm_has_id_primary_key
    @pytest.mark.unit
    def test_reference_task_orm_has_id_primary_key(self) -> None:
        table = cast(Table, ReferenceTaskORM.__table__)
        assert "id" in table.columns
        primary_key_columns = [column.name for column in table.primary_key.columns]
        assert primary_key_columns == ["id"]

    # FUNCTION: test_reference_task_orm_has_title_column
    @pytest.mark.unit
    def test_reference_task_orm_has_title_column(self) -> None:
        assert "title" in ReferenceTaskORM.__table__.columns

    # FUNCTION: test_title_column_is_as_long_as_the_domain_allows
    # SUMMARY: Verify the column's declared length is the domain constant, not a literal that
    # happens to equal it today.
    @pytest.mark.unit
    def test_title_column_is_as_long_as_the_domain_allows(self) -> None:
        # **LOGIC_STEP**: Read off the metadata, so `String(200)` written in place of
        # `String(MAX_TITLE_LENGTH)` is caught the day the constant moves and the column does
        # not — the exact split the constant exists to prevent. Found by a reviewer's mutation
        # on 2026-09-02: with the literal in the model and the constant raised to 300, every
        # suite stayed green.
        column_type = cast(Table, ReferenceTaskORM.__table__).columns["title"].type

        assert isinstance(column_type, String)
        assert column_type.length == MAX_TITLE_LENGTH


# CLASS: tests.infrastructure.test_persistence_models.TestORMRegistry
# SUMMARY: The reference vertical's table is registered in the SQLAlchemy metadata.
# NOTE: The exact-set ledger that used to live here is gone, deliberately. It asserted
# `set(Base.metadata.tables) == {"reference_tasks"}` — the same sentence, on the same object, as
# tests/application/test_validate_migrations.py. Two copies in two directories is why adding a
# first table went red twice on two different projects: the developer fixed the ledger the failure
# named, reran, and met the second one. The ledger now lives in exactly one place, next to the
# migration check whose contract it states. A membership check is kept here because it is a
# different claim — this table exists — and it stays green when a project adds tables of its own.
class TestORMRegistry:
    # FUNCTION: test_metadata_contains_reference_tasks
    @pytest.mark.unit
    def test_metadata_contains_reference_tasks(self) -> None:
        assert "reference_tasks" in Base.metadata.tables
