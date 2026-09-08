# FILE: tests/infrastructure/test_persistence_models.py
# SUMMARY: Verify the kernel placeholder ORM (ReferenceTaskORM) declares the expected schema.

from __future__ import annotations

import pytest
from typing import cast

from sqlalchemy import Column, ForeignKey, MetaData, String, Table

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


# FUNCTION: _foreign_keys_missing_ondelete
# SUMMARY: Every column in the metadata that carries a ForeignKey with no explicit `ondelete`.
# OUTPUT: (list[str]): `"<table>.<column>"` for each bare foreign key, sorted for a stable message.
# NOTE: `alembic revision --autogenerate` copies whatever `ForeignKey(...)` the model declares and
# chooses nothing on its own — a bare one autogenerates without complaint, and PostgreSQL's own
# default (behaviourally RESTRICT) only surfaces the day something deletes a parent with a child
# still attached, as an IntegrityError the client sees as a 500 instead of the domain's own 409 or
# 403. See docs/adr/ADR-007-autocommit-and-explicit-transactions.md, "A foreign key's deletion
# policy is a domain decision, not a schema detail".
def _foreign_keys_missing_ondelete(metadata: MetaData) -> list[str]:
    return sorted(
        f"{table.name}.{column.name}"
        for table in metadata.tables.values()
        for column in table.columns
        for foreign_key in column.foreign_keys
        if foreign_key.ondelete is None
    )


# CLASS: tests.infrastructure.test_persistence_models.TestForeignKeysDeclareOnDelete
# SUMMARY: Guard the deletion policy of every foreign key the kernel's ORM metadata declares.
# NOTE: The reference vertical ships one table and zero relationships, so it cannot demonstrate this
# on its own — the two tests below build a throwaway MetaData to prove the checker actually catches
# a bare ForeignKey and actually accepts an explicit one, before the third test points the same
# checker at the real, shipped metadata.
class TestForeignKeysDeclareOnDelete:
    # FUNCTION: test_a_foreign_key_without_ondelete_is_reported
    @pytest.mark.unit
    def test_a_foreign_key_without_ondelete_is_reported(self) -> None:
        bare = MetaData()
        Table("parents", bare, Column("id", String, primary_key=True))
        Table(
            "children",
            bare,
            Column("id", String, primary_key=True),
            Column("parent_id", String, ForeignKey("parents.id")),
        )

        assert _foreign_keys_missing_ondelete(bare) == ["children.parent_id"]

    # FUNCTION: test_a_foreign_key_with_ondelete_is_not_reported
    @pytest.mark.unit
    def test_a_foreign_key_with_ondelete_is_not_reported(self) -> None:
        explicit = MetaData()
        Table("parents", explicit, Column("id", String, primary_key=True))
        Table(
            "children",
            explicit,
            Column("id", String, primary_key=True),
            Column("parent_id", String, ForeignKey("parents.id", ondelete="CASCADE")),
        )

        assert _foreign_keys_missing_ondelete(explicit) == []

    # FUNCTION: test_no_shipped_foreign_key_is_missing_a_deletion_policy
    # SUMMARY: The live guard — vacuous today, and the point of it, per the NOTE above.
    @pytest.mark.unit
    def test_no_shipped_foreign_key_is_missing_a_deletion_policy(self) -> None:
        # **LOGIC_STEP**: Passes vacuously while the shipped schema has no ForeignKey at all — the
        # reference vertical is one table with no relationships. The moment a vertical's model adds
        # one without `ondelete=`, this goes red instead of waiting for `make test-e2e` to delete a
        # parent row and hit PostgreSQL's own silent default.
        assert _foreign_keys_missing_ondelete(Base.metadata) == []
