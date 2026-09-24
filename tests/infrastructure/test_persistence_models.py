# FILE: tests/infrastructure/test_persistence_models.py
# SUMMARY: Every foreign key in the shipped metadata declares its deletion policy.

from __future__ import annotations

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, String, Table

from project.infrastructure.persistence.orm_models import Base


# Returns `"<table>.<column>"` for each bare foreign key, sorted for a stable message.
# `alembic revision --autogenerate` copies whatever `ForeignKey(...)` the model declares and
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
        # An empty string is not a policy either, and it is worse than None:
        # SQLAlchemy accepts `ondelete=""` at model level and PostgreSQL's DDL compiler then fails
        # with `Unexpected SQL phrase: ''` at migration time, when the useful message is furthest
        # from the line that caused it. Anything blank is reported here instead.
        if foreign_key.ondelete is None or not str(foreign_key.ondelete).strip()
    )


# The reference vertical ships one table and zero relationships, so it cannot demonstrate this
# on its own — the two tests below build a throwaway MetaData to prove the checker actually catches
# a bare ForeignKey and actually accepts an explicit one, before the third test points the same
# checker at the real, shipped metadata.
class TestForeignKeysDeclareOnDelete:
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

    # Verify an empty policy is caught at model level rather than at DDL compilation.
    @pytest.mark.unit
    def test_a_foreign_key_with_a_blank_ondelete_is_reported(self) -> None:
        blank = MetaData()
        Table("parents", blank, Column("id", String, primary_key=True))
        Table(
            "children",
            blank,
            Column("id", String, primary_key=True),
            Column("parent_id", String, ForeignKey("parents.id", ondelete="  ")),
        )

        assert _foreign_keys_missing_ondelete(blank) == ["children.parent_id"]

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

    @pytest.mark.unit
    def test_no_shipped_foreign_key_is_missing_a_deletion_policy(self) -> None:
        # Passes vacuously while the shipped schema has no ForeignKey at all — the
        # reference vertical is one table with no relationships. The moment a vertical's model adds
        # one without `ondelete=`, this goes red instead of waiting for `make test-e2e` to delete a
        # parent row and hit PostgreSQL's own silent default.
        assert _foreign_keys_missing_ondelete(Base.metadata) == []
