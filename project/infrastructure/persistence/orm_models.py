# FILE: project/infrastructure/persistence/orm_models.py
# SUMMARY: Defines SQLAlchemy ORM models for database interaction.

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from project.domain.reference_task import CLOSED_STATUS, MAX_TITLE_LENGTH


class Base(DeclarativeBase):
    pass


# Alembic reads this metadata; nothing reads the ORM at runtime, because ReferenceTaskRepository speaks raw psycopg. Verticals add their own tables alongside it and delete this one with the rest of the example.
class ReferenceTaskORM(Base):
    __tablename__ = "reference_tasks"
    __table_args__ = (
        Index(
            "ix_reference_tasks_status_created_at",
            "status",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)

    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)

    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # The repository's update matches on it.
    # No `onupdate=func.now()` here on purpose. Nothing reads this ORM at runtime — the
    # repository speaks raw psycopg — so a SQLAlchemy-side default would fire for nobody, while
    # making it look as though the column maintained itself. The value is set by the application
    # service and sent as a parameter, which is also what lets the update use it as an optimistic
    # token: a database that stamped its own time would leave the caller with no value to match on.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


# The rule spans rows, so the single-row updated_at token cannot hold it, and neither can a
# check the service runs before writing — two requests both read "free" and both write. The
# database holds it, in the statement that writes, for every writer at once: ADR-007, "Where the
# single-row token does not reach". Declared here so `alembic check` compares it with migration
# b5e2c1a9d4f0, which creates it; the repository turns its violation into ConflictError by name.
OPEN_TITLE_INDEX = Index(
    "uq_reference_tasks_open_title",
    func.lower(ReferenceTaskORM.title),
    unique=True,
    postgresql_where=ReferenceTaskORM.status != CLOSED_STATUS,
)
